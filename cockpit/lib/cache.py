"""Cockpit's two caches: per-PR JSON snapshots and the flat-file render cache.

Two cache directories, both owned by this module:

1. `$COCKPIT_HOME/cache/{repo}__pr-{N}.json` (referenced as `CACHE_DIR`).
   Rich JSON per PR. Written each reconcile cycle by `write_pr_cache`,
   read by the `cockpit watch` table (including its close actions).

2. `$TMPDIR/cockpit-cache/{stem}[-<sid>|-<cwd-slug>]` (referenced as
   `FLAT_CACHE_DIR`). Flat one-string-per-file payloads consumed by
   `cockpit/starship.py`'s field printers under starship. Written by:
   - `lib.claude.stash_from_stdin` (session-scoped: context, rate-limit,
     transcript-path, cost)
   - `write_worktree_cost_cache` (`cockpit.py` fast tick — folds those
     session-scoped `cost-<sid>` cells into one `wt-cost-<cwd>` cell per
     worktree, so the TUI reads a cell instead of source state)
   - `write_worktree_pr_cache` (`cockpit.py` daemon tick, from the PR data
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
import re
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


def snoozed_payload(pref: NudgePref | None) -> str:
    """Serialize a NudgePref into the `pr-snoozed` flat-cell contract.

    Returns "snoozed" when the PR is snoozed, else "". Deliberately its own
    cell rather than a third value in `pr-muted`: mute and snooze are different
    user states (see `nudges`), the statusline's `pr-muted` field means mute,
    and a cell whose name lies is the drift this codebase keeps paying to avoid.
    """
    return "snoozed" if (pref is not None and pref.snoozed) else ""


# ── JSON per-PR cache (cockpit's primary state) ────────────────────────────


def _repo_slug(repo_name: str) -> str:
    """Filesystem-safe prefix for a repo's per-PR cache files (`owner/name`)."""
    return repo_name.replace("/", "_")


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write `payload` as indented JSON to `path` via a PID-suffixed tmp + rename.

    The PID suffix keeps concurrent writers (daemon + a renderer-spawned
    refresher) from racing on the same tmp name.
    """
    atomic_write(path, json.dumps(payload, indent=2))


def write_pr_cache(
    repo_name: str,
    pr: PR,
    wt: Worktree | None = None,
    pref: NudgePref | None = None,
    ticket: dict | None = None,
    *,
    reused_branch: bool = False,
    other_author: str = "",
) -> dict:
    """Write a JSON snapshot of `pr` to the cache dir and return the payload.

    `wt` is the local worktree backing `pr.branch`, if any. Used to bake
    worktree-dependent pill decisions (rebase/merge/wip) into the cached
    `pills` array so both cmux and footer read the same source of truth.

    `pref` is the daemon-resolved nudge mute/snooze state. Baked in as `muted`
    and `snoozed` so `refresh_pr_data` (the `warm` prewarm) can republish the
    same snapshot into the `pr-muted` / `pr-snoozed` flat cells without
    re-reading `nudges`.

    `ticket` is the resolved delivery block — `{"tickets": [{"id", "state",
    "title", "url"}], "fetched_at": ts}` — for the tickets this PR delivers (from
    its provider footer: Linear/Jira/Trello/GitHub). Provider-neutral: stored
    under the `ticket` key (`title` is the enrichment a statusline consumer like
    cship reads; `url` is the ticket's web link, which three of the four
    providers can only read out of the PR body — see `cycle._stamp_ticket_urls` —
    so caching it is what lets a renderer link the cell without a `gh` call).
    Network-fetched like the PR itself, so it is cached here rather than
    recomputed every render. The daemon (cycle.py) decides when to refetch vs.
    carry forward; this writer just persists what it's handed.

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
        # The local worktree backing this PR, "" when none. The flat cells are
        # keyed by worktree path (`cwd_cache`), and `republish_pr_caches_from_disk`
        # runs on the fast tick from the JSON alone — so the path has to travel
        # in the payload. Resolving it there from `branch` instead is exactly the
        # ambiguity the cwd key exists to remove: several repos' worktrees answer
        # to one branch name.
        "cwd": str(wt.path) if wt else "",
        # The PR's base branch — the one link a stack is derived from
        # (`lib.stacks.find_stacks`), persisted so the fast tick's flat-cell
        # republish can feed the TUI's indentation without a `gh` round-trip.
        "base": pr.base,
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
        "snoozed": snoozed_payload(pref),
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
    if ticket is not None:
        payload["ticket"] = ticket
    _atomic_write_json(path, payload)
    return payload


