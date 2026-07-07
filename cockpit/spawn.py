"""Create worktree (sibling of main repo) + spawn cmux workspace with claude pre-running.

Usage:
  spawn.py <branch|PR|github-url>                       # positional (auto-detected)
  spawn.py --branch <branch>                            # explicit branch
  spawn.py --pr <num>                                   # explicit PR (fetch pull/N/head)
  spawn.py --name <short>  (--repo <n> | --cwd <path>)  # new branch (--repo) or workspace at path (--cwd)
  spawn.py --skill <name>  (--repo <n> | --cwd <path>)  # spawn workspace running a skill
  spawn.py --cwd <path>                                 # arbitrary dir (no repo, no branch)
  spawn.py                                              # bare: register cwd's git repo (in_place) + in-place workspace

Sources are strictly mutex: pick exactly one of
  {positional, --branch, --pr, --name, --skill} — or --cwd alone, or nothing
  (bare: in-place workspace on the cwd repo, see below).

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

Positional detection:
  1. GitHub PR URL (https://github.com/.../pull/N) → PR mode
  2. GitHub Actions run URL                          → actions mode
  3. Slack message/thread permalink                  → slack mode (codename branch)
  4. #-prefixed PR number (#N)                       → PR mode
                                                       (bare N is a branch — see below)
  5. Linear ID ([A-Z]{2,6}-\\d+, case-insensitive)   → linear mode
  6. Local branch (refs/heads/<branch> exists)           → checkout
  7. Remote branch (ls-remote origin <branch> matches)   → fetch + checkout
  8. New branch (neither local nor remote)               → create from default_base

  Slack mode synthesizes a deterministic codename branch `<branch_prefix><adj>-<noun>`
  (e.g. `khivi/cosmic-otter`) from the thread's stable identity, then — with
  `use_slack: true` and the Slack MCP detected — seeds a prompt instructing
  Claude to read the thread via the Slack MCP and rename the branch + workspace
  to append a topic slug. The thread URL is always seeded as context regardless
  of `use_slack`.

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
  `khivi/pe-1234`). With `tickets: linear` and the Linear MCP detected
  via `claude mcp list`, cockpit seeds a plan-only prompt that instructs
  Claude to fetch the ticket via the Linear MCP and rename the branch +
  workspace to include the ticket title slug. Otherwise the workspace
  starts with the generic plan prompt.

  With `tickets: linear` and no `--repo`, cockpit also routes the spawn
  to the repo whose per-repo `linear_keys` list contains the Linear key
  prefix (e.g. `PE-1234` → the repo declaring `"linear_keys": ["PE"]`).
  A unique match wins; zero matches falls back to cwd discovery; multiple
  matches print a note and also fall back. `--repo <name>` always wins.

  GitHub-issue mode (`tickets: github`) creates a fresh branch `issue-<N>`
  from an issue URL or the `i#N` / `gh#N` shorthand, then seeds a plan-only
  prompt instructing Claude to read the issue via `gh issue view` and rename
  the branch + workspace to include the issue title slug. The issue URL's
  `owner/repo` routes to the matching configured repo.

Behaviour:
  - For positional/--branch/--pr without --repo: walk up from cwd to match a
    registered repo in ~/.config/cockpit/config.json; an unmatched repo errors
    (pass --repo, or run bare `cockpit new` to register it). --repo <n> bypasses
    discovery.
  - Bare (no source, no --cwd, no --repo): `register_cwd(in_place=True)` appends
    the cwd's git repo to config.json (marked `in_place: true`, so the daemon
    never auto-spawns worktrees for it) and opens an in-place workspace on the
    current branch — no worktree. Errors (exit 1) if cwd is not a git repo.
  - Worktree path: dirname(repo)/<name>, with -2/-3/... on collision.
  - --cwd <path> must exist (errors if not).
  - Idempotent: existing worktree+workspace for the branch -> attach, don't error.
    An already-registered repo is reused as-is by the bare path (not re-flagged).

Exit codes:
  0 = ok (created or attached)
  1 = usage / config error (incl. bare run outside a git repo)
  2 = worktree resolution failed (no managed repo for the source)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from cockpit.lib.cmux import (
    cmux,
    deliver_followup,
    require_workspace_binary,
    spawn_workspace,
    workspace_cwds,
    workspace_names,
)
from cockpit.lib.codename import codename
from cockpit.lib.config import (
    REVIEW_COMMAND_DEFAULT,
    discover_repo,
    find_repo_by_name,
    find_repo_by_nwo,
    find_repos_by_linear_key,
    github_start_label,
)
from cockpit.lib.config import (
    tickets as cfg_tickets,
)
from cockpit.lib.config import (
    use_slack as cfg_use_slack,
)
from cockpit.lib.daemon_signal import kick_running
from cockpit.lib.gh import (
    fetch_pr_info,
    fetch_run_info,
    pr_for_branch,
    resolve_pr_branch,
)
from cockpit.lib.git import (
    branch_exists,
    branch_label,
    collision_free,
    create_new_branch_worktree,
    create_worktree,
    slugify,
    worktree_for_branch,
)
from cockpit.lib.github_issues import (
    GITHUB_ISSUE_SHORTHAND_RE,
    GITHUB_ISSUE_URL_RE,
    add_label,
)
from cockpit.lib.linear import LINEAR_RE_CI, linear_mcp_available
from cockpit.lib.prompts import claude_command, split_prompt_prefix
from cockpit.lib.registry import register_cwd
from cockpit.lib.repos import repo_names
from cockpit.lib.slack import SLACK_URL_RE, slack_seed
from cockpit.lib.templates import render
from cockpit.lib.trello import TRELLO_CARD_URL_RE, trello_seed


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
            f"--repo {name!r}: no configured repo with that name. Configured: {listed}."
        )
    return (
        f"--repo {name!r}: no configured repo with that name, and no repos "
        f"are configured. Run /cockpit:new from inside a "
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
    p.add_argument(
        "--review",
        action="store_true",
        help="seed the worktree's first turn with a review slash command "
        "instead of the plan-only prompt (used by the daemon's per-repo "
        "`review_prs`)",
    )
    p.add_argument(
        "--review-command",
        default=REVIEW_COMMAND_DEFAULT,
        help="the review slash command seeded under --review (default "
        f"`{REVIEW_COMMAND_DEFAULT}`); the daemon passes the per-repo "
        "`review_command`, e.g. `/review` or `/pr-review`",
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
      - `gh-issue`: GitHub issue URL, or the `i#N` / `gh#N` shorthand. `value`
                    is the issue number; `nwo_hint` is `<owner>/<repo>` for the
                    URL form (the shorthand carries no repo, routed via --repo/cwd).
                    A bare `#N` stays `pr` (PRs and issues share a number space).
      - `actions` : GitHub Actions run URL (optionally job-scoped).
                    `value` is `<run_id>` or `<run_id>:<job_id>`.
      - `slack`   : Slack message/thread permalink. `value` is the URL verbatim
                    (the spawned Claude reads the thread via the Slack MCP).
      - `trello`  : Trello card URL (`trello.com/c/<shortLink>`). `value` is the
                    URL verbatim (the spawned Claude reads the card via the
                    Trello MCP; the branch is a codename, like `slack`).
      - `linear`  : whole positional matches `[A-Z]{2,6}-\\d+` (case-insensitive).
                    Normalised to uppercase in `value`.
      - `branch`  : anything else; local/remote/new resolved by create_worktree.
    """
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/(\d+)", value)
    if m:
        return "pr", m.group(2), m.group(1)
    m = GITHUB_ISSUE_URL_RE.fullmatch(value)
    if m:
        return "gh-issue", m.group(2), m.group(1)
    m = re.match(
        r"https?://github\.com/([^/]+/[^/]+)/actions/runs/(\d+)"
        r"(?:/attempts/\d+)?(?:/job/(\d+))?",
        value,
    )
    if m:
        run_id, job_id = m.group(2), m.group(3)
        return "actions", f"{run_id}:{job_id}" if job_id else run_id, m.group(1)
    if SLACK_URL_RE.match(value):
        return "slack", value, None
    if TRELLO_CARD_URL_RE.match(value):
        return "trello", value, None
    m = GITHUB_ISSUE_SHORTHAND_RE.fullmatch(value)
    if m:
        return "gh-issue", m.group(1), None
    if re.fullmatch(r"#\d+", value):
        return "pr", value.lstrip("#"), None
    if LINEAR_RE_CI.fullmatch(value):
        return "linear", value.upper(), None
    return "branch", value, None


