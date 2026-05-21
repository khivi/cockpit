#!/usr/bin/env python3
"""`/cockpit:close` — remove worktree + cmux workspace + PR cache.

Refuses on uncommitted changes, unpushed commits, or open PR unless `--force`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.cache import (  # noqa: E402
    delete_pr_caches_for_branch,
    find_pr_payload,
)
from lib.cmux import (  # noqa: E402
    cmux_close_workspace_best_effort,
    require_workspace_binary,
    resolve_workspace,
)
from lib.config import discover_repo  # noqa: E402
from lib.git import remove_worktree  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Close a cockpit worktree + workspace.")
    p.add_argument("query", help="PR (#N or N), branch, or workspace slug")
    p.add_argument(
        "--force", action="store_true", help="bypass dirty/unpushed/open-PR refusal"
    )
    return p.parse_args()


def main() -> int:
    require_workspace_binary()
    args = parse_args()
    repo_cfg = discover_repo()
    repo_dir = Path(repo_cfg["path"]).expanduser() if repo_cfg else Path.cwd()
    repo_name = repo_cfg.get("name") if repo_cfg else None

    try:
        match = resolve_workspace(args.query, repo_dir)
    except LookupError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    wt = match.worktree
    blockers: list[str] = []
    if wt is not None:
        if wt.dirty_count > 0:
            blockers.append(f"{wt.dirty_count} uncommitted file(s)")
        if wt.unpushed > 0:
            blockers.append(f"{wt.unpushed} unpushed commit(s)")
        elif wt.unpushed == -1:
            blockers.append("could not verify push state")

    payload = None
    if wt is not None and repo_name is not None:
        payload = find_pr_payload(wt.branch, repo_name=repo_name)
    if payload and str(payload.get("state", "")).upper() == "OPEN":
        blockers.append(f"PR #{payload['number']} is OPEN")

    if blockers and not args.force:
        print(
            f"ERROR: refusing to close {match.name or match.ref}: "
            + "; ".join(blockers)
            + " (re-run with --force to override)",
            file=sys.stderr,
        )
        return 1

    cmux_close_workspace_best_effort(match.ref)

    if wt is not None:
        ok, err = remove_worktree(repo_dir, wt.path, force=args.force)
        if not ok:
            print(f"WARN: git worktree remove failed: {err}", file=sys.stderr)
        if repo_name is not None:
            delete_pr_caches_for_branch(repo_name, wt.branch)

    print(f"closed workspace {match.name or match.ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