def restamp_pref(
    repo_name: str, number: int, cwd: os.PathLike[str] | str, pref: NudgePref
) -> None:
    """Re-stamp one PR snapshot's `muted`/`snoozed` fields from `pref` and
    republish its flat cells, without the `gh` round-trip `write_pr_cache` needs.

    The TUI's `m`/`z` keys are the only writers of a state the daemon does not
    derive — it reads it back out of the pref file — so they are the one case
    where waiting for the next reconcile is pure lag: pressing `z` left the row
    unfolded, unbanded and the footer still reading "Snooze" until the kicked
    cycle had fetched every repo, which reads as a dropped keypress.

    Both fields have to move together. The pref file is the authority, but every
    republish path (`refresh_pr_data`, `republish_pr_caches_from_disk`) reads the
    payload's copy of it — so writing only the cells would have the next fast
    tick revert it 30s later.

    No-op when the snapshot is missing; the kicked cycle rebuilds it.
    """
    path = CACHE_DIR / f"{_repo_slug(repo_name)}__pr-{number}.json"
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    payload["muted"] = muted_payload(pref)
    payload["snoozed"] = snoozed_payload(pref)
    _atomic_write_json(path, payload)
    # Publish from the payload just written rather than re-reading it by branch:
    # the caller named the row's worktree, and a branch lookup could resolve to
    # a same-named branch in another repo.
    _publish_pr_cells(cwd, payload)


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


def find_pr_payload_for_cwd(cwd: os.PathLike[str] | str, branch: str) -> dict | None:
    """Return the cached PR snapshot backing the worktree at `cwd`, or None.

    Prefers the snapshot the daemon stamped with this exact worktree path, which
    is unambiguous even when several repos hold a worktree on `branch`. Falls
    back to a branch match for a snapshot written before the `cwd` field existed
    (or by a `gh`-less prewarm that never saw a worktree) — a payload from the
    wrong repo is still better than a blank card, and the next slow tick
    overwrites it with a stamped one.
    """
    target = str(Path(cwd).resolve())
    by_cwd: list[dict] = [
        payload
        for _, payload in _iter_cache("*__pr-*.json")
        if payload.get("cwd") and str(Path(payload["cwd"]).resolve()) == target
    ]
    if by_cwd:
        return max(by_cwd, key=_pr_payload_rank)
    return find_pr_payload(branch)


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


def _cwd_key(cwd: os.PathLike[str] | str) -> str:
    """Filesystem-safe slug for an absolute cwd: `/` → `-`, leading dash stripped."""
    return str(Path(cwd).resolve()).replace("/", "-").lstrip("-")


def cwd_cache(stem: str, cwd: os.PathLike[str] | str) -> Path:
    """Per-cwd flat-cache cell — the only key this cache has.

    Git-state cells are keyed by cwd because the branch name is itself one of
    the cached values: readers don't know the branch until they've read the
    cache, so the key must be derivable from cwd alone.

    The PR and base-distance cells were once keyed by branch, and that key is
    not unique. A branch name is unique within a repo, never across a fleet —
    three repos each holding a `khivi/ci-gatekeeper` worktree shared one
    `pr-num` cell, so all three rows rendered whichever repo's PR was written
    last, and `z` on any of them snoozed a PR number belonging to a different
    repo. Every cell is keyed by the worktree path instead, which is unique by
    construction and is what each of the three renderers (TUI row, starship
    footer, tooltip) already has in hand.
    """
    return _ensure_flat_cache_dir() / f"{stem}-{_cwd_key(cwd)}"


