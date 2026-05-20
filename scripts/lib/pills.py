"""Semantic pill decisions derived from PR + Worktree state.

Both the cmux side panel and the Claude Code statusLine footer render the same
set of pills; this module is the single decider. Each surface owns a
`kind -> styled-pill` map and applies its own emoji/color/ANSI choices.

`decide_pills(pr, wt)` returns an ordered list of `{"kind": str, **payload}`
dicts. Surfaces consume the list in order; emission order is the canonical
display order.

The `state` kind (MERGED/CLOSED) is emitted always; surfaces decide whether to
render it. cmux drops it because autoclose tears down workspaces for non-OPEN
PRs within one daemon cycle. Footer keeps it because the statusLine renders in
any git dir indefinitely, including merged-but-not-cleaned-up worktrees.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gh import PR
    from .git import Worktree


KIND_ORDER = (
    "rebase",
    "merge",
    "wip",
    "ci_failed",
    "ci_pending",
    "unaddressed",
    "changes_requested",
    "conflict",
    "draft",
    "approved",
    "state",
)


def decide_pills(pr: "PR", wt: "Worktree | None") -> list[dict]:
    """Ordered semantic pill list for `pr` (and its local `wt` if known).

    Each entry has `kind` plus optional payload (e.g. `count`, `phase`,
    `state`). Order is canonical — see KIND_ORDER. No styling, no emoji.
    """
    pills: list[dict] = []
    if wt is not None and wt.rebasing:
        pills.append({"kind": "rebase"})
    if wt is not None and wt.merging:
        pills.append({"kind": "merge"})
    if wt is not None and wt.dirty_count > 0:
        pills.append({"kind": "wip", "count": wt.dirty_count})
    if pr.ci.startswith("failed"):
        phase = pr.ci.split(":", 1)[1] if ":" in pr.ci else ""
        pills.append({"kind": "ci_failed", "phase": phase})
    elif pr.ci == "pending":
        pills.append({"kind": "ci_pending"})
    if pr.unaddressed > 0:
        pills.append({"kind": "unaddressed", "count": pr.unaddressed})
    elif pr.review_decision == "CHANGES_REQUESTED":
        pills.append({"kind": "changes_requested"})
    if pr.mergeable == "CONFLICTING":
        pills.append({"kind": "conflict"})
    if pr.is_draft:
        pills.append({"kind": "draft"})
    if pr.review_decision == "APPROVED":
        pills.append({"kind": "approved"})
    if pr.state and pr.state != "OPEN":
        pills.append({"kind": "state", "state": pr.state})
    return pills
