"""PR cache snapshots under $COCKPIT_HOME/cache/.

Cockpit writes one `{repo}__pr-{N}.json` file per relevant PR each cycle.
Consumers:
  - reconcile loop reads + writes via this module
  - `lib/list.py` and `scripts/close.py` read via `find_pr_payload`
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from .config import CACHE_DIR, ensure_state_dirs
from .pills import decide_pills

if TYPE_CHECKING:
    from .gh import PR
    from .git import Worktree


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