# ── Per-worktree session cost ───────────────────────────────────────────────
# `lib.claude.stash_from_stdin` writes one `cost-<sid>` cell per Claude Code
# session, keyed by session id. The TUI is keyed by worktree path, so the two
# need bridging — and the bridge is Claude Code's own project directory, whose
# name is a lossy-but-deterministic *forward* slug of the session's cwd:
#
#   /opt/dev/repo/wt     →  ~/.claude/projects/-opt-dev-repo-wt/<sid>.jsonl
#   /opt/dev/repo/.bare  →  ~/.claude/projects/-opt-dev-repo--bare/
#
# Every non-alphanumeric character collapses to `-` (so `/` and `.` both do),
# case is preserved, and no leading dash is stripped. That map is one-way — two
# distinct paths can slug to the same directory — so it is only ever walked
# forwards, worktree path → directory. Never try to recover a path from a slug.

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _claude_project_slug(cwd: os.PathLike[str] | str) -> str:
    """Claude Code's `~/.claude/projects/` directory name for `cwd`."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd).resolve()))


def worktree_cost(cwd: os.PathLike[str] | str) -> float:
    """Total USD spent by every Claude Code session rooted at `cwd`.

    Sums the `cost-<sid>` session cells for the sids Claude Code has recorded
    under `cwd`'s project directory — i.e. what this worktree has cost so far,
    not what its newest session cost. Reads only cells that already exist; no
    transcript is parsed (Claude Code hands cockpit the pre-computed number in
    the statusLine blob) and no session is created by asking.

    Returns 0.0 when the project directory is absent (no session ever ran here)
    or when no session reported a cost — the caller treats that as "nothing to
    show" rather than as a real $0.
    """
    proj = CLAUDE_PROJECTS_DIR / _claude_project_slug(cwd)
    try:
        sids = [p.stem for p in proj.glob("*.jsonl")]
    except OSError:
        return 0.0
    total = 0.0
    for sid in sids:
        try:
            total += float(read_text(session_cache("cost", sid)) or 0)
        except ValueError:
            continue
    return total


def write_worktree_cost_cache(cwd: os.PathLike[str] | str) -> None:
    """Snapshot `cwd`'s total session spend into the `wt-cost` flat cell.

    Daemon-only writer (fast tick), so the TUI reads one cell per row instead
    of globbing `~/.claude/projects` on every render — renderers never read
    source state. The stem is `wt-cost`, deliberately not `cost`: the latter is
    `lib.claude`'s session-keyed cell, and a shared stem would make `cost-*`
    match both a session id and a path slug.

    Writes "" rather than "0" for a costless worktree so the reader can tell
    "nothing reported" from a genuine zero without parsing.
    """
    total = worktree_cost(cwd)
    atomic_write(cwd_cache("wt-cost", cwd), f"{total:.4f}" if total > 0 else "")


def read_worktree_cost(cwd: os.PathLike[str] | str) -> float:
    """Read back `write_worktree_cost_cache`'s cell; 0.0 when unset/unparsable."""
    try:
        return float(read_text(cwd_cache("wt-cost", cwd)) or 0)
    except ValueError:
        return 0.0


def cost_reporting_available() -> bool:
    """True when Claude Code reports real per-session spend on this machine.

    Some plans/builds write `total_cost_usd: 0` for every session, which would
    leave a permanently blank `$` column. There is no subscription tier in the
    statusLine blob to test, so the *data* is the gate: if no session has ever
    reported a non-zero cost, the column never appears. Cheap enough to call at
    compose time — one glob over the flat cache dir.
    """
    try:
        cells = FLAT_CACHE_DIR.glob("cost-*")
    except OSError:
        return False
    for cell in cells:
        try:
            if float(read_text(cell) or 0) > 0:
                return True
        except ValueError:
            continue
    return False


def _resolve_state(state: str, is_draft: bool, review: str) -> str:
    if state == "OPEN":
        if is_draft:
            return "DRAFT"
        if review:
            return review
    return state


_STATUSLINE_TICKET_MAX = 40


