#!/usr/bin/env python3
"""Create worktree (sibling of main repo) + spawn cmux workspace with claude pre-running.

Usage:
  spawn.py <branch|PR|github-url>                         # positional (auto-detected)
  spawn.py --branch <branch> --name <name>                # branch mode (explicit)
  spawn.py --branch <branch> --name <name> --pr <num>     # PR mode (fetch pull/N/head)
  spawn.py --cwd <path> --name <name>                     # arbitrary dir (no repo)

Optional:
  --claude-prompt <str>   Prompt for claude's first message.
                          Defaults to a plan-only prompt when input is a PR.
                          Defaults to none (bare `claude`) for branch/cwd input.

Positional detection (5 steps):
  1. GitHub PR URL (https://github.com/.../pull/N) → PR mode
  2. Bare PR number (#N or N)                       → PR mode
  3. Local branch (refs/heads/<branch> exists)           → checkout
  4. Remote branch (ls-remote origin <branch> matches)   → fetch + checkout
  5. New branch (neither local nor remote)               → create from default_base
  # TODO: Linear ID (PE-1234) → resolve via Linear API
  # TODO: Slack URL           → resolve via Slack API

Behaviour:
  - Repo discovery walks up from cwd; matches against ~/.config/cockpit/config.json.
    If unmatched, calls lib.registry.register_cwd() to add cwd's repo.
  - --repo <name> overrides cwd-based discovery and targets a specific configured
    repo by `name`. Useful when invoking from outside the repo's tree.
  - Worktree path: dirname(repo)/<name>, with -2/-3/... on collision.
  - --cwd mode skips repo discovery and worktree creation entirely; the workspace
    is spawned in <path>, which is created if it does not exist.
  - Idempotent: existing worktree+workspace for the branch -> attach, don't error.
  - Explicit --branch/--pr take priority over positional.

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
from lib.config import discover_repo, find_repo_by_name
from lib.gh import fetch_pr_info, resolve_pr_branch
from lib.git import collision_free, create_worktree, slugify, worktree_for_branch
from lib.prompts import claude_command
from lib.registry import register_cwd


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("positional", nargs="?", metavar="branch|PR|url")
    p.add_argument("--branch")
    p.add_argument("--cwd")
    p.add_argument("--name")
    p.add_argument("--pr")
    p.add_argument(
        "--repo", help="target a configured repo by name (skips cwd-based discovery)"
    )
    p.add_argument("--claude-prompt", help="prompt for claude's first message")
    return p.parse_args()


def detect_source(value: str) -> tuple[str, str]:
    """Classify positional into (mode, resolved_value).

    Steps 1-2 resolved here; steps 3-5 (local/remote/new branch) resolved by
    create_worktree at worktree-creation time.
    """
    # Step 1: GitHub PR URL
    m = re.match(r"https?://github\.com/[^/]+/[^/]+/pull/(\d+)", value)
    if m:
        return "pr", m.group(1)
    # Step 2: bare PR number
    if re.fullmatch(r"#?\d+", value):
        return "pr", value.lstrip("#")
    # Steps 3-5: branch (local / remote / new — git resolves at worktree time)
    return "branch", value


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
    repo_name: str | None,
) -> tuple[Path, str, bool]:
    repo_cfg = select_repo(repo_name)
    repo = Path(repo_cfg["path"]).expanduser().resolve()
    branch_prefix = repo_cfg.get("branch_prefix", "")
    base = repo_cfg.get("default_base", "main")

    if pr_num and not branch:
        branch = resolve_pr_branch(pr_num)
    if not branch:
        raise ValueError("need <branch> or --pr <num>")

    existing = worktree_for_branch(repo, branch)
    if existing is not None:
        return existing, branch, True

    wt = collision_free(repo.parent / slugify(branch.rsplit("/", 1)[-1]))
    branch = create_worktree(
        repo, branch, wt, base=base, pr_num=pr_num, branch_prefix=branch_prefix
    )
    return wt, branch, False


def _plan_only_prompt(pr_num: str, branch: str, wt: Path) -> str | None:
    try:
        info = fetch_pr_info(pr_num, wt)
    except Exception:
        return None
    author = (info.get("author") or {}).get("login", "unknown")
    title = info.get("title", f"PR #{pr_num}")
    url = info.get("url", "")
    return "\n".join(
        [
            "/session-coordination",
            "",
            f"You are starting a fresh task in a new worktree on branch `{branch}`.",
            "",
            f"**Source**: PR #{pr_num} by @{author}",
            f"**Task**: {title}",
            "",
            f"**Context**: {url}",
            "",
            "**HARD RULE — PLAN ONLY, NO CODE THIS TURN**:",
            "- DO NOT edit files, write code, run tests, or commit anything.",
            "- You MAY use Read, Grep, Glob, and re-fetch the linked ticket/thread for context.",
            "- Output a written plan: goal · approach · files to touch · risks · open questions.",
            "- Ask clarifying questions if the spec is ambiguous.",
            "- Wait for the user to approve or refine before implementing.",
            "",
            "Begin by writing the plan.",
        ]
    )


def main() -> int:
    args = parse_args()
    branch, cwd, short, pr_num = args.branch, args.cwd, args.name, args.pr

    if args.positional and (branch or pr_num or short):
        print(
            "ERROR: positional is mutually exclusive with --branch/--pr/--name",
            file=sys.stderr,
        )
        return 1
    elif args.positional:
        mode, value = detect_source(args.positional)
        if mode == "pr":
            pr_num = value
        else:
            branch = value
    elif short and not (branch or pr_num or cwd):
        branch = short

    if cwd and (branch or pr_num or args.repo):
        print(
            "ERROR: --cwd is mutually exclusive with --branch/--pr/--repo args",
            file=sys.stderr,
        )
        return 1

    prompt: str | None = args.claude_prompt

    if cwd:
        wt = Path(cwd).expanduser().resolve()
        wt.mkdir(parents=True, exist_ok=True)
        if not short:
            short = slugify(wt.name)
        attached_wt = True
        branch_display = None
    else:
        if not branch and not pr_num:
            print(
                "ERROR: positional <branch|PR|url> or --branch/--pr is required",
                file=sys.stderr,
            )
            return 1

        try:
            wt, branch, attached_wt = resolve_worktree(branch, pr_num, args.repo)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

        if not short:
            short = slugify(branch.rsplit("/", 1)[-1])
        branch_display = branch

        if prompt is None and pr_num:
            prompt = _plan_only_prompt(pr_num, branch, wt)

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
