"""Derive stacked-PR chains from one repo's PRs.

GitHub's stacked pull requests (`gh stack`) carry no stack id, position, or
parent field on the API — a stack is simply a run of PRs where each one's base
branch is the previous one's head. So cockpit derives it, for free: the
relevant-PR query already selects `baseRefName` (`PR.base`), so a chain costs
no extra round-trip, needs no local `gh stack` state in the worktree, and works
for a coworker's stack as well as my own.

Pure and derived-every-cycle, like every other piece of cockpit's inventory —
nothing here is cached or stored.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gh import PR


def find_stacks(prs: Iterable[PR]) -> list[list[PR]]:
    """Every stack among `prs`, each as one root-first list of >= 2 open PRs.

    A PR's parent is the open PR whose head branch equals this PR's `base`;
    roots are the PRs based on something that isn't another open PR (the trunk,
    usually). Each stack is emitted once, breadth-first from its root, so a PR
    belongs to exactly one chain even when the stack forks — a cmux workspace
    can only live in one sidebar group, so overlapping chains are not an option.

    Merged/closed PRs are excluded: once the bottom of a stack lands, the
    remaining PRs are re-based onto the trunk and are no longer stacked on it.

    Chains are matched on `PR.branch`, which is `gh.pr_worktree_branch`-
    normalized. That only differs from the raw head for a trunk-headed PR, whose
    synthesized `pr-<N>-…` branch simply never matches another PR's base — the
    right answer, since a merge-the-trunk PR is not a stack member.
    """
    open_prs = [pr for pr in prs if pr.state == "OPEN"]
    # Two PRs can't share a head branch, but a stale duplicate in `prs` can:
    # keep the highest number so the chain follows the live PR.
    by_branch: dict[str, PR] = {}
    for pr in open_prs:
        if pr.branch and pr.number >= by_branch.get(pr.branch, pr).number:
            by_branch[pr.branch] = pr

    children: dict[int, list[PR]] = {}
    roots: list[PR] = []
    for pr in sorted(by_branch.values(), key=lambda p: p.number):
        parent = by_branch.get(pr.base) if pr.base else None
        if parent is None or parent.number == pr.number:
            roots.append(pr)
        else:
            children.setdefault(parent.number, []).append(pr)

    stacks: list[list[PR]] = []
    seen: set[int] = set()
    for root in roots:
        chain: list[PR] = []
        queue = [root]
        while queue:
            pr = queue.pop(0)
            if pr.number in seen:
                continue  # base cycle, or a PR already claimed by another root
            seen.add(pr.number)
            chain.append(pr)
            queue.extend(children.get(pr.number, ()))
        if len(chain) > 1:
            stacks.append(chain)
    return stacks


def stack_order(
    branches: Sequence[str], base_of: Callable[[str], str]
) -> list[tuple[int, int]]:
    """`(index, depth)` for each of `branches`, stacks contiguous and root-first.

    The renderer-side half of `find_stacks`: same `base` link, but read off the
    daemon-written `pr-base` cell (`base_of`) rather than a live `PR`, so the
    TUI can indent a stacked row without a network call or a stored stack id.
    A branch whose base is another branch in `branches` sorts directly under it
    at one more level of depth; everything else stays a depth-0 row in its
    original order.

    Indices, not branch names, so two worktrees that report the same branch
    (detached HEADs both reading "") each keep their own row. A base cycle
    (only reachable from a stale cell) leaves its members unvisited by the
    walk, so they are appended flat at the end rather than dropped.
    """
    index_of = {b: i for i, b in enumerate(branches) if b}
    children: dict[int, list[int]] = {}
    roots: list[int] = []
    for i, branch in enumerate(branches):
        parent = index_of.get(base_of(branch)) if branch else None
        if parent is None or parent == i:
            roots.append(i)
        else:
            children.setdefault(parent, []).append(i)

    out: list[tuple[int, int]] = []
    seen: set[int] = set()
    for root in roots:
        queue = [(root, 0)]
        while queue:
            i, depth = queue.pop()
            if i in seen:
                continue
            seen.add(i)
            out.append((i, depth))
            queue.extend((child, depth + 1) for child in reversed(children.get(i, [])))
    out.extend((i, 0) for i in range(len(branches)) if i not in seen)
    return out