def ticket_display(
    t: dict, provider: str, *, missing: str = "", max_len: int | None = None
) -> str:
    """The human-facing handle for one delivered ticket. Trello ids are opaque
    short links, so prefer the cached card title (id fallback, truncated with a
    trailing "…" when `max_len` is set — a long card name can't widen a pill);
    every other provider's id (PE-1234, #123, PROJ-45) is itself the meaningful
    handle, returned as-is. Shared by the statusline `pr-ticket` cell
    (`ticket_pill_id`), the TUI Ticket cell / 📍 hover (`worktree_table`), and
    the `devdone=` pill (`cycle`)."""
    if provider == "trello":
        text = str(t.get("title") or t.get("id") or missing)
        if max_len is not None and len(text) > max_len:
            text = text[: max_len - 1] + "…"
        return text
    return str(t.get("id") or missing)


def ticket_pill_id(block: dict | None) -> str:
    """The first delivered ticket's display handle from a `ticket` block
    (`{"provider", "tickets": [{"id", "title", ...}], ...}`), or "" — the value
    the statusline `pr-ticket` cell / pill renders: Linear `PE-1234`, Jira
    `PROJ-123`, GitHub `#123`, Trello the card *title* (short-link fallback,
    truncated to `_STATUSLINE_TICKET_MAX`). Reads the provider off the block
    (self-describing, written by `cycle._prefetch_linear_blocks`); an old
    provider-less on-disk block falls back to the raw id for one cycle until
    rewritten. Footer-derived by the daemon, so a codename branch that carries
    no id (Trello/Slack sources) still resolves once the PR footer is aligned.
    """
    block = block or {}
    tickets = block.get("tickets") or []
    if not tickets:
        return ""
    return ticket_display(
        tickets[0], str(block.get("provider") or ""), max_len=_STATUSLINE_TICKET_MAX
    )


def _write_pr_flat_cells(
    cwd: os.PathLike[str] | str,
    *,
    state: str,
    number: int | None,
    title: str,
    muted: str,
    comments: int,
    total: int = 0,
    author: str = "",
    nudge: str = "",
    ticket_id: str = "",
    base: str = "",
    snoozed: str = "",
) -> None:
    """Write the eleven worktree-keyed PR flat cells that every PR writer shares.

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

    `ticket_id` is the delivered ticket display handle (`ticket_pill_id` of the
    PR's `ticket` block — the id for most providers, the card title for Trello)
    that the statusline `pr-ticket` pill renders, resolved off the footer so a
    Trello codename branch works without a branch regex. Always written so a
    re-aligned or removed footer clears the stale value.

    `base` is the PR's base branch (`PR.base`). It is what makes a stack
    visible to a renderer: the TUI indents a row under the row whose branch
    equals this cell, the same `PR.base` link `lib.stacks.find_stacks` follows
    — derived, never stored as a stack id. Always written so a retarget (or a
    merged parent) re-flattens the row on the next republish.

    `snoozed` follows the `pr-snoozed` flat-cell contract: "" (awake) or
    "snoozed". Always written so the daemon's auto-wake (a new comment or a
    review decision — see `nudges.wake_signature`) un-folds the row same-tick,
    with no separate clearing path.
    """
    atomic_write(cwd_cache("pr-state", cwd), state)
    atomic_write(cwd_cache("pr-num", cwd), str(number) if number else "")
    atomic_write(cwd_cache("pr-title", cwd), str(title or ""))
    atomic_write(cwd_cache("pr-muted", cwd), str(muted or ""))
    atomic_write(cwd_cache("pr-comments", cwd), str(comments) if comments else "")
    atomic_write(cwd_cache("pr-comments-total", cwd), str(total) if total else "")
    atomic_write(cwd_cache("pr-author", cwd), str(author or ""))
    atomic_write(cwd_cache("pr-nudge", cwd), str(nudge or ""))
    atomic_write(cwd_cache("pr-ticket", cwd), str(ticket_id or ""))
    atomic_write(cwd_cache("pr-base", cwd), str(base or ""))
    atomic_write(cwd_cache("pr-snoozed", cwd), str(snoozed or ""))


