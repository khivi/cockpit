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
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .config import CACHE_DIR, ensure_state_dirs
from .pills import ci_glyph as _ci_glyph
from .pills import decide_pills

if TYPE_CHECKING:
    from .gh import PR
    from .git import Worktree


# ── JSON per-PR cache (cockpit's primary state) ────────────────────────────


def write_pr_cache(repo_name: str, pr: "PR", wt: "Worktree | None" = None) -> dict:
    """Write a JSON snapshot of `pr` to the cache dir and return the payload.

    `wt` is the local worktree backing `pr.branch`, if any. Used to bake
    worktree-dependent pill decisions (rebase/merge/wip) into the cached
    `pills` array so both cmux and footer read the same source of truth.
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
        "pills": decide_pills(pr, wt),
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


def find_pr_payload(branch: str, repo_name: str | None = None) -> dict | None:
    """Return the cached PR snapshot whose payload matches `branch`, or None.

    If `repo_name` is given, restrict the search to that repo's cache files
    (prefix-glob). Otherwise scan every cache file.
    """
    pattern = f"{repo_name.replace('/', '_')}__pr-*.json" if repo_name else "*.json"
    for _, payload in _iter_cache(pattern):
        if payload.get("branch") == branch:
            return payload
    return None


def find_pr_payload_by_number(pr_num: str, repo_name: str | None = None) -> dict | None:
    """Return the cached PR snapshot whose `number` matches `pr_num`, or None."""
    pattern = (
        f"{repo_name.replace('/', '_')}__pr-{pr_num}.json"
        if repo_name
        else f"*__pr-{pr_num}.json"
    )
    for _, payload in _iter_cache(pattern):
        if str(payload.get("number")) == str(pr_num):
            return payload
    return None


def delete_pr_caches_for_branch(repo_name: str, branch: str) -> None:
    """Remove cached PR snapshots for `repo_name` whose payload `branch` matches."""
    prefix = repo_name.replace("/", "_")
    for path, data in _iter_cache(f"{prefix}__pr-*.json"):
        if data.get("branch") == branch:
            path.unlink(missing_ok=True)


# ── flat-file render cache (read by starship field printers) ───────────────


FLAT_CACHE_DIR = Path(tempfile.gettempdir()) / "cockpit-cache"
PR_CACHE_TTL_SECS = 60


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


def is_fresh(path: Path, ttl_secs: int = PR_CACHE_TTL_SECS) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) < ttl_secs
    except OSError:
        return False


def _resolve_state(state: str, is_draft: bool, review: str) -> str:
    if state == "OPEN":
        if is_draft:
            return "DRAFT"
        if review:
            return review
    return state


def refresh_pr_data(branch: str) -> None:
    """Repopulate pr-state / pr-num / pr-title flat-cache cells for `branch`
    from the daemon's per-PR JSON snapshot.

    Empty (no-PR) sentinel = zero-byte file with a fresh mtime; suppresses
    per-render reads during the 60s TTL.
    """
    if not branch:
        return
    state_path = branch_cache("pr-state", branch)
    num_path = branch_cache("pr-num", branch)
    title_path = branch_cache("pr-title", branch)
    data = find_pr_payload(branch)
    if data is None:
        atomic_write(state_path, "")
        atomic_write(num_path, "")
        atomic_write(title_path, "")
        return
    state = _resolve_state(
        str(data.get("state") or ""),
        bool(data.get("isDraft")),
        str(data.get("review") or ""),
    )
    number = data.get("number")
    title = data.get("title") or ""
    atomic_write(state_path, state)
    atomic_write(num_path, str(number) if number else "")
    atomic_write(title_path, str(title))


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


def write_base_distance(branch: str, count: int, fetch_epoch: int) -> None:
    """Cache rebase-staleness for `branch` as `<count> <fetch_epoch>`.

    Written by the cockpit daemon once per cycle, after one shared
    `git fetch origin <base>` per repo. The fetch_epoch lets readers
    decide whether the count is fresh enough to display.

    Empty / no-base writes the empty payload so a stale reader doesn't
    keep showing a value from a previous repo state.
    """
    if not branch:
        return
    path = branch_cache("base-distance", branch)
    if count < 0 or fetch_epoch <= 0:
        atomic_write(path, "")
        return
    atomic_write(path, f"{count} {fetch_epoch}")


def write_base_ahead(branch: str, count: int, fetch_epoch: int) -> None:
    """Cache ahead-of-base for `branch` as `<count> <fetch_epoch>`.

    Mirrors `write_base_distance` — same payload shape, same staleness
    semantics, written from the same daemon tick on the same fetch.
    """
    if not branch:
        return
    path = branch_cache("base-ahead", branch)
    if count < 0 or fetch_epoch <= 0:
        atomic_write(path, "")
        return
    atomic_write(path, f"{count} {fetch_epoch}")


def write_branch_pr_cache(
    branch: str,
    *,
    state: str,
    is_draft: bool,
    review_decision: str,
    number: int | None,
    title: str,
    ci_glyph: str = "",
) -> None:
    """Daemon-tick entrypoint: write pre-resolved PR fields straight to the
    flat cache, no `gh` round-trip needed. Caller (cockpit.py::cycle_repo)
    already has this data from its own PR fetch.

    `ci_glyph` is empty by default — the per-render background refresh
    will repopulate `pr-checks-<branch>` from `gh pr checks` when stale.
    """
    if not branch:
        return
    resolved = _resolve_state(state, is_draft, review_decision)
    atomic_write(branch_cache("pr-state", branch), resolved)
    atomic_write(branch_cache("pr-num", branch), str(number) if number else "")
    atomic_write(branch_cache("pr-title", branch), title or "")
    if ci_glyph:
        atomic_write(branch_cache("pr-checks", branch), ci_glyph)


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
