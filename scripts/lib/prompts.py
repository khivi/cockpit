"""Per-PR / orphan-worktree Claude prompts + shell quoting."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gh import PR
    from .git import Worktree


def shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def claude_command(prompt: str | None) -> str:
    if prompt is None:
        return "claude"
    return f"claude {shell_quote(prompt)}"


_AUTHORITY = (
    "Authority: commit and push to this PR's branch (including force-push after rebase) "
    "without asking. Ask y/n only before posting external writes — GitHub PR/review "
    "comments, etc. When you finish or hit a blocker, report state and stop; do not "
    "idle for follow-up unless a y/n is genuinely pending."
)

_ISSUE_ACTIONS: dict[object, tuple[object, bool]] = {
    "comments": (
        lambda pr: (
            f"Action: address {pr.unaddressed} unresolved review thread(s). Draft replies; "
            "ask y/n before posting. Code changes, commits, and pushes are pre-authorized."
        ),
        True,
    ),
    "changes-requested": (
        "All review threads are resolved; reviewer hasn't dismissed CHANGES_REQUESTED "
        "yet. No action for you — report current state and exit.",
        False,
    ),
    "ci": (
        lambda pr: (
            f"Action: CI is failing. Run `gh pr checks {pr.number}` and "
            "`gh run view --log-failed` on failing runs to investigate. Fix, commit, and "
            "push without asking. Report and stop when CI is re-running or you're blocked."
        ),
        True,
    ),
    "conflicts": (
        "Action: merge conflicts vs base. Plan a rebase, execute it, and force-push "
        "without asking. Report and stop when pushed or blocked.",
        True,
    ),
    "approved": (
        "PR is approved and ready to merge. Report current state (CI, mergeability) "
        "and exit; the human will run `gh pr merge` when ready.",
        False,
    ),
    None: (
        "PR looks clean (CI green, no unaddressed comments, mergeable). Report current state "
        "and exit without changes.",
        False,
    ),
}


def build_pr_prompt(pr: "PR") -> str:
    """Per-PR Claude prompt in author-mode. A local worktree on a PR's branch
    implies the user intends to author/collaborate.
    """
    base = (
        f"PR #{pr.number} — {pr.title}\n"
        f"branch: {pr.branch}\n"
        f"author: @{pr.author}\n"
        f"url: {pr.url}\n\n"
    )
    action, with_authority = _ISSUE_ACTIONS.get(pr.display_issue, _ISSUE_ACTIONS[None])
    text = action(pr) if callable(action) else action
    return base + text + (f"\n\n{_AUTHORITY}" if with_authority else "")


def build_orphan_prompt(wt: "Worktree") -> str:
    return (
        f"This worktree ({wt.short}, branch {wt.branch}) has no open PR. "
        "Resume work and push a PR when ready, or close the worktree if abandoned."
    )
