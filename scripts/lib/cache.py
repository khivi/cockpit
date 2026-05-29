"""Cockpit's two caches: per-PR JSON snapshots and the flat-file render cache.

Two cache directories, both owned by this module:

1. `$COCKPIT_HOME/cache/{repo}__pr-{N}.json` (referenced as `CACHE_DIR`).
   Rich JSON per PR. Written each reconcile cycle by `write_pr_cache`,
   read by `/cockpit:list` and `scripts/close.py`.

2. `$TMPDIR/cockpit-cache/{stem}[-<sid>|-<branch>]` (referenced as
   `FLAT_CACHE_DIR`). Flat one-string-per-file payloads consumed by
   `scripts/starship.py`'s field printers under starship. Written by:
   - `lib.claude.stash_from_stdin` (session-scoped: context, rate-limit,
     transcript-path)
   - `write_branch_pr_cache` (`cockpit.py` daemon tick, from the PR data
     the daemon fetched — single source of truth for PR-derived fields)
   - `refresh_pr_data` / `refresh_pr_checks` (60s stale-triggered, forked
     from a field printer that sees its cache file is too old). Both
     re-derive the flat-cache values from the daemon's per-PR JSON
     snapshot, so the footer and cmux sidebar share one source.

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

    Returns "" when not muted, "all" for full mute, or a sorted comma-joined
    category list (e.g. "ci,comments") for partial. Same string is also
    embedded as JSON `muted` so renderer-spawned refreshers can copy it
    straight through.
    """
    if pref is None or not pref.disabled_categories:
        return ""
    from .nudges import KNOWN_CATEGORIES

    cats = pref.disabled_categories
    if cats >= set(KNOWN_CATEGORIES):
        return "all"
    return ",".join(sorted(cats))


# ── JSON per-PR cache (cockpit's primary state) ────────────────────────────


def write_pr_cache(
    repo_name: str,
    pr: PR,
    wt: Worktree | None = None,
    pref: NudgePref | None = None,
) -> dict:
    """Write a JSON snapshot of `pr` to the cache dir and return the payload.

    `wt` is the local worktree backing `pr.branch`, if any. Used to bake
    worktree-dependent pill decisions (rebase/merge/wip) into the cached
    `pills` array so both cmux and footer read the same source of truth.

    `pref` is the daemon-resolved nudge mute state. Baked in as `muted` so the
    renderer-spawned `refresh_pr_data` can republish the same snapshot into
    the `pr-muted` flat cell without re-reading `nudges`.
    """
    ensure_state_dirs()
    safe = repo_name.replace("/", "_")
    path = CACHE_DIR / f"{safe}__pr-{pr.number}.json"
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
        "mergeable": pr.mergeable,
        "muted": muted_payload(pref),
        "pills": decide_pills(pr, wt, pref),
    }
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)
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
    pattern = f"{repo_name.replace('/', '_')}__pr-*.json" if repo_name else "*.json"
    best: dict | None = None
    for _, payload in _iter_cache(pattern):
        if payload.get("branch") != branch:
            continue
        if best is None or _pr_payload_rank(payload) > _pr_payload_rank(best):
            best = payload
    return best


def find_pr_payload_by_number(pr_num: str, repo_name: str | None = None) -> dict | None:
    """Return the cached PR snapshot whose `number` matches `pr_num`, or None."""
    pattern = (
        f"{repo_name.replace('/', '_')}__pr-{pr_num}.json"
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
    prefix = repo_name.replace("/", "_")
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
    prefix = repo_name.replace("/", "_")
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
    """Write `payload` to `path` atomically via .tmp + rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload)
    os.replace(tmp, path)


def read_text(path: Path) -> str:
    """Best-effort read; returns empty string on any IO error."""
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _branch_key(branch: str) -> str:
    return branch.replace("/", "-")


def session_cache(stem: str, sid: str | None) -> Path:
    suffix = f"-{sid}" if sid else ""
    return _ensure_flat_cache_dir() / f"{stem}{suffix}"


def branch_cache(stem: str, branch: str) -> Path:
    return _ensure_flat_cache_dir() / f"{stem}-{_branch_key(branch)}"


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


def refresh_pr_data(branch: str) -> None:
    """Repopulate pr-state / pr-num / pr-title / pr-muted / pr-comments
    flat-cache cells for `branch` from the daemon's per-PR JSON snapshot.

    Empty (no-PR) sentinel = zero-byte file with a fresh mtime; suppresses
    per-render reads during the 60s TTL.

    The mute cell is copied straight from the JSON's `muted` field — the
    daemon is the only place mute state is resolved (see write_pr_cache).
    Importing `nudges` here would defeat the single-authority invariant.
    """
    if not branch:
        return
    state_path = branch_cache("pr-state", branch)
    num_path = branch_cache("pr-num", branch)
    title_path = branch_cache("pr-title", branch)
    muted_path = branch_cache("pr-muted", branch)
    comments_path = branch_cache("pr-comments", branch)
    data = find_pr_payload(branch)
    if data is None:
        atomic_write(state_path, "")
        atomic_write(num_path, "")
        atomic_write(title_path, "")
        atomic_write(muted_path, "")
        atomic_write(comments_path, "")
        return
    state = _resolve_state(
        str(data.get("state") or ""),
        bool(data.get("isDraft")),
        str(data.get("review") or ""),
    )
    number = data.get("number")
    title = data.get("title") or ""
    unaddressed = int(data.get("unaddressed") or 0)
    atomic_write(state_path, state)
    atomic_write(num_path, str(number) if number else "")
    atomic_write(title_path, str(title))
    atomic_write(muted_path, str(data.get("muted") or ""))
    atomic_write(comments_path, str(unaddressed) if unaddressed else "")


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
    if data is None:
        atomic_write(cache, "")
        return
    atomic_write(cache, _ci_glyph(str(data.get("ci") or "")))


def write_git_state_cache(cwd: os.PathLike[str] | str) -> None:
    """Snapshot `cwd`'s local git state (branch + status counts + ahead/behind
    of origin) into three flat cells. Reader-side replacement for the
    `git rev-parse` / `git status` / `git rev-list` calls that the footer's
    branch_identity / worktree_status / linear printers otherwise make on
    every render.

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


