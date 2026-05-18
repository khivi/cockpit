#!/usr/bin/env python3
"""Create worktree (sibling of main repo) + spawn cmux workspace with claude pre-running.

Usage:
  spawn.py --branch <branch> --path <path> --short <short>             # branch mode
  spawn.py --branch <branch> --path <path> --short <short> --pr <num>  # PR mode (fetch pull/N/head)
  spawn.py <pr-or-branch> [--base <branch>]                            # convenience entrypoint

Behaviour:
  - Repo discovery walks up from cwd; matches against ~/.config/cockpit/config.json.
    If unmatched, calls lib.registry.register_cwd() to add cwd's repo.
  - Worktree path: dirname(repo)/<short>, with -2/-3/... on collision.
  - Idempotent: existing worktree+workspace for the branch -> attach, don't error.
  - cmux workspace: `cmux new-workspace --name <short> --cwd <wt> --command 'claude' --focus false`

Exit codes:
  0 = ok (created or attached)
  1 = usage / config error
  2 = no managed repo and register_cwd failed
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from lib.cmux import cmux, workspace_names
from lib.config import discover_repo
from lib.gh import resolve_pr_branch
from lib.git import collision_free, create_worktree, slugify, worktree_for_branch
from lib.registry import register_cwd


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--branch")
    p.add_argument("--path")
    p.add_argument("--short")
    p.add_argument("--pr")
    p.add_argument("--base")
    p.add_argument("positional", nargs="?")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    branch, wt_path, short, pr_num, base = (
        args.branch,
        args.path,
        args.short,
        args.pr,
        args.base,
    )

    repo_cfg = discover_repo()
    if repo_cfg is None:
        print(
            "no managed repo for cwd; auto-adding via register_cwd",
            file=sys.stderr,
        )
        try:
            repo_cfg = register_cwd()
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        repo_cfg = discover_repo()
        if repo_cfg is None:
            print("ERROR: still no managed repo after auto-add", file=sys.stderr)
            return 2

    repo = Path(repo_cfg["path"]).expanduser().resolve()
    branch_prefix = repo_cfg.get("branch_prefix", "")
    default_base = repo_cfg.get("default_base", "main")
    if not base:
        base = default_base

    if args.positional and not branch and not pr_num:
        if re.fullmatch(r"#?\d+", args.positional):
            pr_num = args.positional.lstrip("#")
        else:
            branch = args.positional

    if pr_num and not branch:
        try:
            branch = resolve_pr_branch(pr_num)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    if not branch:
        print("ERROR: need <branch> or --pr <num>", file=sys.stderr)
        return 1

    if not short:
        short = slugify(branch.rsplit("/", 1)[-1])

    if not wt_path:
        wt_path = str(repo.parent / short)
    wt = Path(wt_path)

    existing = worktree_for_branch(repo, branch)
    if existing is not None:
        wt = existing
        attached_wt = True
    else:
        attached_wt = False
        wt = collision_free(wt)
        branch = create_worktree(
            repo,
            branch,
            wt,
            base=base,
            pr_num=pr_num,
            branch_prefix=branch_prefix,
        )

    ws_name = short
    existing_ws = set(workspace_names().values())
    if ws_name in existing_ws:
        attached_ws = True
    else:
        attached_ws = False
        cmux(
            "new-workspace",
            "--name",
            ws_name,
            "--cwd",
            str(wt),
            "--command",
            "claude",
            "--focus",
            "false",
        )

    if attached_wt and attached_ws:
        print(f"attached existing workspace {ws_name} at {wt} on {branch}")
    else:
        print(f"workspace {ws_name} spawned at {wt} on {branch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