def _pr_author(pr_info: dict) -> str:
    """Login of a PR's author, or "unknown" when the author object is null/absent.

    `gh`'s PR JSON can carry `author: null` (deleted account), so guard the
    nested lookup rather than assuming a dict.
    """
    return str((pr_info.get("author") or {}).get("login", "unknown"))


def _pr_fields(pr_info: dict) -> tuple[str, int, str]:
    """`(author, number, title)` from a PR payload, with `title` falling back to
    `PR #<n>`. The shared unpack for the plan-only + review context blocks.

    (`_actions_prompt` keeps its own title handling — it wants an empty default,
    not the `PR #<n>` one — so it reads `author`/`number` directly.)
    """
    number = pr_info["number"]
    title = pr_info.get("title") or f"PR #{number}"
    return _pr_author(pr_info), number, title


def _scenario_prompt(name: str, **fields: object) -> str:
    """Render a source-fetch first-turn template, auto-filling the shared
    plan tail every such template ends with. Centralizes the one constant slot
    so the Linear / GitHub-issue / Slack / Actions builders don't each repeat it.
    """
    return render(name, plan_tail=render("plan_tail"), **fields)


def _linear_prompt(branch: str, identifier: str) -> str:
    """First-turn prompt that delegates Linear ticket fetch to the Linear MCP
    and then renames the branch to include the ticket title slug.

    Cockpit does not call the Linear API itself: spawn creates the worktree
    on `<prefix><id-lower>` (e.g. `khivi/pe-1234`) and Claude does both the
    ticket fetch and the post-fetch `git branch -m` to `<prefix><id-lower>-<title-slug>`.
    Workspace name + worktree directory stay on the original short slug;
    cockpit's reconciliation re-reads `git worktree list` each cycle, so the
    rename surfaces in the `cockpit watch` table without further action.

    Prose lives in ``cockpit/prompts/linear.txt`` (see `cockpit.lib.templates`).
    """
    return _scenario_prompt("linear", branch=branch, identifier=identifier)


