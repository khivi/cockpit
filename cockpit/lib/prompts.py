"""Per-PR / orphan-worktree Claude prompts + shell quoting.

The prompt prose lives in packaged templates (`cockpit/prompts/*.txt`, rendered
via `cockpit.lib.templates`); this module owns the control flow — picking the
per-`display_issue` action template and deciding whether the author-mode
authority block applies. Mirrors the spawn first-turn prompts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import prompt_prefix
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


def claude_command(prompt: str | None) -> str:
    """Build the `claude [prompt]` shell command for the *initial* turn.

    Prefix handling lives in `split_prompt_prefix` — pass it the ``initial``
    half (the prefix when one is configured, else the body). A `None` prompt
    yields a bare `claude`.
    """
    if prompt is None:
        return "claude"
    return f"claude {shell_quote(prompt)}"


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


def build_pr_prompt(pr: PR) -> str:
    """Per-PR Claude prompt in author-mode. A local worktree on a PR's branch
    implies the user intends to author/collaborate.
    """
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
