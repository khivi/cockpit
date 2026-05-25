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

Positional detection (7 steps):
  1. GitHub PR URL (https://github.com/.../pull/N) → PR mode
  2. #-prefixed PR number (#N)                       → PR mode
                                                       (bare N is a branch — see below)
  3. Linear ID ([A-Z]{2,6}-\\d+, case-insensitive)   → linear mode
  4. Slack archives URL                              → slack mode
  5. Local branch (refs/heads/<branch> exists)           → checkout
  6. Remote branch (ls-remote origin <branch> matches)   → fetch + checkout
  7. New branch (neither local nor remote)               → create from default_base

  After branch resolution (steps 5-7), gh is queried for an open PR on
  the head ref; if found, the PR info is printed and the plan-only prompt
  is auto-generated (unless --claude-prompt overrides).

  Linear/Slack modes create a fresh branch under `branch_prefix` (named
  `<id-lower>` / `slack-<channel>-<ts>`) and seed a plan-only prompt that
  instructs Claude to fetch the ticket/thread via its MCP connector on the
  first turn. Cockpit does not call the Linear/Slack APIs itself — if the
  relevant MCP is not connected, the spawned Claude reports that and stops.

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

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.lib.cmux import (
    cmux,
    require_workspace_binary,
    workspace_names,
)  # noqa: E402
from scripts.lib.config import (
    discover_repo,
    find_repo_by_name,
    find_repo_by_nwo,
    use_linear as cfg_use_linear,
)  # noqa: E402
from scripts.lib.daemon_signal import kick_running  # noqa: E402
from scripts.lib.gh import fetch_pr_info, pr_for_branch, resolve_pr_branch  # noqa: E402
from scripts.lib.git import (  # noqa: E402
    branch_exists,
    collision_free,
    create_new_branch_worktree,
    create_worktree,
    slugify,
    worktree_for_branch,
)
from scripts.lib.linear import LINEAR_RE_CI, linear_mcp_available  # noqa: E402
from scripts.lib.prompts import claude_command  # noqa: E402
from scripts.lib.repos import repo_names  # noqa: E402
from scripts.lib.slack import SLACK_URL_RE, parse_url as parse_slack_url  # noqa: E402


def _die(msg: str, code: int = 1) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return code


def _format_configured_repos(names: list[str]) -> str:
    """`"<n1>, <n2> (+K more)"` from a list of repo names, capped at 10. Empty if no names."""
    if not names:
        return ""
    listed = ", ".join(names[:10])
    more = f" (+{len(names) - 10} more)" if len(names) > 10 else ""
    return f"{listed}{more}"


def _unknown_repo_msg(name: str) -> str:
    listed = _format_configured_repos(repo_names())
    if listed:
        return (
            f"--repo {name!r}: no configured repo with that name. "
            f"Configured: {listed}. Run /cockpit:repos for details."
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

    Modes:
      - `pr`     : GitHub PR URL or `#N`. Bare integers stay `branch`.
      - `linear` : whole positional matches `[A-Z]{2,6}-\\d+` (case-insensitive).
                   Normalised to uppercase in `value`.
      - `slack`  : whole positional is a Slack archives URL.
      - `branch` : anything else; local/remote/new resolved by create_worktree.
    """
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/(\d+)", value)
    if m:
        return "pr", m.group(2), m.group(1)
    if re.fullmatch(r"#\d+", value):
        return "pr", value.lstrip("#"), None
    if SLACK_URL_RE.match(value):
        return "slack", value, None
    if LINEAR_RE_CI.fullmatch(value):
        return "linear", value.upper(), None
    return "branch", value, None


def _slack_branch_name(url: str) -> str:
    """Deterministic `slack-<channel-lower>-<ts-dash>` from a Slack archives URL.

    Cockpit deliberately avoids fetching the thread body to pick a prettier
    name: that's Claude's job on the first turn, via the Slack MCP. The
    branch name only needs to be unique and re-derivable — re-spawning on
    the same URL must hit the same branch (idempotency).
    """
    parsed = parse_slack_url(url)
    if parsed is None:
        return slugify(url) or "slack-thread"
    ch, ts = parsed
    return f"slack-{ch.lower()}-{ts.replace('.', '-')}"


_PLAN_TAIL = [
    "",
    "**HARD RULE — PLAN ONLY, NO CODE THIS TURN**:",
    "- DO NOT edit files, write code, run tests, or commit anything.",
    "- You MAY use Read, Grep, Glob for context (re-fetch the source where relevant).",
    "- Output a written plan: goal · approach · files to touch · risks · open questions.",
    "- Ask clarifying questions if the task is ambiguous.",
    "- Wait for the user to approve or refine before implementing.",
    "",
    "Begin by fetching the source above, then write the plan.",
]


def _linear_prompt(branch: str, identifier: str) -> str:
    """First-turn prompt that delegates Linear ticket fetch to the Linear MCP
    and then renames the branch to include the ticket title slug.

    Cockpit does not call the Linear API itself: spawn creates the worktree
    on `<prefix><id-lower>` (e.g. `khivi/pe-1234`) and Claude does both the
    ticket fetch and the post-fetch `git branch -m` to `<prefix><id-lower>-<title-slug>`.
    Workspace name + worktree directory stay on the original short slug;
    cockpit's reconciliation re-reads `git worktree list` each cycle, so the
    rename surfaces in `/cockpit:list` without further action.
    """
    lines = [
        f"You are starting a fresh task in a new worktree on branch `{branch}`.",
        "",
        f"**Source**: Linear ticket {identifier}",
        "",
        "**Step 1 (REQUIRED)** — Fetch the ticket via the Linear MCP:",
        f"- Use the Linear MCP tool to read issue `{identifier}` (title, description, comments).",
        "- If the Linear MCP is not connected, STOP. Report to the user that the "
        "Linear connector is required and exit without writing a plan. Do not "
        "fall back to guessing from the ticket id alone.",
        "",
        "**Step 2 (REQUIRED)** — Derive a slug and rename the branch:",
        "- Derive `<slug>` from the ticket title: lowercase, non-alphanumerics → `-`, "
        "trim leading/trailing `-`, cap at 30 chars. Use the SAME `<slug>` in step 3.",
        "- Read the current branch: `CUR=$(git branch --show-current)`.",
        '- Run: `git branch -m "$CUR" "$CUR-<slug>"` (append `-<slug>` to whatever '
        "the current branch is — cockpit may have bumped it to `-2`/`-3` to avoid a collision).",
        "- Verify with `git branch --show-current` — it should now end with `-<slug>`.",
        "- If the rename fails (target already exists, etc.), keep the original "
        "branch and note it in your plan.",
        "",
        "**Step 3 (REQUIRED)** — Rename the cmux workspace to drop the `<id>`-style placeholder:",
        "- The workspace was created with cockpit's placeholder name (e.g. `pe-1234`). "
        "Replace it with the SAME `<slug>` from step 2 — no id prefix.",
        '- Run: `cmux workspace-action --action rename --title "<slug>"`. '
        "Defaults to the current workspace via `$CMUX_WORKSPACE_ID` (always set "
        "inside a cmux-spawned shell).",
        "- If `$CMUX_WORKSPACE_ID` is unset for any reason, run `cmux identify` "
        "first to discover the workspace ref, then pass `--workspace <ref>` explicitly.",
        "- Cockpit's next reconcile cycle reads `cmux list-workspaces`, so the renamed "
        "workspace surfaces in `/cockpit:list` automatically.",
        "- Do not push or change anything else in this step.",
    ]
    return "\n".join(lines + _PLAN_TAIL)


def _slack_prompt(branch: str, url: str) -> str:
    """First-turn prompt that delegates Slack thread fetch to the Slack MCP.

    Same contract as `_linear_prompt`: Claude reads the thread via MCP; no
    cockpit-side API. The `(channel, ts)` pair is parsed out only so Claude
    has them ready to pass to the MCP tool.
    """
    parsed = parse_slack_url(url)
    channel_ts = (
        f"channel `{parsed[0]}`, ts `{parsed[1]}`" if parsed else "(unparsed URL)"
    )
    lines = [
        f"You are starting a fresh task in a new worktree on branch `{branch}`.",
        "",
        f"**Source**: Slack thread — {channel_ts}",
        f"**Permalink**: {url}",
        "",
        "**First step (REQUIRED)**: Fetch the thread via the Slack MCP before planning.",
        "- Use the Slack MCP tool to read the full thread (root message + replies).",
        "- If the Slack MCP is not connected, STOP. Report to the user that the "
        "Slack connector is required and exit without writing a plan. Do not "
        "fall back to guessing from the URL alone.",
    ]
    return "\n".join(lines + _PLAN_TAIL)


def select_repo(repo_name: str | None) -> dict:
    if repo_name:
        repo_cfg = find_repo_by_name(repo_name)
        if repo_cfg is None:
            raise ValueError(_unknown_repo_msg(repo_name))
        return repo_cfg
    repo_cfg = discover_repo()
    if repo_cfg is None:
        listed = _format_configured_repos(repo_names())
        hint = f" Configured repos: {listed}. Run /cockpit:repos." if listed else ""
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
        return _die(_unknown_repo_msg(args.repo))

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
        return _die(
            "at most one of positional, --branch, --pr, --name, --skill "
            f"may be given (got: {', '.join(chosen)})"
        )
    if not chosen and not cwd:
        return _die(
            "one of positional, --branch, --pr, --name, --skill, or --cwd is required"
        )
    if cwd and (args.positional or args.branch or args.pr):
        return _die(
            "--cwd cannot combine with positional/--branch/--pr "
            "(those resolve a repo; use --repo to target one)"
        )
    if (args.name or args.skill) and not (args.repo or cwd):
        return _die("--name and --skill require --repo <name> or --cwd <path>")
    if cwd:
        cwd_path = Path(cwd).expanduser().resolve()
        if not cwd_path.exists():
            return _die(f"--cwd {cwd!r}: path does not exist")

    branch = args.branch
    pr_num = args.pr
    short = args.name
    skill = args.skill
    from_name = False

    prompt: str | None = args.claude_prompt
    seeded_prompt: str | None = None  # holds the linear/slack MCP-instructing prompt

    if args.positional:
        mode, value, nwo_hint = detect_source(args.positional)
        if mode == "pr":
            pr_num = value
        elif mode == "linear":
            branch = value.lower()
            from_name = True
            if cfg_use_linear():
                mcp = linear_mcp_available()
                if mcp is False:
                    print(
                        f"cockpit: Linear MCP not detected via 'claude mcp list'; "
                        f"falling back to plain branch mode for {value}",
                        file=sys.stderr,
                    )
                else:
                    seeded_prompt = _linear_prompt(branch, value)
        elif mode == "slack":
            branch = _slack_branch_name(value)
            from_name = True
            seeded_prompt = _slack_prompt(branch, value)
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

    # --claude-prompt wins over the linear/slack-seeded prompt; otherwise the
    # seeded one wins over the generic plan-only prompt added below.
    if prompt is None:
        prompt = seeded_prompt

    if skill:
        try:
            wt, skill_prompt = resolve_skill(skill, args.repo)
        except ValueError as e:
            return _die(str(e))
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
            return _die(str(e), code=2)
        except ValueError as e:
            return _die(str(e))

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
    require_workspace_binary()
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