def _jira_prompt(branch: str, identifier: str) -> str:
    """First-turn prompt that delegates Jira issue fetch to the Atlassian/Jira
    MCP and then renames the branch to include the issue summary slug.

    The Jira analog of `_linear_prompt`: cockpit doesn't call the Jira API at
    spawn time — spawn creates the worktree on `<key-lower>` (e.g. `proj-123`) and
    the spawned Claude does the MCP fetch and the post-fetch `git branch -m` to
    `<key-lower>-<summary-slug>`. The daemon's direct REST calls (the `devdone=`
    pill, the merge transition) are a separate, headless path. Prose lives in
    ``cockpit/prompts/jira.txt`` (see `cockpit.lib.templates`).
    """
    return _scenario_prompt("jira", branch=branch, identifier=identifier)


def _github_issue_prompt(branch: str, number: str, nwo: str | None) -> str:
    """First-turn prompt for a GitHub-issue source (`tickets: github`).

    The GitHub analog of `_linear_prompt`, but the transport is the `gh` CLI
    (already authenticated) rather than an MCP — so there's no retry-on-handshake
    dance, just `gh issue view`. spawn creates the worktree on `issue-<N>` and the
    spawned Claude reads the issue, then renames the branch to
    `issue-<N>-<title-slug>` and the workspace to the same slug. cockpit re-reads
    `git worktree list` each cycle, so the rename surfaces in `cockpit watch`.
    """
    issue_ref = f"{nwo}#{number}" if nwo else f"#{number}"
    view_cmd = (
        f"gh issue view {number} --repo {nwo}" if nwo else f"gh issue view {number}"
    )
    return _scenario_prompt(
        "github_issue", branch=branch, issue_ref=issue_ref, view_cmd=view_cmd
    )