def _publish_pr_cells(cwd: os.PathLike[str] | str, payload: dict) -> None:
    """Write one PR snapshot's flat cells (`pr-*` minus `pr-checks`) for `cwd`.

    The shared tail of every republish path — `refresh_pr_data`,
    `republish_pr_caches_from_disk` and the TUI's `restamp_pref` — so the three
    can't drift on which fields a cell is derived from.
    """
    _write_pr_flat_cells(
        cwd,
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
        ticket_id=ticket_pill_id(payload.get("ticket")),
        base=str(payload.get("base") or ""),
        snoozed=str(payload.get("snoozed") or ""),
    )


def refresh_pr_data(cwd: os.PathLike[str] | str, branch: str) -> None:
    """Repopulate pr-state / pr-num / pr-title / pr-muted / pr-comments /
    pr-author / pr-nudge flat-cache cells for the worktree at `cwd` from the
    daemon's per-PR JSON snapshot.

    Empty (no-PR) sentinel = zero-byte file with a fresh mtime; suppresses
    per-render reads during the 60s TTL.

    The mute cell is copied straight from the JSON's `muted` field — the
    daemon is the only place mute state is resolved (see write_pr_cache).
    Importing `nudges` here would defeat the single-authority invariant.
    """
    if not branch:
        return
    data = find_pr_payload_for_cwd(cwd, branch)
    # A reused-branch merged/closed snapshot (see write_pr_cache) is treated
    # like "no PR" — its cells stay empty so the card shows `—`.
    if data is None or data.get("reusedBranch"):
        _write_pr_flat_cells(
            cwd,
            state="",
            number=None,
            title="",
            muted="",
            comments=0,
            total=0,
            author="",
            nudge="",
            ticket_id="",
            base="",
            snoozed="",
        )
        return
    _publish_pr_cells(cwd, data)


def refresh_pr_checks(cwd: os.PathLike[str] | str, branch: str) -> None:
    """Repopulate the pr-checks flat-cache cell for the worktree at `cwd` from
    the daemon's per-PR JSON snapshot, derived via `ci_glyph(payload["ci"])` —
    the same converter the cmux sidebar uses.

    Empty payload when no PR snapshot exists for the worktree.
    """
    if not branch:
        return
    cache = cwd_cache("pr-checks", cwd)
    data = find_pr_payload_for_cwd(cwd, branch)
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


def _write_base_count(stem: str, cwd: os.PathLike[str] | str, count: int) -> None:
    """Cache a base-relative commit count for the worktree at `cwd`.

    Written by the cockpit daemon once per cycle, after one shared
    `git fetch origin <base>` per repo. A negative `count` (no base) writes
    the empty payload so a stale reader doesn't keep showing a value from a
    previous repo state.
    """
    atomic_write(cwd_cache(stem, cwd), "" if count < 0 else str(count))


def write_base_distance(cwd: os.PathLike[str] | str, count: int) -> None:
    """Cache rebase-staleness for a worktree (commits on base not in branch)."""
    _write_base_count("base-distance", cwd, count)


def write_base_ahead(cwd: os.PathLike[str] | str, count: int) -> None:
    """Cache ahead-of-base for a worktree (commits on branch not in base)."""
    _write_base_count("base-ahead", cwd, count)


