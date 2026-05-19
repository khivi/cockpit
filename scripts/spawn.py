#!/usr/bin/env python3
"""Create worktree (sibling of main repo) + spawn cmux workspace with claude pre-running.

Usage:
  spawn.py <branch|PR|github-url>                       # positional (auto-detected)
  spawn.py --branch <branch>                            # explicit branch
  spawn.py --pr <num>                                   # explicit PR (fetch pull/N/head)
  spawn.py --name <short>  (--repo <n> | --cwd <path>)  # new branch (--repo) or workspace at path (--cwd)
  spawn.py --skill <name>  (--repo <n> | --cwd <path>)  # spawn workspace running a skill
  spawn.py --cwd <path>                                 # arbitrary dir (no repo, no branch)

Sources are strictly mutex: pick exactly one of
  {positional, --branch, --pr, --name, --skill} — or --cwd alone.

--name and --skill require an explicit location: --repo <n> or --cwd <path>.
  --name <s> --repo R  → new branch <prefix><s> in R, workspace short = s
  --name <s> --cwd P   → workspace at P (no branch), short = s
  --skill K  --repo R  → resolve skill (global first, repo fallback); cwd = R
  --skill K  --cwd P   → resolve skill (global only); cwd = P

--repo overrides cwd-based repo discovery for positional/--branch/--pr too.
--cwd may not combine with positional/--branch/--pr (those need a repo).

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
  - For positional/--branch/--pr without --repo: walk up from cwd to match a
    registered repo in ~/.config/cockpit/config.json (or register_cwd() if
    unmatched). --repo <n> bypasses discovery.
  - Worktree path: dirname(repo)/<name>, with -2/-3/... on collision.
  - --cwd <path> must exist (errors if not).
  - Idempotent: existing worktree+workspace for the branch -> attach, don't error.

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
from lib.config import discover_repo, find_repo_by_name, find_repo_by_nwo
from lib.daemon import kick_running
from lib.gh import fetch_pr_info, pr_for_branch, resolve_pr_branch
from lib.git import (
    branch_exists,
    collision_free,
    create_new_branch_worktree,
    create_worktree,
    slugify,
    worktree_for_branch,
)
from lib.prompts import claude_command
from lib.repos import repo_names


def _unknown_repo_msg(name: str) -> str:
    names = repo_names()
    if names:
        listed = ", ".join(names[:10])
        more = f" (+{len(names) - 10} more)" if len(names) > 10 else ""
        return (
            f"--repo {name!r}: no configured repo with that name. "
            f"Configured: {listed}{more}. Run /cockpit:repos for details."
        )
    return (
        f"--repo {name!r}: no configured repo with that name, and no repos "
        f"are configured. Run /cockpit:repos or /cockpit:new from inside a "
        f"git repo to auto-register."
    )


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


def detect_source(value: str) -> tuple[str, str, str | None]:
    """Classify positional into (mode, resolved_value, nwo_hint).

    `nwo_hint` is `<owner>/<repo>` when a full GitHub PR URL was parsed,
    else None. The caller uses it to route the spawn to the right
    configured repo when invoked from outside its tree.

    PR mode requires a `#` prefix (`#123`) or a full GitHub PR URL. A bare
    integer is treated as a branch name — use `#123` or `--pr 123` for PRs.
    Steps 3-5 (local/remote/new branch) resolved by create_worktree at
    worktree-creation time.
    """
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/(\d+)", value)
    if m:
        return "pr", m.group(2), m.group(1)
    if re.fullmatch(r"#\d+", value):
        return "pr", value.lstrip("#"), None
    return "branch", value, None


def select_repo(repo_name: str | None) -> dict:
    if repo_name:
        repo_cfg = find_repo_by_name(repo_name)
        if repo_cfg is None:
            raise ValueError(_unknown_repo_msg(repo_name))
        return repo_cfg
    repo_cfg = discover_repo()
    if repo_cfg is None:
        names = repo_names()
        hint = (
            f" Configured repos: {', '.join(names[:10])}"
            f"{' (+more)' if len(names) > 10 else ''}. Run /cockpit:repos."
            if names
            else ""
        )
        raise ValueError(
            "cannot determine repo from cwd; pass --repo <name> or run from "
            "inside a managed repo (register first with `cockpit add`)." + hint
        )
    return repo_cfg


def _bump_until_free(repo: Path, branch: str) -> str:
    """Append -2/-3/... to `branch` until it does not exist locally, remotely,
    or as a worktree. Used by `--name` to guarantee a fresh branch."""
    if not (branch_exists(repo, branch) or worktree_for_branch(repo, branch)):
        return branch
    i = 2
    while True:
        cand = f"{branch}-{i}"
        if not (branch_exists(repo, cand) or worktree_for_branch(repo, cand)):
            return cand
        i += 1


def resolve_worktree(
    branch: str | None,
    pr_num: str | None,
    repo_name: str | None,
    *,
    from_name: bool = False,
) -> tuple[Path, str, bool]:
    repo_cfg = select_repo(repo_name)
    repo = Path(repo_cfg["path"]).expanduser().resolve()
    branch_prefix = repo_cfg.get("branch_prefix", "")
    base = repo_cfg.get("default_base", "main")

    if pr_num and not branch:
        branch = resolve_pr_branch(pr_num, repo_dir=repo)
    if not branch:
        raise ValueError("need <branch> or --pr <num>")

    if from_name:
        if branch_prefix and "/" not in branch:
            branch = f"{branch_prefix}{branch}"
        prefixed = branch
        branch = _bump_until_free(repo, branch)
        if branch != prefixed:
            print(
                f"note: branch bumped to {branch} (requested name collided)",
                file=sys.stderr,
            )
        wt = collision_free(repo.parent / slugify(branch.rsplit("/", 1)[-1]))
        branch = create_new_branch_worktree(repo, branch, wt, base=base)
        return wt, branch, False

    existing = worktree_for_branch(repo, branch)
    if existing is None and branch_prefix and "/" not in branch:
        prefixed = f"{branch_prefix}{branch}"
        existing = worktree_for_branch(repo, prefixed)
        if existing is not None:
            branch = prefixed
    if existing is not None:
        return existing, branch, True

    wt = collision_free(repo.parent / slugify(branch.rsplit("/", 1)[-1]))
    branch = create_worktree(
        repo, branch, wt, base=base, pr_num=pr_num, branch_prefix=branch_prefix
    )
    return wt, branch, False


def resolve_skill(name: str, repo_name: str | None) -> tuple[Path, str]:
    """Locate a skill and return (workspace_cwd, claude_prompt).

    Skill-file lookup order (global always wins):
      1. ~/.claude/skills/<name>/skill.md
      2. <repo>/.claude/skills/<name>/skill.md (only when --repo was given)

    Workspace cwd precedence (caller may still override with --cwd):
      - Explicit --repo  → configured repo's path (even when the global skill wins)
      - Global skill, no --repo  → $HOME
    """
    rel = Path(".claude") / "skills" / name / "skill.md"

    repo_path: Path | None = None
    if repo_name:
        repo_cfg = find_repo_by_name(repo_name)
        if repo_cfg is None:
            raise ValueError(_unknown_repo_msg(repo_name))
        repo_path = Path(repo_cfg["path"]).expanduser().resolve()

    home = Path.home()
    if (home / rel).exists():
        cwd = repo_path or home
        return cwd, f"/{name}"

    if repo_path and (repo_path / rel).exists():
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

    if args.repo is not None and find_repo_by_name(args.repo) is None:
        print(f"ERROR: {_unknown_repo_msg(args.repo)}", file=sys.stderr)
        return 1

    cwd = args.cwd

    # Strict source mutex: at most one of {positional, --branch, --pr, --name, --skill}.
    # --cwd alone (no source) is a valid 6th mode.
    chosen = [
        n
        for n, v in [
            ("positional", args.positional),
            ("--branch", args.branch),
            ("--pr", args.pr),
            ("--name", args.name),
            ("--skill", args.skill),
        ]
        if v
    ]
    if len(chosen) > 1:
        print(
            "ERROR: at most one of positional, --branch, --pr, --name, --skill "
            f"may be given (got: {', '.join(chosen)})",
            file=sys.stderr,
        )
        return 1
    if not chosen and not cwd:
        print(
            "ERROR: one of positional, --branch, --pr, --name, --skill, "
            "or --cwd is required",
            file=sys.stderr,
        )
        return 1
    if cwd and (args.positional or args.branch or args.pr):
        print(
            "ERROR: --cwd cannot combine with positional/--branch/--pr "
            "(those resolve a repo; use --repo to target one)",
            file=sys.stderr,
        )
        return 1
    if (args.name or args.skill) and not (args.repo or cwd):
        print(
            "ERROR: --name and --skill require --repo <name> or --cwd <path>",
            file=sys.stderr,
        )
        return 1
    if cwd:
        cwd_path = Path(cwd).expanduser().resolve()
        if not cwd_path.exists():
            print(f"ERROR: --cwd {cwd!r}: path does not exist", file=sys.stderr)
            return 1

    branch = args.branch
    pr_num = args.pr
    short = args.name
    skill = args.skill
    from_name = False

    if args.positional:
        mode, value, nwo_hint = detect_source(args.positional)
        if mode == "pr":
            pr_num = value
        else:
            branch = value
        if nwo_hint and not args.repo:
            match = find_repo_by_nwo(nwo_hint)
            if match is not None:
                args.repo = match["name"]
            else:
                print(
                    f"note: URL points to {nwo_hint} but no configured repo matches; "
                    f"falling back to cwd-based discovery",
                    file=sys.stderr,
                )

    # --name with --repo (no --cwd) → new prefixed branch (from_name path).
    # --name with --cwd → workspace-at-path, no branch (handled in cwd dispatch).
    if args.name and args.repo and not cwd:
        branch = args.name
        from_name = True

    prompt: str | None = args.claude_prompt

    if skill:
        try:
            wt, skill_prompt = resolve_skill(skill, args.repo)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        if cwd:
            wt = Path(cwd).expanduser().resolve()
        if not short:
            short = slugify(skill)
        if prompt is None:
            prompt = skill_prompt
        attached_wt = True
        branch_display = None
    elif cwd and not branch and not pr_num:
        wt = Path(cwd).expanduser().resolve()
        if not short:
            short = slugify(wt.name)
        attached_wt = True
        branch_display = None
    else:
        try:
            wt, branch, attached_wt = resolve_worktree(
                branch, pr_num, args.repo, from_name=from_name
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
    kick_running(quiet=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
