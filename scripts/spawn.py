#!/usr/bin/env python3
"""Create worktree (sibling of main repo) + spawn cmux workspace with claude pre-running.

Usage:
  spawn.py --branch <branch> --path <path> --short <short>             # branch mode
  spawn.py --branch <branch> --path <path> --short <short> --pr <num>  # PR mode (fetch pull/N/head)
  spawn.py --cwd <path> --short <short>                                # skill mode (no worktree)
  spawn.py <pr-or-branch> [--base <branch>]                            # convenience entrypoint

Optional:
  --prompt-stdin   Read a prompt from stdin and pass it as claude's first message
                   (`claude <quoted-prompt>` instead of bare `claude`).

Behaviour:
  - Repo discovery walks up from cwd; matches against ~/.config/cockpit/config.json.
    If unmatched, calls lib.registry.register_cwd() to add cwd's repo.
  - --repo <name> overrides cwd-based discovery and targets a specific configured
    repo by `name`. Useful when invoking from outside the repo's tree.
  - Worktree path: dirname(repo)/<short>, with -2/-3/... on collision.
  - --cwd mode skips repo discovery and worktree creation entirely; the workspace
    is spawned directly in <path>.
  - Idempotent: existing worktree+workspace for the branch -> attach, don't error.

Exit codes:
  0 = ok (created or attached)
  1 = usage / config error
  2 = no managed repo and register_cwd failed
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from pathlib import Path

from lib.cmux import cmux, workspace_names
from lib.config import discover_repo, find_repo_by_name
from lib.gh import resolve_pr_branch
from lib.git import collision_free, create_worktree, slugify, worktree_for_branch
from lib.registry import register_cwd


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--branch")
    p.add_argument("--path")
    p.add_argument("--cwd")
    p.add_argument("--short")
    p.add_argument("--pr")
    p.add_argument("--base")
    p.add_argument(
        "--repo", help="target a configured repo by name (skips cwd-based discovery)"
    )
    p.add_argument("--prompt-stdin", action="store_true")
    p.add_argument("positional", nargs="?")
    return p.parse_args()


def select_repo(repo_name: str | None) -> dict:
    if repo_name:
        repo_cfg = find_repo_by_name(repo_name)
        if repo_cfg is None:
            raise ValueError(f"--repo {repo_name!r}: no configured repo with that name")
        return repo_cfg
    repo_cfg = discover_repo()
    if repo_cfg is None:
        print(
            "no managed repo for cwd; auto-adding via register_cwd",
            file=sys.stderr,
        )
        repo_cfg = register_cwd()
        repo_cfg = discover_repo() or repo_cfg
    return repo_cfg


def resolve_worktree(
    branch: str | None,
    pr_num: str | None,
    wt_path: str | None,
    base: str,
    repo_name: str | None,
) -> tuple[Path, str, bool]:
    repo_cfg = select_repo(repo_name)

    repo = Path(repo_cfg["path"]).expanduser().resolve()
    branch_prefix = repo_cfg.get("branch_prefix", "")

    if pr_num and not branch:
        branch = resolve_pr_branch(pr_num)
    if not branch:
        raise ValueError("need <branch> or --pr <num>")

    if not wt_path:
        short_default = slugify(branch.rsplit("/", 1)[-1])
        wt_path = str(repo.parent / short_default)

    existing = worktree_for_branch(repo, branch)
    if existing is not None:
        return existing, branch, True

    wt = collision_free(Path(wt_path))
    branch = create_worktree(
        repo, branch, wt, base=base, pr_num=pr_num, branch_prefix=branch_prefix
    )
    return wt, branch, False


def claude_command(prompt: str | None) -> str:
    if prompt is None:
        return "claude"
    return f"claude {shlex.quote(prompt)}"


def main() -> int:
    args = parse_args()
    branch, wt_path, cwd, short, pr_num, base = (
        args.branch,
        args.path,
        args.cwd,
        args.short,
        args.pr,
        args.base,
    )

    if cwd and (branch or pr_num or wt_path or args.positional or args.repo):
        print(
            "ERROR: --cwd is mutually exclusive with branch/PR/positional/--repo args",
            file=sys.stderr,
        )
        return 1

    prompt: str | None = None
    if args.prompt_stdin:
        prompt = sys.stdin.read()
        if not prompt.strip():
            print("ERROR: --prompt-stdin set but stdin is empty", file=sys.stderr)
            return 1

    if cwd:
        wt = Path(cwd).expanduser().resolve()
        if not wt.is_dir():
            print(f"ERROR: --cwd path does not exist: {wt}", file=sys.stderr)
            return 1
        if not short:
            short = slugify(wt.name)
        attached_wt = True
        branch_display = None
    else:
        if args.positional and not branch and not pr_num:
            if re.fullmatch(r"#?\d+", args.positional):
                pr_num = args.positional.lstrip("#")
            else:
                branch = args.positional

        if not base:
            repo_cfg = find_repo_by_name(args.repo) if args.repo else discover_repo()
            base = (repo_cfg or {}).get("default_base", "main")

        try:
            wt, branch, attached_wt = resolve_worktree(
                branch, pr_num, wt_path, base, args.repo
            )
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

        if not short:
            short = slugify(branch.rsplit("/", 1)[-1])
        branch_display = branch

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
            claude_command(prompt),
            "--focus",
            "false",
        )

    verb = "attached existing workspace" if attached_wt and attached_ws else "workspace"
    suffix = f"spawned at {wt}" if verb == "workspace" else f"at {wt}"
    on_branch = f" on {branch_display}" if branch_display else " (no worktree)"
    print(f"{verb} {ws_name} {suffix}{on_branch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
