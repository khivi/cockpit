"""Tests for stacked-PR chain detection (cockpit/lib/stacks.py).

Pure function over `PR.base` — no network, no git, no cmux.
"""

from __future__ import annotations

from cockpit.lib.gh import PR
from cockpit.lib.stacks import find_stacks, stack_order


def _pr(number: int, branch: str, base: str, *, state: str = "OPEN") -> PR:
    return PR(
        number=number,
        title=f"pr {number}",
        branch=branch,
        url="",
        author="khivi",
        is_draft=False,
        review_decision="",
        mergeable="MERGEABLE",
        ci="passed",
        unaddressed=0,
        total_from_others=0,
        state=state,
        base=base,
    )


def _numbers(stacks: list[list[PR]]) -> list[list[int]]:
    return [[pr.number for pr in chain] for chain in stacks]


def test_linear_stack_is_one_root_first_chain():
    prs = [
        _pr(3, "khivi/c", "khivi/b"),
        _pr(1, "khivi/a", "main"),
        _pr(2, "khivi/b", "khivi/a"),
    ]
    assert _numbers(find_stacks(prs)) == [[1, 2, 3]]


def test_independent_prs_are_not_a_stack():
    prs = [_pr(1, "khivi/a", "main"), _pr(2, "khivi/b", "main")]
    assert find_stacks(prs) == []


def test_merged_root_leaves_the_rest_stacked():
    # The bottom of the stack landing doesn't unstack what sat on top of it:
    # #2 is now the root (its base is no longer an open PR) and #3 still
    # depends on it.
    prs = [
        _pr(1, "khivi/a", "main", state="MERGED"),
        _pr(2, "khivi/b", "khivi/a"),
        _pr(3, "khivi/c", "khivi/b"),
    ]
    assert _numbers(find_stacks(prs)) == [[2, 3]]


def test_merged_middle_splits_the_chain():
    prs = [
        _pr(1, "khivi/a", "main"),
        _pr(2, "khivi/b", "khivi/a", state="MERGED"),
        _pr(3, "khivi/c", "khivi/b"),
    ]
    assert find_stacks(prs) == []


def test_fork_lands_in_one_chain():
    # A workspace can only sit in one sidebar group, so two PRs based on the
    # same parent join that parent's single chain rather than forming two.
    prs = [
        _pr(1, "khivi/a", "main"),
        _pr(2, "khivi/b", "khivi/a"),
        _pr(3, "khivi/c", "khivi/a"),
    ]
    assert _numbers(find_stacks(prs)) == [[1, 2, 3]]


def test_two_separate_stacks():
    prs = [
        _pr(1, "khivi/a", "main"),
        _pr(2, "khivi/b", "khivi/a"),
        _pr(5, "khivi/x", "main"),
        _pr(6, "khivi/y", "khivi/x"),
    ]
    assert _numbers(find_stacks(prs)) == [[1, 2], [5, 6]]


def test_base_cycle_terminates_and_yields_nothing():
    prs = [_pr(1, "khivi/a", "khivi/b"), _pr(2, "khivi/b", "khivi/a")]
    assert find_stacks(prs) == []


def test_self_based_pr_is_a_root_not_a_cycle():
    prs = [_pr(1, "khivi/a", "khivi/a"), _pr(2, "khivi/b", "khivi/a")]
    assert _numbers(find_stacks(prs)) == [[1, 2]]


def test_trunk_headed_pr_never_joins_a_stack():
    # `gh.pr_worktree_branch` rewrites a main-headed PR to `pr-<N>-<base>`, so
    # its branch matches nobody's base — a merge-the-trunk PR is not a stack.
    prs = [
        _pr(1, "khivi/a", "main"),
        _pr(7, "pr-7-khivi-a", "khivi/a"),
        _pr(8, "khivi/b", "main"),
    ]
    assert _numbers(find_stacks(prs)) == [[1, 7]]


def test_empty_base_is_a_root():
    prs = [_pr(1, "khivi/a", ""), _pr(2, "khivi/b", "khivi/a")]
    assert _numbers(find_stacks(prs)) == [[1, 2]]


def test_duplicate_branch_keeps_the_live_pr():
    # A stale payload for a reused branch must not shadow the current PR.
    prs = [
        _pr(1, "khivi/a", "main"),
        _pr(9, "khivi/a", "main"),
        _pr(10, "khivi/b", "khivi/a"),
    ]
    assert _numbers(find_stacks(prs)) == [[9, 10]]


# ── stack_order (renderer-side derivation off the `pr-base` cells) ──────────


def _order(branches: list[str], bases: dict[str, str]) -> list[tuple[str, int]]:
    return [
        (branches[i], depth)
        for i, depth in stack_order(branches, lambda b: bases.get(b, ""))
    ]


def test_stack_order_nests_a_child_under_its_base():
    order = _order(["khivi/a", "khivi/b"], {"khivi/a": "main", "khivi/b": "khivi/a"})
    assert order == [("khivi/a", 0), ("khivi/b", 1)]


def test_stack_order_deepens_with_each_level():
    order = _order(
        ["khivi/a", "khivi/b", "khivi/c"],
        {"khivi/b": "khivi/a", "khivi/c": "khivi/b"},
    )
    assert order == [("khivi/a", 0), ("khivi/b", 1), ("khivi/c", 2)]


def test_stack_order_pulls_a_child_up_next_to_its_root():
    # The chain renders contiguously even when git listed an unrelated worktree
    # between the two — a stack that reads as a tree has to be adjacent.
    order = _order(["khivi/a", "khivi/z", "khivi/b"], {"khivi/b": "khivi/a"})
    assert order == [("khivi/a", 0), ("khivi/b", 1), ("khivi/z", 0)]


def test_stack_order_keeps_unstacked_rows_flat_and_in_order():
    order = _order(["khivi/b", "khivi/a"], {"khivi/a": "main", "khivi/b": "main"})
    assert order == [("khivi/b", 0), ("khivi/a", 0)]


def test_stack_order_forked_stack_indents_both_children():
    order = _order(
        ["khivi/a", "khivi/b", "khivi/c"],
        {"khivi/b": "khivi/a", "khivi/c": "khivi/a"},
    )
    assert order == [("khivi/a", 0), ("khivi/b", 1), ("khivi/c", 1)]


def test_stack_order_keeps_every_row_when_branches_repeat():
    # Two detached worktrees both report "" — neither row may be dropped.
    assert stack_order(["", ""], lambda b: "") == [(0, 0), (1, 0)]


def test_stack_order_base_cycle_falls_back_to_flat_rows():
    order = _order(["khivi/a", "khivi/b"], {"khivi/a": "khivi/b", "khivi/b": "khivi/a"})
    assert sorted(order) == [("khivi/a", 0), ("khivi/b", 0)]


def test_stack_order_self_base_is_a_root():
    assert _order(["khivi/a"], {"khivi/a": "khivi/a"}) == [("khivi/a", 0)]