def write_base_distance(branch: str, count: int) -> None:
    """Cache rebase-staleness for `branch` as `<count>`.

    Written by the cockpit daemon once per cycle, after one shared
    `git fetch origin <base>` per repo.

    Empty / no-base writes the empty payload so a stale reader doesn't
    keep showing a value from a previous repo state.
    """
    if not branch:
        return
    path = branch_cache("base-distance", branch)
    if count < 0:
        atomic_write(path, "")
        return
    atomic_write(path, str(count))


def write_base_ahead(branch: str, count: int) -> None:
    """Cache ahead-of-base for `branch` as `<count>`.

    Mirrors `write_base_distance` — same payload shape, written from the
    same daemon tick on the same fetch.
    """
    if not branch:
        return
    path = branch_cache("base-ahead", branch)
    if count < 0:
        atomic_write(path, "")
        return
    atomic_write(path, str(count))


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
) -> None:
    """Daemon-tick entrypoint: write pre-resolved PR fields straight to the
    flat cache, no `gh` round-trip needed. Caller (cockpit.py::cycle_repo)
    already has this data from its own PR fetch.

    `ci_glyph` is empty by default — the per-render background refresh
    will repopulate `pr-checks-<branch>` from `gh pr checks` when stale.

    `muted` follows the `pr-muted` flat-cell contract: "" (not muted), "all"
    (full mute), or sorted comma-joined category list (partial). Always
    written so an unmute clears the cell same-tick.

    `comments` is the unaddressed review-thread count from the PR fetch.
    """
    if not branch:
        return
    resolved = _resolve_state(state, is_draft, review_decision)
    atomic_write(branch_cache("pr-state", branch), resolved)
    atomic_write(branch_cache("pr-num", branch), str(number) if number else "")
    atomic_write(branch_cache("pr-title", branch), title or "")
    atomic_write(branch_cache("pr-muted", branch), muted)
    atomic_write(branch_cache("pr-comments", branch), str(comments) if comments else "")
    if ci_glyph:
        atomic_write(branch_cache("pr-checks", branch), ci_glyph)


def republish_pr_caches_from_disk() -> None:
    """Re-publish every cached PR JSON snapshot to its branch-keyed flat cells.

    Daemon-side replacement for the old renderer-spawned `*-refresh`
    pattern. Walks `$COCKPIT_HOME/cache/*__pr-*.json` and, for each
    payload's `branch`, re-writes `pr-state`, `pr-num`, `pr-title`,
    `pr-muted`, `pr-checks`. Pure JSON → flat-cell republish, no `gh`
    calls — safe to run on the fast tick.

    Necessary because the per-PR JSON lives under `$COCKPIT_HOME/cache/`
    (persistent) but the flat cells live under `$TMPDIR/cockpit-cache/`
    (subject to OS tmpdir cleanup). When the OS prunes tmpdir, the JSON
    survives; the fast tick repopulates the flat cells from JSON within
    one cycle. Also bounds the lag between an externally-triggered
    `cockpit --once` (which writes JSON + cells together) and the next
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
        state = _resolve_state(
            str(payload.get("state") or ""),
            bool(payload.get("isDraft")),
            str(payload.get("review") or ""),
        )
        number = payload.get("number")
        unaddressed = int(payload.get("unaddressed") or 0)
        atomic_write(branch_cache("pr-state", branch), state)
        atomic_write(branch_cache("pr-num", branch), str(number) if number else "")
        atomic_write(branch_cache("pr-title", branch), str(payload.get("title") or ""))
        atomic_write(branch_cache("pr-muted", branch), str(payload.get("muted") or ""))
        atomic_write(
            branch_cache("pr-checks", branch), _ci_glyph(str(payload.get("ci") or ""))
        )
        atomic_write(
            branch_cache("pr-comments", branch), str(unaddressed) if unaddressed else ""
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