def write_worktree_pr_cache(
    cwd: os.PathLike[str] | str,
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
    ticket_id: str = "",
    base: str = "",
    snoozed: str = "",
) -> None:
    """Daemon-tick entrypoint: write pre-resolved PR fields straight to the
    flat cache, no `gh` round-trip needed. Caller (cockpit.py::cycle_repo)
    already has this data from its own PR fetch.

    `ci_glyph` is empty by default — the per-render background refresh
    will repopulate the `pr-checks` cell from `gh pr checks` when stale.

    `muted` follows the `pr-muted` flat-cell contract: "" (not muted) or
    "muted". Always written so an unmute clears the cell same-tick. `snoozed`
    is its sibling ("" or "snoozed") — see `snoozed_payload`.

    `comments` is the unaddressed review-thread count from the PR fetch;
    `total` is the total threads opened by others (`pr.total_from_others`).

    `author` is the coworker login for an other-authored PR, empty for a
    self-authored one (the daemon resolves this against `self_user` — see
    `write_pr_cache`'s `other_author`).

    `nudge` is `PR.nudge_issue` — the actionable issue category ("" when none)
    rendered as the TUI 🔔; always written so the bell clears same-tick.

    `base` is `PR.base` — the stack link the TUI indents rows by.
    """
    _write_pr_flat_cells(
        cwd,
        state=_resolve_state(state, is_draft, review_decision),
        number=number,
        title=title,
        muted=muted,
        comments=comments,
        total=total,
        author=author,
        nudge=nudge,
        ticket_id=ticket_id,
        base=base,
        snoozed=snoozed,
    )
    if ci_glyph:
        atomic_write(cwd_cache("pr-checks", cwd), ci_glyph)


_PR_CELLS = (
    "pr-state",
    "pr-num",
    "pr-title",
    "pr-muted",
    "pr-comments",
    "pr-comments-total",
    "pr-author",
    "pr-nudge",
    "pr-ticket",
    "pr-base",
    "pr-snoozed",
    "pr-checks",
)


def clear_pr_flat_cells(cwd: os.PathLike[str] | str) -> None:
    """Empty every PR flat cell for the worktree at `cwd`.

    The daemon writes this when a branch's only PR snapshot is a merged/closed
    PR whose head the worktree has advanced past (branch reused — see
    `cycle._is_reused_branch_merge`). The persistent JSON snapshot is kept
    (autoclose/teardown still read it), but the statusline must show no PR, so
    every flat cell (`_PR_CELLS`) is zeroed — the same empty shape the
    no-PR path in `refresh_pr_data` / `refresh_pr_checks` writes.
    """
    for stem in _PR_CELLS:
        atomic_write(cwd_cache(stem, cwd), "")


def republish_pr_caches_from_disk() -> None:
    """Re-publish every cached PR JSON snapshot to its worktree's flat cells.

    Daemon-side replacement for the old renderer-spawned `*-refresh`
    pattern. Walks `$COCKPIT_HOME/cache/*__pr-*.json` and, for each
    payload's `cwd`, re-writes `pr-state`, `pr-num`, `pr-title`,
    `pr-muted`, `pr-comments`, `pr-comments-total`, `pr-author`, `pr-nudge`,
    `pr-ticket`, `pr-base`, `pr-checks`.
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

    A PR with no local worktree carries an empty `cwd` and is skipped: there is
    no row and no session, so nothing reads a cell for it. Dedup is per worktree
    rather than per branch, since two repos' worktrees can answer to one branch
    and each owns its own cells.
    """
    if not CACHE_DIR.is_dir():
        return
    best_by_cwd: dict[str, dict] = {}
    for _, payload in _iter_cache("*__pr-*.json"):
        cwd = payload.get("cwd")
        if not cwd:
            continue
        cur = best_by_cwd.get(cwd)
        if cur is None or _pr_payload_rank(payload) > _pr_payload_rank(cur):
            best_by_cwd[cwd] = payload
    for cwd, payload in best_by_cwd.items():
        if payload.get("reusedBranch"):
            # Branch reused after its PR merged/closed — no PR to show. Clear
            # the flat cells so the OS-tmpdir-wipe recovery path doesn't
            # republish a stale merged state.
            clear_pr_flat_cells(cwd)
            continue
        _publish_pr_cells(cwd, payload)
        atomic_write(
            cwd_cache("pr-checks", cwd), _ci_glyph(str(payload.get("ci") or ""))
        )


def warm_all(branch: str | None = None) -> None:
    """Synchronous prewarm for the current worktree: PR data + checks + seed a
    transcript-path from the latest project JSONL if Claude Code hasn't yet
    fed one via statusLine input.
    """
    from .git import current_branch

    cwd = os.getcwd()
    branch = branch or current_branch(cwd)
    if not branch:
        return
    refresh_pr_data(cwd, branch)
    refresh_pr_checks(cwd, branch)
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