def _slack_prompt(branch: str, url: str, *, mcp_fetch: bool) -> str:
    """First-turn prompt for a Slack-thread source.

    Cockpit never calls the Slack API itself: spawn creates the worktree on a
    deterministic codename branch (e.g. `khivi/cosmic-otter`) and the spawned
    Claude reads the thread via the Slack MCP, derives the task, and — when
    `mcp_fetch` — renames the branch + workspace to append a topic slug:
    `cosmic-otter` → `cosmic-otter-fix-oauth-retry`. The codename survives as the
    prefix (the "something cool" part) and the slug makes the worktree
    discoverable. Mirrors `_linear_prompt`'s fetch-then-rename shape.

    `mcp_fetch` is just `use_slack` (no `claude mcp list` pre-flight — that probe
    is unreliable for managed connectors; see `cockpit.lib.slack`). It gates the
    explicit fetch + rename steps, whose own retry-then-STOP logic handles a
    genuinely absent connector in-session. When False the prompt still carries
    the URL so the thread is available as context (read it best-effort, no
    rename) — the URL is the whole point of a Slack source, so it is always
    seeded regardless of the flag.

    The two modes are two templates — ``cockpit/prompts/slack_fetch.txt`` (the
    fetch + rename steps) and ``slack_context.txt`` (read-for-context only).
    """
    name = "slack_fetch" if mcp_fetch else "slack_context"
    return _scenario_prompt(name, branch=branch, url=url)


def _trello_prompt(branch: str, url: str) -> str:
    """First-turn prompt for a Trello-card source (`tickets: trello`).

    Cockpit never calls the Trello API at spawn time: a card URL carries no
    human-readable name, so spawn creates the worktree on a deterministic
    codename branch (e.g. `khivi/cosmic-otter`, seeded from the card's short
    link so re-spawning the same URL is idempotent) and the spawned Claude reads
    the card via the official Trello MCP, then renames the branch + workspace to
    append a topic slug (`cosmic-otter` → `cosmic-otter-fix-oauth`). The daemon's
    direct REST calls (the `devdone=` pill, the merge move) are a separate,
    headless path. Mirrors `_slack_prompt`'s codename-then-rename shape and
    `_jira_prompt`'s MCP retry-then-STOP handling — no `claude mcp list`
    pre-flight (that probe is unreliable for managed connectors). Prose lives in
    ``cockpit/prompts/trello.txt``.
    """
    return _scenario_prompt("trello", branch=branch, url=url)


def _repo_entry_or_none(repo_name: str | None) -> dict | None:
    """The resolved repo config entry, or None — the non-raising form of
    `select_repo` (used for best-effort reads like the start-label lookup, where
    an unknown repo should just skip, not abort the spawn)."""
    if repo_name:
        return find_repo_by_name(repo_name)
    return discover_repo()


def select_repo(repo_name: str | None) -> dict:
    if repo_name:
        repo_cfg = find_repo_by_name(repo_name)
        if repo_cfg is None:
            raise ValueError(_unknown_repo_msg(repo_name))
        return repo_cfg
    repo_cfg = discover_repo()
    if repo_cfg is None:
        listed = _format_configured_repos(repo_names())
        hint = f" Configured repos: {listed}." if listed else ""
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
    """Plan-only first-turn prompt. PR context block is included when `pr_info` is set.

    Prose lives in ``cockpit/prompts/plan_only.txt``; the optional PR block is
    interpolated into its ``{source_block}`` slot.
    """
    source_block = ""
    if pr_info:
        author, number, title = _pr_fields(pr_info)
        source_block = (
            f"\n\n**Source**: PR #{number} by @{author}"
            f"\n**Task**: {title}"
            f"\n\n**Context**: {pr_info.get('url', '')}"
        )
    return render("plan_only", branch=branch, source_block=source_block)


