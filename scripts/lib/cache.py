"""PR cache snapshots under $COCKPIT_HOME/cache/.

Cockpit writes one `{repo}__pr-{N}.json` file per relevant PR each cycle.
Consumers:
  - reconcile loop reads + writes via this module
  - statusLine footer (`lib/footer.render_footer`) reads via `find_pr_payload`
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .config import CACHE_DIR, ensure_state_dirs

if TYPE_CHECKING:
    from .gh import PR


def write_pr_cache(repo_name: str, pr: "PR") -> dict:
    """Write a JSON snapshot of `pr` to the cache dir and return the payload."""
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
    }
    path.write_text(json.dumps(payload, indent=2))
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
