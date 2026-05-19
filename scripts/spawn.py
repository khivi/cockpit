#!/usr/bin/env python3
"""Create worktree (sibling of main repo) + spawn cmux workspace with claude pre-running.

Usage:
  spawn.py <branch|PR|github-url>                         # positional (auto-detected)
  spawn.py --branch <branch> [--name <name>]              # branch mode (explicit)
  spawn.py --pr <num> [--branch <name>] [--name <short>]  # PR mode (fetch pull/N/head)
  spawn.py --name <short>                                 # new branch + workspace named <short>
  spawn.py --cwd <path> [--name <short>]                  # arbitrary dir (no repo)
  spawn.py --skill <name> [--name <short>] [--repo <n>]   # spawn workspace running a skill

  --name is the workspace short name. Alone, it also seeds the new branch name.
  --skill resolves a repo skill (<repo>/.claude/skills/<n>/skill.md) or a
  global skill (~/.claude/skills/<n>/skill.md); workspace cwd is the repo path
  or $HOME respectively, with `/<n>` as the first-turn claude prompt.

Optional:
  --claude-prompt <str>   Prompt for claude's first message.
                          Defaults to a plan-only prompt when input is a PR.
                          Defaults to none (bare `claude`) for branch/cwd input.

Positional detection (5 steps):
  1. GitHub PR URL (https://github.com/.../pull/N) → PR mode
  2. #-prefixed PR number (#N)                       → PR mode
                                                       (bare N is a branch — see below)
  3. Local branch (refs/heads/<branch> exists)           → checkout
  4. Remote branch (ls-remote origin <branch> matches)   → fetch + checkout
  5. New branch (neither local nor remote)               → create from default_base

  After branch resolution (steps 3-5), gh is queried for an open PR on
  the head ref; if found, the PR info is printed and the plan-only prompt
  is auto-generated (unless --claude-prompt overrides).
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
from lib.gh import fetch_pr_info, pr_for_branch, resolve_pr_branch
from lib.git import collision_free, create_worktree, slugify, worktree_for_branch
from lib.prompts import claude_command


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
    p.add_argument(
        "--skill",
        help="spawn workspace running a global or repo skill (no worktree, no branch)",
    )
    p.add_argument("--claude-prompt", help="prompt for claude's first message")
    return p.parse_args()


def detect_source(value: str) -> tuple[str, str]:
    """Classify positional into (mode, resolved_value).

    PR mode requires a `#` prefix (`#123`) or a full GitHub PR URL. A bare
    integer is treated as a branch name — use `#123` or `--pr 123` for PRs.
    Steps 3-5 (local/remote/new branch) resolved by create_worktree at
    worktree-creation time.
    """
    # Step 1: GitHub PR URL
    m = re.match(r"https?://github\.com/[^/]+/[^/]+/pull/(\d+)", value)
    if m:
        return "pr", m.group(1)
    # Step 2: #-prefixed PR number
    if re.fullmatch(r"#\d+", value):
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
        raise ValueError(
            "cannot determine repo from cwd; pass --repo <name> or run from "
            "inside a managed repo (register first with `cockpit add`)"
        )
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
        branch = resolve_pr_branch(pr_num, repo_dir=repo)
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


def resolve_skill(name: str, repo_name: str | None) -> tuple[Path, str]:
    """Locate a skill and return (workspace_cwd, claude_prompt).

    Lookup order (global takes precedence — mirrors the user's
    "global skills always win" rule):
      1. ~/.claude/skills/<name>/skill.md. If found, cwd is $HOME.
      2. Preferred repo (--repo if given, else current repo via discover_repo):
         <repo>/.claude/skills/<name>/skill.md. If found, cwd is the repo path.
    """
    rel = Path(".claude") / "skills" / name / "skill.md"

    home = Path.home()
    if (home / rel).exists():
        return home, f"/{name}"

    if repo_name:
        repo_cfg = find_repo_by_name(repo_name)
        if repo_cfg is None:
            raise ValueError(f"--repo {repo_name!r}: no configured repo with that name")
    else:
        repo_cfg = discover_repo()

    if repo_cfg is not None:
        repo_path = Path(repo_cfg["path"]).expanduser().resolve()
        if (repo_path / rel).exists():
            return repo_path, f"/{name}"

    raise ValueError(
        f"--skill {name!r}: not found in ~/.claude/skills/ or preferred repo"
    )


def _plan_only_prompt(branch: str, pr_info: dict | None = None) -> str:
    """Plan-only first-turn prompt. PR context block is included when `pr_info` is set."""
    lines = [f"You are starting a fresh task in a new worktree on branch `{branch}`."]
    if pr_info:
        author = (pr_info.get("author") or {}).get("login", "unknown")
        number = pr_info["number"]
        title = pr_info.get("title") or f"PR #{number}"
        lines += [
            "",
            f"**Source**: PR #{number} by @{author}",
            f"**Task**: {title}",
            "",
            f"**Context**: {pr_info.get('url', '')}",
        ]
    lines += [
        "",
        "**HARD RULE — PLAN ONLY, NO CODE THIS TURN**:",
        "- DO NOT edit files, write code, run tests, or commit anything.",
        "- You MAY use Read, Grep, Glob for context (re-fetch the linked ticket/thread where relevant).",
        "- Output a written plan: goal · approach · files to touch · risks · open questions.",
        "- Ask clarifying questions if the task is ambiguous.",
        "- Wait for the user to approve or refine before implementing.",
        "",
        "Begin by writing the plan.",
    ]
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    branch, cwd, short, pr_num, skill = (
        args.branch,
        args.cwd,
        args.name,
        args.pr,
        args.skill,
    )

    if args.positional and (branch or pr_num or short or skill):
        print(
            "ERROR: positional is mutually exclusive with --branch/--pr/--name/--skill",
            file=sys.stderr,
        )
        return 1
    elif args.positional:
        mode, value = detect_source(args.positional)
        if mode == "pr":
            pr_num = value
        else:
            branch = value
    elif short and not (branch or pr_num or cwd or skill):
        branch = short

    if cwd and (branch or pr_num or skill):
        print(
            "ERROR: --cwd is mutually exclusive with --branch/--pr/--skill args",
            file=sys.stderr,
        )
        return 1
    if skill and (branch or pr_num or cwd):
        print(
            "ERROR: --skill is mutually exclusive with --branch/--pr/--cwd args",
            file=sys.stderr,
        )
        return 1
    # --repo is a universal override on repo discovery — combinable with any
    # input source. In --cwd mode it has no effect (no repo lookup happens).
    # --cwd + --name is allowed: --name sets the workspace short name.

    prompt: str | None = args.claude_prompt

    if cwd:
        wt = Path(cwd).expanduser().resolve()
        wt.mkdir(parents=True, exist_ok=True)
        if not short:
            short = slugify(wt.name)
        attached_wt = True
        branch_display = None
    elif skill:
        try:
            wt, skill_prompt = resolve_skill(skill, args.repo)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        if not short:
            short = slugify(skill)
        if prompt is None:
            prompt = skill_prompt
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

        pr_info: dict | None = None
        if pr_num:
            try:
                pr_info = fetch_pr_info(pr_num, wt)
            except Exception:
                pr_info = None
        else:
            pr_info = pr_for_branch(branch, wt)
            if pr_info is not None:
                pr_num = str(pr_info["number"])
                author = (pr_info.get("author") or {}).get("login", "unknown")
                print(
                    f"note: open PR #{pr_num} exists for branch {branch!r}: "
                    f"{pr_info.get('title', '')} by @{author} ({pr_info.get('url', '')})",
                    file=sys.stderr,
                )

        if prompt is None:
            prompt = _plan_only_prompt(branch, pr_info)

    ws_name = short
    attached_ws = ws_name in set(workspace_names().values())
    if not attached_ws:
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

    if attached_wt and attached_ws:
        prefix = f"attached existing workspace {ws_name} at {wt}"
    else:
        prefix = f"workspace {ws_name} spawned at {wt}"
    suffix = f" on {branch_display}" if branch_display else " (no worktree)"
    print(f"{prefix}{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