def _review_prompt(
    branch: str, pr_info: dict | None = None, command: str = REVIEW_COMMAND_DEFAULT
) -> str:
    """First-turn prompt for an auto-spawned review worktree (per-repo
    `review_prs`).

    Leads with ``command`` — a review slash command — so Claude Code runs that
    review against the PR checked out on this branch; the PR context block
    follows for the human reading the transcript. Mirrors the `--skill` path,
    which also delivers a bare slash command as the first turn. ``command``
    defaults to cockpit's `/cockpit:review` plugin command; the daemon passes
    the per-repo `review_command` (e.g. `/review` or `/pr-review`) via
    `--review-command`.

    The closing line keeps the worktree dry-run: report findings, then stop
    before posting comments or submitting an approve / request-changes verdict —
    a human authorizes those, never the auto-spawn.

    Prose lives in ``cockpit/prompts/review.txt``; ``command`` leads and the PR
    (or bare-branch) line fills the ``{context}`` slot.
    """
    if pr_info:
        author, number, title = _pr_fields(pr_info)
        context = (
            f"Reviewing PR #{number} by @{author} — {title}"
            f"\nbranch: {branch}"
            f"\n{pr_info.get('url', '')}"
        )
    else:
        context = f"Reviewing the open PR on branch `{branch}`."
    return render("review", command=command, context=context)


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
    related_pr_block = ""
    if pr_info:
        author = _pr_author(pr_info)
        related_pr_block = (
            f"\n**Related PR**: #{pr_info['number']} by @{author} — "
            f"{pr_info.get('title', '')} ({pr_info.get('url', '')})"
        )
    return _scenario_prompt(
        "actions",
        branch=branch,
        source=source,
        conclusion=conclusion,
        head_branch=head_branch,
        run_url=run_url,
        related_pr_block=related_pr_block,
        log_cmd=log_cmd,
    )


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
    # Bare `cockpit new` (no source, no --cwd, no --repo): register the cwd's
    # git repo for an in-place, no-worktree workspace on the current branch, then
    # flow through the --cwd path below. The daemon shows the repo's row but
    # never auto-spawns worktrees for it (`in_place: true`). Off-GitHub and
    # master-only repos register fine — `register_cwd` defaults the prefix empty
    # and `default_branch` falls back to git symbolic-ref / "main".
    if not chosen and not cwd and not args.repo:
        try:
            entry = register_cwd(in_place=True)
        except RuntimeError as e:
            return _die(
                f"{e}. Bare `cockpit new` registers the current git repo for an "
                "in-place (no-worktree) workspace; for an arbitrary directory "
                "use `cockpit new --cwd <path>` instead."
            )
        cwd = entry["path"]
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
    is_slack = False  # positional classified as a Slack URL (any context → plan)
    is_trello = False  # positional classified as a Trello card URL (any context → plan)
    is_gh_issue = False  # positional classified as a GitHub issue (any context → plan)
    gh_issue_value: str | None = None  # the issue number, for the start_label write

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
        elif mode == "slack":
            # No human-readable name in a Slack URL, so synthesize a cool
            # codename branch (deterministic from the thread's stable identity,
            # so re-spawning the same URL is idempotent). The spawned Claude
            # reads the thread via the Slack MCP and renames the branch to
            # append a topic slug (see _slack_prompt).
            branch = codename(slack_seed(value))
            from_name = True
            is_slack = True
            # `use_slack` alone gates the fetch + rename steps — no
            # `claude mcp list` pre-flight. That probe is unreliable for
            # claude.ai-managed connectors (false-negatives even when live), so
            # the fetch prompt's own retry-then-STOP logic handles a genuinely
            # absent connector in-session instead. The thread URL is always
            # seeded as context regardless, since it's the entire source.
            seeded_prompt = _slack_prompt(branch, value, mcp_fetch=cfg_use_slack())
        elif mode == "trello":
            # A Trello card URL carries no human name, so synthesize a codename
            # branch (deterministic from the card's short link, so re-spawning
            # the same URL is idempotent) — same shape as Slack. The spawned
            # Claude reads the card via the Trello MCP and renames the branch to
            # append a topic slug (see `_trello_prompt`). No `claude mcp list`
            # pre-flight (unreliable for managed connectors); the prompt's own
            # retry-then-STOP logic handles a genuinely absent connector. Seed the
            # fetch+rename prompt only when Trello is the active provider;
            # otherwise the card still seeds plan-only (via is_trello) with the
            # URL as context.
            branch = codename(trello_seed(value))
            from_name = True
            is_trello = True
            if cfg_tickets() == "trello":
                seeded_prompt = _trello_prompt(branch, value)
        elif mode == "gh-issue":
            # `value` is the issue number; the worktree lands on `issue-<N>` and
            # the spawned Claude reads the issue via `gh issue view`, then renames
            # the branch + workspace to append a title slug (see
            # `_github_issue_prompt`). `nwo_hint` (URL form) routes to the right
            # repo below; the `i#N`/`gh#N` shorthand relies on --repo/cwd.
            branch = f"issue-{value}"
            from_name = True
            is_gh_issue = True
            gh_issue_value = value
            # No `claude mcp list` pre-flight — the transport is the `gh` CLI, not
            # an MCP. Seed the fetch+rename prompt only when GitHub is the active
            # provider; otherwise the issue still seeds plan-only (via is_gh_issue)
            # with the number as context.
            if cfg_tickets() == "github":
                seeded_prompt = _github_issue_prompt(branch, value, nwo_hint)
        elif mode == "linear":
            branch = value.lower()
            from_name = True
            is_linear = True
            if cfg_tickets() == "linear" and not args.repo:
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
            if cfg_tickets() == "linear":
                mcp = linear_mcp_available()
                if mcp is False:
                    print(
                        f"cockpit: Linear MCP not detected via 'claude mcp list'; "
                        f"falling back to plain branch mode for {value}",
                        file=sys.stderr,
                    )
                else:
                    seeded_prompt = _linear_prompt(branch, value)
            elif cfg_tickets() == "jira":
                # Jira keys share Linear's `[A-Z]{2,6}-N` shape, so detect_source
                # classifies them as `linear` mode; the active provider picks the
                # prompt. No `claude mcp list` pre-flight — the Atlassian connector
                # is claude.ai-managed (that probe is unreliable; the prompt's own
                # retry-then-STOP logic handles a truly-absent MCP, mirroring
                # Slack). ponytail: a project key with digits or >6 letters won't
                # match LINEAR_RE_CI and falls to plain branch mode (worktree still
                # created, just unseeded) — broaden detect_source if that bites.
                seeded_prompt = _jira_prompt(branch, value)
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
            # Name the workspace by the same branch-derived label the daemon
            # re-asserts each tick, so the spawn name agrees with reconcile and
            # the path/name dedup below (no one-tick flip after creation).
            prefix = select_repo(args.repo).get("branch_prefix", "")
            short = branch_label(branch, prefix)
        branch_display = branch

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
                author = _pr_author(pr_info)
                print(
                    f"note: open PR #{pr_num} exists for branch "
                    f"{pr_lookup_branch!r}: {pr_info.get('title', '')} by "
                    f"@{author} ({pr_info.get('url', '')})",
                    file=sys.stderr,
                )

        if actions_run_info is not None:
            prompt = _actions_prompt(branch, actions_run_info, actions_job_id, pr_info)
        elif args.review:
            prompt = _review_prompt(branch, pr_info, command=args.review_command)
        elif prompt is None and (
            pr_info
            or is_linear
            or is_slack
            or is_trello
            or is_gh_issue
            or args.context_text
            or args.claude_addendum
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
    # Match by name first, then fall back to worktree path. The path check
    # catches the case where the daemon already spawned a workspace for this
    # worktree under a different slug (e.g. cockpit auto-spawned before the
    # user ran /cockpit:new), preventing a duplicate workspace.
    existing_ref = next((ref for ref, n in ws_refs.items() if n == ws_name), None)
    if existing_ref is None and wt is not None:
        try:
            cwds = workspace_cwds()
            resolved_wt = wt.resolve()
            existing_ref = next(
                (ref for ref, cwd in cwds.items() if cwd.resolve() == resolved_wt),
                None,
            )
            if existing_ref is not None:
                ws_name = ws_refs.get(existing_ref, ws_name)
        except Exception:
            pass
    attached_ws = existing_ref is not None
    if existing_ref is None:
        # A configured `prompt_prefix` (e.g. a session-setup slash command)
        # rides in as the initial turn; the task body, if any, is delivered as
        # a SEPARATE second submission so the two don't collapse onto one
        # slash-command line.
        initial, followup = split_prompt_prefix(prompt)
        new_ref = spawn_workspace(ws_name, wt, claude_command(initial))
        if new_ref is not None and followup:
            deliver_followup(new_ref, followup)
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

    # Mark the issue "work started" with the configured `tickets.start_label`
    # (opt-in; the one spawn-time GitHub write). Best-effort: a failed label
    # never blocks the spawn. Run inside the worktree so `gh` infers the repo.
    if is_gh_issue and gh_issue_value and wt is not None:
        start_label = github_start_label(repo_entry=_repo_entry_or_none(args.repo))
        if start_label and add_label(
            f"#{gh_issue_value}", start_label, repo_dir=str(wt)
        ):
            print(f"note: labeled issue #{gh_issue_value} '{start_label}'")

    kick_running(quiet=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
