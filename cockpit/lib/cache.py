"""Cockpit's two caches: per-PR JSON snapshots and the flat-file render cache.

Two cache directories, both owned by this module:

1. `$COCKPIT_HOME/cache/{repo}__pr-{N}.json` (referenced as `CACHE_DIR`).
   Rich JSON per PR. Written each reconcile cycle by `write_pr_cache`,
   read by the `cockpit watch` table (including its close actions).

2. `$TMPDIR/cockpit-cache/{stem}[-<sid>|-<branch>]` (referenced as
   `FLAT_CACHE_DIR`). Flat one-string-per-file payloads consumed by
   `cockpit/starship.py`'s field printers under starship. Written by:
   - `lib.claude.stash_from_stdin` (session-scoped: context, rate-limit,
     transcript-path)
   - `write_branch_pr_cache` (`cockpit.py` daemon tick, from the PR data
     the daemon fetched — single source of truth for PR-derived fields)
   - `refresh_pr_data` / `refresh_pr_checks` (the synchronous `warm`
     prewarm: `cockpit/starship.py warm` → `warm_all`). Both re-derive
     the flat-cache values from the daemon's per-PR JSON snapshot, so the
     footer and cmux sidebar share one source.

Flat layout exists because starship spawns 8 independent subprocesses per
render and each one needs to read one cache cell in sub-millisecond time;
parsing JSON in every subprocess is too expensive.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .config import CACHE_DIR, ensure_state_dirs
from .pills import ci_glyph as _ci_glyph
from .pills import decide_pills

if TYPE_CHECKING:
    from .gh import PR
    from .git import Worktree
    from .nudges import NudgePref


def muted_payload(pref: NudgePref | None) -> str:
    """Serialize a NudgePref into the `pr-muted` flat-cell contract.

    Returns "muted" when the PR is muted, else "". The same string is embedded
    as JSON `muted` so renderer-spawned refreshers can copy it straight through.
    """
    return "muted" if (pref is not None and pref.muted) else ""


# ── JSON per-PR cache (cockpit's primary state) ────────────────────────────


def _repo_slug(repo_name: str) -> str:
    """Filesystem-safe prefix for a repo's per-PR cache files (`owner/name`)."""
    return repo_name.replace("/", "_")


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write `payload` as indented JSON to `path` via a PID-suffixed tmp + rename.

    The PID suffix keeps concurrent writers (daemon + a renderer-spawned
    refresher) from racing on the same tmp name.
    """
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def write_pr_cache(
    repo_name: str,
    pr: PR,
    wt: Worktree | None = None,
    pref: NudgePref | None = None,
    linear: dict | None = None,
    *,
    reused_branch: bool = False,
    other_author: str = "",
) -> dict:
    """Write a JSON snapshot of `pr` to the cache dir and return the payload.

    `wt` is the local worktree backing `pr.branch`, if any. Used to bake
    worktree-dependent pill decisions (rebase/merge/wip) into the cached
    `pills` array so both cmux and footer read the same source of truth.

    `pref` is the daemon-resolved nudge mute state. Baked in as `muted` so
    `refresh_pr_data` (the `warm` prewarm) can republish the same snapshot
    into the `pr-muted` flat cell without re-reading `nudges`.

    `linear` is the resolved Linear-delivery block — `{"tickets": [{"id",
    "state"}], "fetched_at": ts}` — for the tickets this PR delivers (from its
    `Linear:` footer). Network-fetched like the PR itself, so it is cached here
    rather than recomputed every render. The daemon (cycle.py) decides when to
    refetch vs. carry forward; this writer just persists what it's handed.

    `reused_branch` records the daemon's reused-branch decision (a merged/closed
    PR whose head the worktree's HEAD has advanced past — see
    `cycle._is_reused_branch_merge`). It is the one place that signal is
    computed (the slow tick holds the worktree); every read path
    (`find_pr_payload` consumers, `republish_pr_caches_from_disk`,
    `refresh_pr_data`/`refresh_pr_checks`) trusts the persisted boolean rather
    than re-running `git`, so the fast tick and renderer paths stay
    `git`-free. `headRefOid` is stored alongside for debuggability.

    `other_author` holds the PR author's login *only* when the PR was authored
    by someone other than the daemon's user (the coworker / review-PR case);
    it is empty for self-authored PRs. The daemon makes that comparison once
    (it is the only place `self_user` is known) and bakes the result here so
    every flat-cell republish path (`republish_pr_caches_from_disk`,
    `refresh_pr_data`) can populate the `pr-author` cell without re-resolving
    `self_user`.
    """
    ensure_state_dirs()
    path = CACHE_DIR / f"{_repo_slug(repo_name)}__pr-{pr.number}.json"
    payload = {
        "number": pr.number,
        "title": pr.title,
        "branch": pr.branch,
        "state": pr.state,
        "isDraft": pr.is_draft,
        "ci": pr.ci,
        "review": pr.review_decision,
        "url": pr.url,
        "updatedAt": pr.updated_at,
        "unaddressed": pr.unaddressed,
        "total": pr.total_from_others,
        "mergeable": pr.mergeable,
        "muted": muted_payload(pref),
        "pills": decide_pills(pr, wt, pref),
        "headRefOid": pr.head_oid,
        "reusedBranch": reused_branch,
        "author": other_author,
        # The actionable issue category that warrants a nudge ("" when none) —
        # `PR.nudge_issue`, persisted so every flat-cell republish path
        # (`republish_pr_caches_from_disk`, `refresh_pr_data`) can populate the
        # `pr-nudge` cell that drives the TUI 🔔 without recomputing the model's
        # issue logic (the daemon-is-sole-decider invariant).
        "nudge": pr.nudge_issue,
    }
    if linear is not None:
        payload["linear"] = linear
    _atomic_write_json(path, payload)
    return payload


def _iter_cache(pattern: str):
    """Yield (path, payload) for each readable JSON cache file matching pattern."""
    if not CACHE_DIR.is_dir():
        return
    for path in CACHE_DIR.glob(pattern):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        yield path, payload


def _pr_payload_rank(payload: dict) -> tuple[int, str, int]:
    """Sort key for choosing among PR snapshots that share a branch; higher
    wins. Prefer OPEN (incl. draft — draft is state=OPEN) over MERGED/CLOSED,
    then newer `updatedAt` (ISO-8601 sorts lexically), then higher number.

    `_iter_cache` walks `Path.glob`, whose order is undefined. A branch reused
    across PRs (an old PR merged, then a new PR opened from the same head)
    leaves two `{repo}__pr-{N}.json` files carrying the same `branch`; without
    a deterministic key the flat cells — keyed by branch only — would resolve
    to whichever snapshot the filesystem happened to yield first.
    """
    is_open = 1 if str(payload.get("state") or "").upper() == "OPEN" else 0
    updated = str(payload.get("updatedAt") or "")
    try:
        number = int(payload.get("number") or 0)
    except (TypeError, ValueError):
        number = 0
    return (is_open, updated, number)


def find_pr_payload(branch: str, repo_name: str | None = None) -> dict | None:
    """Return the cached PR snapshot whose payload matches `branch`, or None.

    If `repo_name` is given, restrict the search to that repo's cache files
    (prefix-glob). Otherwise scan every cache file. When several snapshots
    share `branch` (reused branch, old PR's JSON still cached), the
    highest-ranked one wins — see `_pr_payload_rank`.
    """
    pattern = f"{_repo_slug(repo_name)}__pr-*.json" if repo_name else "*.json"
    best: dict | None = None
    for _, payload in _iter_cache(pattern):
        if payload.get("branch") != branch:
            continue
        if best is None or _pr_payload_rank(payload) > _pr_payload_rank(best):
            best = payload
    return best


def load_pr_payloads_by_branch(repo_name: str) -> dict[str, dict]:
    """One disk pass → `{branch: best_payload}` for all of `repo_name`'s PRs.

    Same selection as `find_pr_payload` (rank dedup via `_pr_payload_rank` when
    a branch is reused across PRs), but built once so a caller resolving many
    branches in a cycle avoids re-globbing + re-parsing every cache file per
    branch (the per-call cost is O(P); calling it per-PR is O(P^2)).
    """
    best: dict[str, dict] = {}
    for _, payload in _iter_cache(f"{_repo_slug(repo_name)}__pr-*.json"):
        branch = payload.get("branch")
        if not branch:
            continue
        cur = best.get(branch)
        if cur is None or _pr_payload_rank(payload) > _pr_payload_rank(cur):
            best[branch] = payload
    return best


def find_pr_payload_by_number(pr_num: str, repo_name: str | None = None) -> dict | None:
    """Return the cached PR snapshot whose `number` matches `pr_num`, or None."""
    pattern = (
        f"{_repo_slug(repo_name)}__pr-{pr_num}.json"
        if repo_name
        else f"*__pr-{pr_num}.json"
    )
    for _, payload in _iter_cache(pattern):
        data: dict = payload
        if str(data.get("number")) == str(pr_num):
            return data
    return None


def delete_pr_caches_for_branch(repo_name: str, branch: str) -> None:
    """Remove cached PR snapshots for `repo_name` whose payload `branch` matches."""
    prefix = _repo_slug(repo_name)
    for path, data in _iter_cache(f"{prefix}__pr-*.json"):
        if data.get("branch") == branch:
            path.unlink(missing_ok=True)


def prune_superseded_pr_caches(repo_name: str) -> list[Path]:
    """Unlink per-PR JSON snapshots that lost to a higher-ranked snapshot on
    the same branch, returning the paths removed.

    A reused branch (old PR merged, new PR opened from the same head) leaves
    two `{repo}__pr-{N}.json` files carrying the same `branch`. The read paths
    (`find_pr_payload`, `republish_pr_caches_from_disk`) already pick the
    winner deterministically (`_pr_payload_rank`), but the loser lingers until
    the worktree tears down — and teardown only fires when the worktree is
    closed, which never happens while the branch is still in use. Dropping the
    loser here removes the collision at the source.

    Daemon-only writer (slow tick, after the authoritative PR fetch has
    rewritten current snapshots). Keyed by `repo_name` so one repo's cycle
    never touches another's snapshots.
    """
    prefix = _repo_slug(repo_name)
    by_branch: dict[str, list[tuple[Path, dict]]] = {}
    for path, payload in _iter_cache(f"{prefix}__pr-*.json"):
        branch = payload.get("branch")
        if not branch:
            continue
        by_branch.setdefault(branch, []).append((path, payload))
    pruned: list[Path] = []
    for entries in by_branch.values():
        if len(entries) < 2:
            continue
        winner, _ = max(entries, key=lambda e: _pr_payload_rank(e[1]))
        for path, _ in entries:
            if path != winner:
                path.unlink(missing_ok=True)
                pruned.append(path)
    return pruned


# ── flat-file render cache (read by starship field printers) ───────────────


FLAT_CACHE_DIR = Path(tempfile.gettempdir()) / "cockpit-cache"


def _ensure_flat_cache_dir() -> Path:
    FLAT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return FLAT_CACHE_DIR


def atomic_write(path: Path, payload: str) -> None:
    """Write `payload` to `path` via a PID-suffixed tmp + rename.

    The PID suffix keeps concurrent writers (daemon + a renderer-spawned
    refresher) from racing on the same tmp name — mirrors `_atomic_write_json`.
    """
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(payload)
    os.replace(tmp, path)


def read_text(path: Path) -> str:
    """Best-effort read; returns empty string on any IO error."""
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def session_cache(stem: str, sid: str | None) -> Path:
    suffix = f"-{sid}" if sid else ""
    return _ensure_flat_cache_dir() / f"{stem}{suffix}"


def branch_cache(stem: str, branch: str) -> Path:
    return _ensure_flat_cache_dir() / f"{stem}-{branch.replace('/', '-')}"


def _cwd_key(cwd: os.PathLike[str] | str) -> str:
    """Filesystem-safe slug for an absolute cwd: `/` → `-`, leading dash stripped."""
    return str(Path(cwd).resolve()).replace("/", "-").lstrip("-")


def cwd_cache(stem: str, cwd: os.PathLike[str] | str) -> Path:
    """Per-cwd flat-cache cell (mirrors `branch_cache` but keyed by path slug).

    Git-state cells are keyed by cwd rather than branch because the branch
    name is itself one of the cached values — readers don't know the branch
    until they've read the cache, so the key must be derivable from cwd alone.
    """
    return _ensure_flat_cache_dir() / f"{stem}-{_cwd_key(cwd)}"


def _resolve_state(state: str, is_draft: bool, review: str) -> str:
    if state == "OPEN":
        if is_draft:
            return "DRAFT"
        if review:
            return review
    return state


def _write_pr_flat_cells(
    branch: str,
    *,
    state: str,
    number: int | None,
    title: str,
    muted: str,
    comments: int,
    total: int = 0,
    author: str = "",
    nudge: str = "",
) -> None:
    """Write the eight branch-keyed PR flat cells that every PR writer shares.

    `state` is already resolved (see `_resolve_state`). The `pr-checks` cell is
    deliberately NOT written here — its three writers disagree on purpose
    (slow tick only when non-empty, fast-tick republish always, the `warm`
    prewarm via `refresh_pr_checks`), so each handles it itself.

    `comments` is the unaddressed review-thread count; `total` is the total
    threads opened by others (`pr.total_from_others`). The TUI table renders
    `unaddressed/total` from the pair; the starship footer reads only
    `pr-comments`. Both cells write "" when zero so a stale value can't survive.

    `author` is the coworker login for an other-authored PR, empty for a
    self-authored one (see `write_pr_cache`'s `other_author`). Always written so
    a PR that flips ownership (rare) or whose snapshot is rebuilt clears stale
    values.

    `nudge` is `PR.nudge_issue` — the actionable issue category ("" when none)
    that the TUI renders as 🔔. Always written so the bell clears the moment CI
    goes green / threads resolve / the PR merges, with no separate clearing path
    (derived, never stored as standalone state).
    """
    atomic_write(branch_cache("pr-state", branch), state)
    atomic_write(branch_cache("pr-num", branch), str(number) if number else "")
    atomic_write(branch_cache("pr-title", branch), str(title or ""))
    atomic_write(branch_cache("pr-muted", branch), str(muted or ""))
    atomic_write(branch_cache("pr-comments", branch), str(comments) if comments else "")
    atomic_write(branch_cache("pr-comments-total", branch), str(total) if total else "")
    atomic_write(branch_cache("pr-author", branch), str(author or ""))
    atomic_write(branch_cache("pr-nudge", branch), str(nudge or ""))


def refresh_pr_data(branch: str) -> None:
    """Repopulate pr-state / pr-num / pr-title / pr-muted / pr-comments /
    pr-author / pr-nudge flat-cache cells for `branch` from the daemon's per-PR
    JSON snapshot.

    Empty (no-PR) sentinel = zero-byte file with a fresh mtime; suppresses
    per-render reads during the 60s TTL.

    The mute cell is copied straight from the JSON's `muted` field — the
    daemon is the only place mute state is resolved (see write_pr_cache).
    Importing `nudges` here would defeat the single-authority invariant.
    """
    if not branch:
        return
    data = find_pr_payload(branch)
    # A reused-branch merged/closed snapshot (see write_pr_cache) is treated
    # like "no PR" — its cells stay empty so the card shows `—`.
    if data is None or data.get("reusedBranch"):
        _write_pr_flat_cells(
            branch,
            state="",
            number=None,
            title="",
            muted="",
            comments=0,
            total=0,
            author="",
            nudge="",
        )
        return
    _write_pr_flat_cells(
        branch,
        state=_resolve_state(
            str(data.get("state") or ""),
            bool(data.get("isDraft")),
            str(data.get("review") or ""),
        ),
        number=data.get("number"),
        title=str(data.get("title") or ""),
        muted=str(data.get("muted") or ""),
        comments=int(data.get("unaddressed") or 0),
        total=int(data.get("total") or 0),
        author=str(data.get("author") or ""),
        nudge=str(data.get("nudge") or ""),
    )


def refresh_pr_checks(branch: str) -> None:
    """Repopulate pr-checks flat-cache cell for `branch` from the daemon's
    per-PR JSON snapshot, derived via `ci_glyph(payload["ci"])` — the same
    converter the cmux sidebar uses.

    Empty payload when no PR snapshot exists for the branch.
    """
    if not branch:
        return
    cache = branch_cache("pr-checks", branch)
    data = find_pr_payload(branch)
    if data is None or data.get("reusedBranch"):
        atomic_write(cache, "")
        return
    atomic_write(cache, _ci_glyph(str(data.get("ci") or "")))


def write_git_state_cache(cwd: os.PathLike[str] | str, repo_name: str = "") -> None:
    """Snapshot `cwd`'s local git state (branch + status counts + ahead/behind
    of origin, plus the owning repo name) into flat cells. Reader-side
    replacement for the `git rev-parse` / `git status` / `git rev-list` calls
    that the footer's branch_identity / worktree_status / linear / repo printers
    otherwise make on every render.

    `repo_name` (the config repo name) is cached in the `git-repo` cell for the
    footer's `print_repo`. It rides this writer because the daemon already knows
    it per worktree; readers can't derive it (no git).

    Daemon-only writer. Called from:
      - slow tick: `_write_pr_caches` in `orchestrators.cycle` (once per
        worktree per `slow_poll_interval_seconds`, alongside PR cache writes)
      - fast tick: `cockpit._fast_tick` (every `fast_poll_interval_seconds`,
        network-free; this is what keeps `git checkout` visible in the
        footer within ~30s rather than ~300s)

    The renderer never writes these cells — it reads them, with a one-shot
    live-git fallback only when the cell is missing entirely (cold start
    before the daemon's first tick on a new worktree).

    The `git-branch` cell is the authority on "is cache populated": when
    branch resolves empty (not a git repo, or fully detached with no
    rebase-head-name), all three cells are written empty so a stale value
    from a previous cwd state cannot survive.
    """
    from .git import (
        ahead_of_origin,
        behind_of_origin,
        count_status,
        current_branch,
    )

    branch_path = cwd_cache("git-branch", cwd)
    status_path = cwd_cache("git-status", cwd)
    sync_path = cwd_cache("git-sync", cwd)
    repo_path = cwd_cache("git-repo", cwd)
    atomic_write(repo_path, repo_name or "")

    branch = current_branch(cwd)
    if not branch:
        atomic_write(branch_path, "")
        atomic_write(status_path, "")
        atomic_write(sync_path, "")
        return
    counts = count_status(Path(cwd))
    ahead = ahead_of_origin(cwd, branch)
    behind = behind_of_origin(cwd, branch)
    atomic_write(branch_path, branch)
    atomic_write(status_path, f"{counts.staged} {counts.unstaged} {counts.untracked}")
    atomic_write(sync_path, f"{ahead} {behind}")


def _write_base_count(stem: str, branch: str, count: int) -> None:
    """Cache a base-relative commit count for `branch` in the `stem` cell.

    Written by the cockpit daemon once per cycle, after one shared
    `git fetch origin <base>` per repo. A negative `count` (no base) writes
    the empty payload so a stale reader doesn't keep showing a value from a
    previous repo state.
    """
    if not branch:
        return
    atomic_write(branch_cache(stem, branch), "" if count < 0 else str(count))


def write_base_distance(branch: str, count: int) -> None:
    """Cache rebase-staleness for `branch` (commits on base not in branch)."""
    _write_base_count("base-distance", branch, count)


def write_base_ahead(branch: str, count: int) -> None:
    """Cache ahead-of-base for `branch` (commits on branch not in base)."""
    _write_base_count("base-ahead", branch, count)


def write_branch_pr_cache(
    branch: str,
    *,
    state: str,
    is_draft: bool,
    review_decision: str,
    number: int | None,
    title: str,
    ci_glyph: str = "",
    muted: str = "",
    comments: int = 0,
    total: int = 0,
    author: str = "",
    nudge: str = "",
) -> None:
    """Daemon-tick entrypoint: write pre-resolved PR fields straight to the
    flat cache, no `gh` round-trip needed. Caller (cockpit.py::cycle_repo)
    already has this data from its own PR fetch.

    `ci_glyph` is empty by default — the per-render background refresh
    will repopulate `pr-checks-<branch>` from `gh pr checks` when stale.

    `muted` follows the `pr-muted` flat-cell contract: "" (not muted) or
    "muted". Always written so an unmute clears the cell same-tick.

    `comments` is the unaddressed review-thread count from the PR fetch;
    `total` is the total threads opened by others (`pr.total_from_others`).

    `author` is the coworker login for an other-authored PR, empty for a
    self-authored one (the daemon resolves this against `self_user` — see
    `write_pr_cache`'s `other_author`).

    `nudge` is `PR.nudge_issue` — the actionable issue category ("" when none)
    rendered as the TUI 🔔; always written so the bell clears same-tick.
    """
    if not branch:
        return
    _write_pr_flat_cells(
        branch,
        state=_resolve_state(state, is_draft, review_decision),
        number=number,
        title=title,
        muted=muted,
        comments=comments,
        total=total,
        author=author,
        nudge=nudge,
    )
    if ci_glyph:
        atomic_write(branch_cache("pr-checks", branch), ci_glyph)


_BRANCH_PR_CELLS = (
    "pr-state",
    "pr-num",
    "pr-title",
    "pr-muted",
    "pr-comments",
    "pr-comments-total",
    "pr-author",
    "pr-nudge",
    "pr-checks",
)


def clear_branch_pr_cache(branch: str) -> None:
    """Empty every branch-keyed PR flat cell for `branch`.

    The daemon writes this when a branch's only PR snapshot is a merged/closed
    PR whose head the worktree has advanced past (branch reused — see
    `cycle._is_reused_branch_merge`). The persistent JSON snapshot is kept
    (autoclose/teardown still read it), but the statusline must show no PR, so
    every flat cell (`_BRANCH_PR_CELLS`) is zeroed — the same empty shape the
    no-PR path in `refresh_pr_data` / `refresh_pr_checks` writes.
    """
    if not branch:
        return
    for stem in _BRANCH_PR_CELLS:
        atomic_write(branch_cache(stem, branch), "")


def republish_pr_caches_from_disk() -> None:
    """Re-publish every cached PR JSON snapshot to its branch-keyed flat cells.

    Daemon-side replacement for the old renderer-spawned `*-refresh`
    pattern. Walks `$COCKPIT_HOME/cache/*__pr-*.json` and, for each
    payload's `branch`, re-writes `pr-state`, `pr-num`, `pr-title`,
    `pr-muted`, `pr-comments`, `pr-comments-total`, `pr-author`, `pr-nudge`,
    `pr-checks`.
    Pure JSON → flat-cell republish,
    no `gh` calls — safe to run on the fast tick.

    Necessary because the per-PR JSON lives under `$COCKPIT_HOME/cache/`
    (persistent) but the flat cells live under `$TMPDIR/cockpit-cache/`
    (subject to OS tmpdir cleanup). When the OS prunes tmpdir, the JSON
    survives; the fast tick repopulates the flat cells from JSON within
    one cycle. Also bounds the lag between an externally-triggered
    the slow tick (which writes JSON + cells together) and the next
    render — without this, the renderer would have to spawn its own
    refresher to detect tmpdir-wipe.
    """
    if not CACHE_DIR.is_dir():
        return
    best_by_branch: dict[str, dict] = {}
    for _, payload in _iter_cache("*__pr-*.json"):
        branch = payload.get("branch")
        if not branch:
            continue
        cur = best_by_branch.get(branch)
        if cur is None or _pr_payload_rank(payload) > _pr_payload_rank(cur):
            best_by_branch[branch] = payload
    for branch, payload in best_by_branch.items():
        if payload.get("reusedBranch"):
            # Branch reused after its PR merged/closed — no PR to show. Clear
            # the flat cells so the OS-tmpdir-wipe recovery path doesn't
            # republish a stale merged state.
            clear_branch_pr_cache(branch)
            continue
        _write_pr_flat_cells(
            branch,
            state=_resolve_state(
                str(payload.get("state") or ""),
                bool(payload.get("isDraft")),
                str(payload.get("review") or ""),
            ),
            number=payload.get("number"),
            title=str(payload.get("title") or ""),
            muted=str(payload.get("muted") or ""),
            comments=int(payload.get("unaddressed") or 0),
            total=int(payload.get("total") or 0),
            author=str(payload.get("author") or ""),
            nudge=str(payload.get("nudge") or ""),
        )
        atomic_write(
            branch_cache("pr-checks", branch), _ci_glyph(str(payload.get("ci") or ""))
        )


def warm_all(branch: str | None = None) -> None:
    """Synchronous prewarm for the current branch: PR data + checks + seed a
    transcript-path from the latest project JSONL if Claude Code hasn't yet
    fed one via statusLine input.
    """
    from .git import current_branch

    branch = branch or current_branch(os.getcwd())
    if not branch:
        return
    refresh_pr_data(branch)
    refresh_pr_checks(branch)
    _seed_transcript_from_project_dir()


def _seed_transcript_from_project_dir() -> None:
    """Pre-seed transcript-path cache (session-less) with the most recent
    .jsonl under ~/.claude/projects/<mangled cwd> so session-time has
    something to render on the first statusline tick.
    """
    cwd = os.getcwd()
    mangled = "-" + cwd.lstrip("/").replace("/", "-").replace(".", "-")
    project_dir = Path.home() / ".claude" / "projects" / mangled
    if not project_dir.is_dir():
        return
    candidates = sorted(
        project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not candidates:
        return
    atomic_write(session_cache("transcript-path", None), str(candidates[0]))
