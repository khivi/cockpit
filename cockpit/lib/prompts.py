"""Per-PR / orphan-worktree Claude prompts + shell quoting.

The prompt prose lives in packaged templates (`cockpit/prompts/*.txt`, rendered
via `cockpit.lib.templates`); this module owns the control flow — picking the
per-`display_issue` action template and deciding whether the author-mode
authority block applies. Mirrors the spawn first-turn prompts.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import TYPE_CHECKING

from .config import prompt_prefix, review_command
from .templates import render

if TYPE_CHECKING:
    from .gh import PR
    from .git import Worktree


def shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def split_prompt_prefix(prompt: str | None) -> tuple[str | None, str | None]:
    """Split a seeded prompt into ``(initial, followup)`` so a configured
    `prompt_prefix` slash command runs as its **own** first turn and the task
    body arrives as a **separate** second submission.

    Embedding both as one `claude '<prefix>\\n\\nbody'` argument collapses the
    body onto the slash command's `$ARGUMENTS` line — the skill and the task
    render as one message. Delivering them as two sends keeps the skill
    invocation and the task on distinct lines.

    - prefix + body → ``(prefix, body)``   skill first, body delivered after
    - prefix only   → ``(prefix, None)``
    - body only     → ``(body, None)``     no prefix configured: unchanged
    """
    prefix = prompt_prefix()
    if prefix and prompt:
        return prefix, prompt
    if prefix:
        return prefix, None
    return prompt, None


# A review session runs in a checkout of someone else's PR head. Loading only
# the user's settings keeps that tree's project/local settings (hooks,
# permissions) out while the owner's own setup still applies. The tree's
# .mcp.json, CLAUDE.md, skills and commands are NOT covered here; the tree's
# .mcp.json is held off by `REVIEW_SETTINGS` instead.
REVIEW_CLAUDE_FLAGS = "--setting-sources user"

# `--settings` outranks user settings, so a user-level
# `enableAllProjectMcpServers: true` cannot auto-start a server the PR's own
# .mcp.json declares; Claude Code prompts a human for it instead.
REVIEW_SETTINGS = {"enableAllProjectMcpServers": False}


def claude_command(
    prompt: str | None,
    *,
    print_mode: bool = False,
    strip_env: Iterable[str],
    deny: Iterable[str],
    review: bool = False,
) -> str:
    """Build the `claude [prompt]` shell command for the *initial* turn.

    Prefix handling lives in `split_prompt_prefix` — pass it the ``initial``
    half (the prefix when one is configured, else the body). A `None` prompt
    yields a bare `claude`. `print_mode` adds `-p`, for a caller that wants the
    turn to complete and exit rather than land in an interactive session.

    `strip_env` names variables to remove from the session's environment, as an
    `env -u NAME` prefix on the command itself. A launched terminal's env is
    cmux's to decide, so scrubbing the caller's env does not confine the session.
    Required, so a launch path cannot forget it: a caller with nothing to strip
    passes `()`. Callers pass `config.credential_env_names(cfg)`: the helper
    fetches nothing.

    This is the **only** place the agent binary is named. Every launch path
    routes through it — the workspace spawns in `cmux.py` and `_run_repo_skills`'
    blocking `fast_skills` run — so swapping the agent stays a one-function
    change. An inline `claude ...` at a call site quietly costs that.
    `review` adds `REVIEW_CLAUDE_FLAGS` and `REVIEW_SETTINGS`, for a session on
    a PR head that is not the user's own.

    `deny` is Claude Code permission rules (`config.deny_tools`) passed as
    `permissions.deny` in one `--settings` JSON. Required for the same reason
    as `strip_env`; a caller with nothing to deny passes `()`.
    """
    flag = " -p" if print_mode else ""
    settings: dict = {}
    if review:
        flag += f" {REVIEW_CLAUDE_FLAGS}"
        settings.update(REVIEW_SETTINGS)
    rules = list(dict.fromkeys(deny))
    if rules:
        settings["permissions"] = {"deny": rules}
    if settings:
        flag += (
            f" --settings {shell_quote(json.dumps(settings, separators=(',', ':')))}"
        )
    names = sorted(set(strip_env))
    env = f"env {' '.join(f'-u {shell_quote(n)}' for n in names)} " if names else ""
    if prompt is None:
        return f"{env}claude{flag}"
    return f"{env}claude{flag} {shell_quote(prompt)}"


# Maps a PR's `display_issue` onto its action template (`pr_action_*.txt`) and
# whether the author-mode authority block (`pr_authority.txt`) is appended.
_ISSUE_ACTIONS: dict[str | None, tuple[str, bool]] = {
    "comments": ("pr_action_comments", True),
    "changes-requested": ("pr_action_changes_requested", False),
    "ci": ("pr_action_ci", True),
    "conflicts": ("pr_action_conflicts", True),
    "approved": ("pr_action_approved", False),
    None: ("pr_action_clean", False),
}


def review_lead(command: str) -> str:
    """The opening block of a review prompt: the configured `skills.review`
    slash command, or cockpit's bundled review prose when none is set.

    Both review call sites go through here — `spawn._review_prompt` for the
    `review_prs` auto-spawn and `build_pr_prompt` for a coworker's PR — so the
    two cannot disagree about what an unset `skills.review` seeds.
    """
    return command or render("review_prose")


def build_pr_prompt(pr: PR) -> str:
    """Per-PR Claude prompt.

    Author-mode for **my own** PR — a local worktree on my PR's branch implies I
    intend to author/collaborate, so the issue-specific action gets the
    `pr_authority` block (commit/push, force-push after rebase, no asking).

    A **coworker's** PR gets the same review-mode seed the `review_prs`
    auto-spawn uses (`spawn._review_prompt`'s `review.txt`, led by
    `skills.review` or the bundled prose `review_lead` falls back to): its branch
    is not mine to rewrite, so no authority block and no "fix the CI" action.
    Resolved global-level (`review_command()` with no repo entry) — this helper
    is reached from `spawn_pr_workspace`, which carries a PR and a worktree, not
    the repo config block.
    """
    if not pr.mine:
        return render(
            "review",
            lead=review_lead(review_command()),
            context=(
                f"Reviewing PR #{pr.number} by @{pr.author} — {pr.title}"
                f"\nbranch: {pr.branch}"
                f"\n{pr.url}"
            ),
        )
    template, with_authority = _ISSUE_ACTIONS.get(
        pr.display_issue, _ISSUE_ACTIONS[None]
    )
    action = render(template, number=pr.number, unaddressed=pr.unaddressed)
    authority = f"\n\n{render('pr_authority')}" if with_authority else ""
    return render(
        "pr",
        number=pr.number,
        title=pr.title,
        branch=pr.branch,
        author=pr.author,
        url=pr.url,
        action=action,
        authority=authority,
    )


def build_orphan_prompt(wt: Worktree) -> str:
    return render("orphan", short=wt.short, branch=wt.branch)
