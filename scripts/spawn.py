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
  -- <text...>            Trailing text after `--` is appended to claude's
                          first-message prompt. Useful for adding extra
                          instructions to the auto-generated plan/skill/Linear
                          prompts (e.g. `spawn.py PE-1234 -- focus on the
                          retry loop in fetch_pr`).

Positional detection (6 steps):
  1. GitHub PR URL (https://github.com/.../pull/N) → PR mode
  2. #-prefixed PR number (#N)                       → PR mode
                                                       (bare N is a branch — see below)
  3. Linear ID ([A-Z]{2,6}-\\d+, case-insensitive)   → linear mode
  4. Local branch (refs/heads/<branch> exists)           → checkout
  5. Remote branch (ls-remote origin <branch> matches)   → fetch + checkout
  6. New branch (neither local nor remote)               → create from default_base

  After branch resolution (steps 4-6), gh is queried for an open PR on
  the head ref; if found, the PR info is printed and the plan-only prompt
  is auto-generated. Trailing `-- <text>` is appended to whatever prompt
  was selected.

  Plan-only is seeded only when there is something to study first: a PR, a
  Linear ticket, inherited `--context`, or an explicit `-- <text>` task. A
  blank worktree (`--name <name> --repo <repo>` / a bare new branch with no
  open PR and none of the above) is ready to work on and gets NO seeded
  prompt — any configured `prompt_prefix` still rides via `claude_command`,
  and the user states the task in the live session.

  Linear mode creates a fresh branch `<branch_prefix><id-lower>` (e.g.
  `khivi/pe-1234`). With `use_linear: true` and the Linear MCP detected
  via `claude mcp list`, cockpit seeds a plan-only prompt that instructs
  Claude to fetch the ticket via the Linear MCP and rename the branch +
  workspace to include the ticket title slug. Otherwise the workspace
  starts with the generic plan prompt.

  With `use_linear: true` and no `--repo`, cockpit also routes the spawn
  to the repo whose per-repo `linear_keys` list contains the Linear key
  prefix (e.g. `PE-1234` → the repo declaring `"linear_keys": ["PE"]`).
  A unique match wins; zero matches falls back to cwd discovery; multiple
  matches print a note and also fall back. `--repo <name>` always wins.

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

from scripts.lib.cache import set_pr_keep  # noqa: E402
from scripts.lib.cmux import (
    cmux,
    require_workspace_binary,
    spawn_workspace,
    workspace_names,
)  # noqa: E402
from scripts.lib.config import (
    discover_repo,
    find_repo_by_name,
    find_repo_by_nwo,
    find_repos_by_linear_key,
)  # noqa: E402
from scripts.lib.config import (
    use_linear as cfg_use_linear,
)
from scripts.lib.daemon_signal import kick_running  # noqa: E402
from scripts.lib.gh import (  # noqa: E402
    fetch_pr_info,
    fetch_run_info,
    pr_for_branch,
    repo_nwo,
    resolve_pr_branch,
)
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
    p.add_argument("--keep", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--review",
        action="store_true",
        help="seed the worktree's first turn with `/review` instead of the "
        "plan-only prompt (used by the daemon's per-repo `review_prs`)",
    )
    p.add_argument(
        "--context-text",
        help="caller-supplied summary of the current session, injected into the "
        "seeded first-turn prompt under a 'Caller session context' heading. The "
        "/cockpit:new skill fills this from `--context` by summarizing the live "
        "session before invoking spawn.py.",
    )
    raw = sys.argv[1:]
    if "--" in raw:
        idx = raw.index("--")
        pre, post = raw[:idx], raw[idx + 1 :]
        addendum = " ".join(post).strip() or None
    else:
        pre, addendum = raw, None
    args = p.parse_args(pre)
    args.claude_addendum = addendum
    return args


def detect_source(value: str) -> tuple[str, str, str | None]:
    """Classify positional into (mode, resolved_value, nwo_hint).

    `nwo_hint` is `<owner>/<repo>` when a full GitHub URL was parsed,
    else None. The caller uses it to route the spawn to the right
    configured repo when invoked from outside its tree.

    Modes:
      - `pr`      : GitHub PR URL or `#N`. Bare integers stay `branch`.
      - `actions` : GitHub Actions run URL (optionally job-scoped).
                    `value` is `<run_id>` or `<run_id>:<job_id>`.
      - `linear`  : whole positional matches `[A-Z]{2,6}-\\d+` (case-insensitive).
                    Normalised to uppercase in `value`.
      - `branch`  : anything else; local/remote/new resolved by create_worktree.
    """
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/(\d+)", value)
    if m:
        return "pr", m.group(2), m.group(1)
    m = re.match(
        r"https?://github\.com/([^/]+/[^/]+)/actions/runs/(\d+)"
        r"(?:/attempts/\d+)?(?:/job/(\d+))?",
        value,
    )
    if m:
        run_id, job_id = m.group(2), m.group(3)
        return "actions", f"{run_id}:{job_id}" if job_id else run_id, m.group(1)
    if re.fullmatch(r"#\d+", value):
        return "pr", value.lstrip("#"), None
    if LINEAR_RE_CI.fullmatch(value):
        return "linear", value.upper(), None
    return "branch", value, None


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


def _review_prompt(branch: str, pr_info: dict | None = None) -> str:
    """First-turn prompt for an auto-spawned review worktree (per-repo
    `review_prs`).

    Leads with the literal `/review` slash command so Claude Code runs the
    built-in PR review against the PR checked out on this branch; the PR
    context block follows for the human reading the transcript. Mirrors the
    `--skill` path, which also delivers a bare slash command as the first turn.
    """
    lines = ["/review", ""]
    if pr_info:
        author = (pr_info.get("author") or {}).get("login", "unknown")
        number = pr_info["number"]
        title = pr_info.get("title") or f"PR #{number}"
        lines += [
            f"Reviewing PR #{number} by @{author} — {title}",
            f"branch: {branch}",
            pr_info.get("url", ""),
        ]
    else:
        lines.append(f"Reviewing the open PR on branch `{branch}`.")
    lines += [
        "",
        "Report findings. Do not post review comments unless explicitly authorized.",
    ]
    return "\n".join(lines)


def _actions_short_name(run_info: dict, job_id: str | None) -> str:
    """Synthesize a workspace short name for an Actions investigation worktree.

    Priority:
      1. Job-scoped → `ci-<job-name>`
      2. Run-scoped with displayTitle → `ci-<workflow>-<title>`
      3. Fallback → `ci-<workflow>-<short-sha>` or `ci-<workflow>-<run-id>`

    Always returns a non-empty slug (`slugify` caps at 30 chars).
    """
    workflow = run_info.get("workflowName") or "ci"
    if job_id:
        for j in run_info.get("jobs") or []:
            if str(j.get("databaseId")) == job_id:
                jname = j.get("name") or ""
                if jname:
                    return slugify(f"ci-{jname}") or f"ci-job-{job_id}"
                break
    title = (run_info.get("displayTitle") or "").strip()
    if title:
        slug = slugify(f"ci-{workflow}-{title}")
        if slug:
            return slug
    sha = (run_info.get("headSha") or "")[:7]
    if sha:
        slug = slugify(f"ci-{workflow}-{sha}")
        if slug:
            return slug
    run_id = run_info.get("databaseId") or "run"
    return slugify(f"ci-{workflow}-{run_id}") or f"ci-run-{run_id}"


def _actions_prompt(
    branch: str, run_info: dict, job_id: str | None, pr_info: dict | None = None
) -> str:
    """First-turn prompt for a GitHub Actions run URL.

    Directs Claude to fetch only the failed-step logs via `gh run view
    --log-failed` (`--job <id>` when a specific job was linked), identify
    the root cause, and propose a plan. Logs are not embedded in the
    prompt — they can be huge.
    """
    run_id = str(run_info.get("databaseId") or "")
    workflow = run_info.get("workflowName") or "workflow"
    conclusion = run_info.get("conclusion") or run_info.get("status") or "unknown"
    run_url = run_info.get("url") or ""
    job_name: str | None = None
    if job_id:
        for j in run_info.get("jobs") or []:
            if str(j.get("databaseId")) == job_id:
                job_name = j.get("name")
                break

    if job_id:
        source = f"Actions job `{job_name or job_id}` in run `{workflow}` #{run_id}"
        log_cmd = f"gh run view {run_id} --log-failed --job {job_id}"
    else:
        source = f"Actions run `{workflow}` #{run_id}"
        log_cmd = f"gh run view {run_id} --log-failed"

    head_branch = run_info.get("headBranch") or ""
    lines = [
        f"You are starting a fresh task in a new worktree on branch `{branch}`.",
        "",
        f"**Source**: {source}",
        f"**Conclusion**: {conclusion}",
        f"**Head branch (where CI ran)**: `{head_branch}`",
        f"**Run URL**: {run_url}",
    ]
    if pr_info:
        author = (pr_info.get("author") or {}).get("login", "unknown")
        lines.append(
            f"**Related PR**: #{pr_info['number']} by @{author} — "
            f"{pr_info.get('title', '')} ({pr_info.get('url', '')})"
        )
    lines += [
        "",
        "**Step 1 (REQUIRED)** — Fetch the failed-step logs:",
        f"- Run: `{log_cmd}`. This pulls only failing steps; full logs "
        "(`--log`) are usually too large for context.",
        "- If the output is still large, pipe through `tail -n 200` or `rg` "
        "for the actual error lines.",
        "",
        "**Step 2 (REQUIRED)** — Identify the root cause:",
        "- Locate the failing step and its command in the workflow YAML "
        "under `.github/workflows/`.",
        "- Reproduce locally where possible (run the same command against "
        "the current branch).",
    ]
    return "\n".join(lines + _PLAN_TAIL)


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
    if args.review and (args.skill or (cwd and not chosen)):
        return _die(
            "--review needs a PR or branch source (positional/--branch/--pr); "
            "it cannot combine with --skill or a bare --cwd"
        )
    if cwd:
        cwd_path = Path(cwd).expanduser().resolve()
        if not cwd_path.exists():
            return _die(f"--cwd {cwd!r}: path does not exist")

    branch = args.branch
    pr_num = args.pr
    short = args.name
    skill = args.skill
    from_name = False
    is_linear = False  # positional classified as a Linear key (any context → plan)
    pr_explicit = (
        False  # set True only when PR is the explicit input (not branch auto-detect)
    )

    prompt: str | None = None
    seeded_prompt: str | None = None  # holds the linear MCP-instructing prompt
    actions_run_info: dict | None = None
    actions_job_id: str | None = None
    actions_head_branch: str | None = None  # original headBranch from Actions run

    if args.positional:
        mode, value, nwo_hint = detect_source(args.positional)
        if mode == "pr":
            pr_num = value
        elif mode == "actions":
            run_id, _, job_id = value.partition(":")
            actions_job_id = job_id or None
            try:
                actions_run_info = fetch_run_info(run_id, nwo=nwo_hint)
            except RuntimeError as e:
                return _die(str(e))
            head_branch = actions_run_info.get("headBranch") or ""
            if not head_branch:
                return _die(
                    f"Actions run {run_id} has no headBranch — cannot resolve a worktree"
                )
            actions_head_branch = head_branch
            # Always synthesize a fresh investigation branch. Reusing the
            # run's headBranch attaches to the existing worktree (often the
            # main repo checkout when CI failed on master after a merge),
            # which is the bug this branch fixes.
            branch = _actions_short_name(actions_run_info, actions_job_id)
            from_name = True
        elif mode == "linear":
            branch = value.lower()
            from_name = True
            is_linear = True
            if cfg_use_linear() and not args.repo:
                matches = find_repos_by_linear_key(value)
                if len(matches) == 1:
                    args.repo = matches[0]["name"]
                elif len(matches) > 1:
                    names = ", ".join(m["name"] for m in matches)
                    print(
                        f"note: Linear key {value!r} matches multiple repos "
                        f"({names}); falling back to cwd-based discovery. "
                        f"Pass --repo <name> to disambiguate.",
                        file=sys.stderr,
                    )
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

        pr_explicit = bool(pr_num)  # True when PR was the input, not auto-detected
        pr_info: dict | None = None
        if pr_num:
            try:
                pr_info = fetch_pr_info(pr_num, wt)
            except Exception:
                pr_info = None
        else:
            # Actions mode synthesizes a fresh `ci-...` branch, so query the
            # PR against the run's original headBranch instead.
            pr_lookup_branch = actions_head_branch or branch
            pr_info = pr_for_branch(pr_lookup_branch, wt)
            if pr_info is not None:
                pr_num = str(pr_info["number"])
                author = (pr_info.get("author") or {}).get("login", "unknown")
                print(
                    f"note: open PR #{pr_num} exists for branch "
                    f"{pr_lookup_branch!r}: {pr_info.get('title', '')} by "
                    f"@{author} ({pr_info.get('url', '')})",
                    file=sys.stderr,
                )

        if actions_run_info is not None:
            prompt = _actions_prompt(branch, actions_run_info, actions_job_id, pr_info)
        elif args.review:
            prompt = _review_prompt(branch, pr_info)
        elif prompt is None and (
            pr_info or is_linear or args.context_text or args.claude_addendum
        ):
            # Plan-only fires only when there's something to study first: a PR,
            # a Linear ticket, inherited `--context`, or an explicit `-- <text>`
            # task. A blank worktree (`/cockpit:new <name> --repo <repo>` with
            # none of those) is ready to work on, so it gets no seeded guidance —
            # any configured `prompt_prefix` (e.g. a session-setup skill) still
            # rides via `claude_command()`, and the user states the task live.
            prompt = _plan_only_prompt(branch, pr_info)

    if args.claude_addendum:
        prompt = (
            f"{prompt}\n\n{args.claude_addendum}" if prompt else args.claude_addendum
        )

    if args.context_text:
        ctx = f"## Caller session context\n\n{args.context_text}"
        prompt = f"{prompt}\n\n{ctx}" if prompt else ctx

    ws_name = short
    require_workspace_binary()
    ws_refs = workspace_names()  # {ref: name}
    existing_ref = next((ref for ref, n in ws_refs.items() if n == ws_name), None)
    attached_ws = existing_ref is not None
    if existing_ref is None:
        spawn_workspace(ws_name, wt, claude_command(prompt))
    elif prompt:
        # The worktree's Claude is already running, so the prompt can't ride in
        # on `--command`. Deliver it into the live session: type the text into
        # the workspace's terminal surface, then submit with Enter. Without
        # this, re-spawning onto an existing workspace silently drops the
        # PR-action / plan / `-- <text>` / context prompt.
        cmux("send", "--workspace", existing_ref, prompt)
        cmux("send-key", "--workspace", existing_ref, "enter")
        print(
            f"note: delivered prompt to existing workspace {ws_name}",
            file=sys.stderr,
        )

    if attached_wt and attached_ws:
        prefix = f"attached existing workspace {ws_name} at {wt}"
    else:
        prefix = f"workspace {ws_name} spawned at {wt}"
    suffix = f" on {branch_display}" if branch_display else " (no worktree)"
    print(f"{prefix}{suffix}")

    if args.keep and pr_explicit and pr_num:
        try:
            owner, name = repo_nwo(wt)
            set_pr_keep(f"{owner}/{name}", pr_num)
        except Exception:
            pass

    kick_running(quiet=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
