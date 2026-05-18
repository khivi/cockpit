#!/usr/bin/env python3
"""`/cockpit:close` — remove worktree + cmux workspace + PR cache.

Refuses on uncommitted changes, unpushed commits, or open PR unless `--force`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.cache import delete_pr_caches_for_branch  # noqa: E402
from lib.cmux import cmux_close_workspace_best_effort, resolve_workspace  # noqa: E402
from lib.config import discover_repo  # noqa: E402
from lib.git import _count_dirty, _count_unpushed  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Close a cockpit worktree + workspace.")
    p.add_argument("query", help="PR (#N or N), branch, or workspace slug")
    p.add_argument(
        "--force", action="store_true", help="bypass dirty/unpushed/open-PR refusal"
    )
    return p.parse_args()


def _is_pr_open(payload: dict | None) -> bool:
    if not payload:
        return False
    state = str(payload.get("state", "")).upper()
    return state == "OPEN"


def main() -> int:
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
        dirty = _count_dirty(wt.path)
        unpushed = _count_unpushed(wt.path)
        if dirty > 0:
            blockers.append(f"{dirty} uncommitted file(s)")
        if unpushed > 0:
            blockers.append(f"{unpushed} unpushed commit(s)")
        if unpushed == -1:
            blockers.append("could not verify push state")

    payload = None
    if wt is not None and repo_name is not None:
        from lib.cache import find_pr_payload

        payload = find_pr_payload(wt.branch, repo_name=repo_name)
    if _is_pr_open(payload):
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
        force_flag = ["--force"] if args.force else []
        res = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "worktree",
                "remove",
                *force_flag,
                str(wt.path),
            ],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            print(
                f"WARN: git worktree remove failed: {res.stderr.strip()}",
                file=sys.stderr,
            )
        if repo_name is not None:
            delete_pr_caches_for_branch(repo_name, wt.branch)

    print(f"closed workspace {match.name or match.ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
