"""Pill decision tests targeting scripts/lib/pills.py.

`decide_pills` is the single source of truth for which pills a PR/worktree
combination should surface. These tests pin the decisions; consumer-side
mapping (cmux) lives in tests/lib/test_cmux.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.lib.gh import PR
from scripts.lib.git import Worktree
from scripts.lib.nudges import KNOWN_CATEGORIES, NudgePref
from scripts.lib.pills import KIND_ORDER, decide_pills


def _pr(**overrides) -> PR:
    base: dict = dict(
        number=1,
        title="t",
        branch="khivi/feature",
        url="https://example/pr/1",
        author="khivi",
        is_draft=False,
        review_decision="REVIEW_REQUIRED",
        mergeable="MERGEABLE",
        ci="passed",
        unaddressed=0,
        total_from_others=0,
        state="OPEN",
        updated_at="",
    )
    base.update(overrides)
    return PR(**base)


def _wt(
    branch: str = "khivi/feature",
    *,
    rebasing: bool = False,
    merging: bool = False,
    dirty: int = 0,
) -> Worktree:
    return Worktree(
        path=Path("/tmp/wt"),
        branch=branch,
        rebasing=rebasing,
        merging=merging,
        dirty_count=dirty,
    )


# ── decide_pills ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pr_overrides,wt_kwargs,expected",
    [
        ({}, {}, [{"kind": "ci_passed"}]),
        ({"ci": "none"}, {}, []),
        ({"review_decision": "APPROVED"}, {}, [{"kind": "approved"}]),
        ({"ci": "failed:lint"}, {}, [{"kind": "ci_failed", "phase": "lint"}]),
        ({"ci": "failed"}, {}, [{"kind": "ci_failed", "phase": ""}]),
        ({"ci": "pending"}, {}, [{"kind": "ci_pending"}]),
        ({"review_decision": "CHANGES_REQUESTED"}, {}, [{"kind": "changes_requested"}]),
        ({"mergeable": "CONFLICTING"}, {}, [{"kind": "conflict"}]),
        (
            {"state": "MERGED"},
            {},
            [{"kind": "state", "state": "MERGED"}],
        ),
        (
            {},
            {"rebasing": True, "dirty": 4},
            [{"kind": "rebase"}, {"kind": "wip", "count": 4}],
        ),
    ],
    ids=[
        "clean_open_pr_with_passing_ci_emits_ci_passed",
        "clean_open_pr_without_ci_emits_no_pills",
        "ci_passed_suppressed_when_other_pills_present",
        "ci_failed_carries_phase",
        "ci_failed_without_phase_marker",
        "ci_pending",
        "changes_requested_alone",
        "conflict_pill",
        "ci_passed_suppressed_for_merged_pr",
        "worktree_pills_independent_of_pr",
    ],
)
def test_decide_pills_equality(pr_overrides, wt_kwargs, expected):
    assert decide_pills(_pr(**pr_overrides), _wt(**wt_kwargs)) == expected


@pytest.mark.parametrize(
    "pr_overrides,expected_kinds",
    [
        ({"is_draft": True, "review_decision": "APPROVED"}, ["draft", "approved"]),
    ],
    ids=["draft_and_approved_coexist"],
)
def test_decide_pills_kinds(pr_overrides, expected_kinds):
    pills = decide_pills(_pr(**pr_overrides), _wt())
    assert [p["kind"] for p in pills] == expected_kinds


@pytest.mark.parametrize(
    "pr_overrides,must_have,must_not_have",
    [
        ({"unaddressed": 1}, ["unaddressed"], ["ci_passed"]),
        (
            {"unaddressed": 3, "review_decision": "CHANGES_REQUESTED"},
            ["unaddressed"],
            ["changes_requested"],
        ),
    ],
    ids=[
        "ci_passed_suppressed_when_unaddressed_present",
        "unaddressed_supersedes_changes_requested",
    ],
)
def test_decide_pills_membership(pr_overrides, must_have, must_not_have):
    kinds = [p["kind"] for p in decide_pills(_pr(**pr_overrides), _wt())]
    for k in must_have:
        assert k in kinds
    for k in must_not_have:
        assert k not in kinds


def test_state_pill_only_for_non_open():
    # OPEN + ci=none → no pills; MERGED/CLOSED → state pill (and ci_passed is
    # suppressed by the state pill, see ci_passed_suppressed_for_merged_pr).
    assert decide_pills(_pr(state="OPEN", ci="none"), _wt()) == []
    assert decide_pills(_pr(state="MERGED", ci="none"), _wt()) == [
        {"kind": "state", "state": "MERGED"}
    ]
    assert decide_pills(_pr(state="CLOSED", ci="none"), _wt()) == [
        {"kind": "state", "state": "CLOSED"}
    ]


def test_wip_dropped_when_no_worktree():
    # PR exists but worktree is unknown (e.g. external repo): no wip pill.
    pills = decide_pills(_pr(ci="failed:test"), None)
    kinds = [p["kind"] for p in pills]
    assert "wip" not in kinds
    assert "ci_failed" in kinds


def test_full_house_canonical_order():
    pills = decide_pills(
        _pr(
            is_draft=True,
            review_decision="APPROVED",
            mergeable="CONFLICTING",
            ci="failed:tests",
            unaddressed=2,
            state="OPEN",
        ),
        _wt(merging=True, dirty=3),
    )
    assert [p["kind"] for p in pills] == [
        "merge",
        "wip",
        "ci_failed",
        "unaddressed",
        "conflict",
        "draft",
        "approved",
    ]


# ── muted pill ──────────────────────────────────────────────────────────────


def test_muted_first_in_kind_order():
    assert KIND_ORDER[0] == "muted"


def test_muted_pref_none_or_empty_emits_no_muted():
    assert [p["kind"] for p in decide_pills(_pr(), _wt(), pref=None)] == ["ci_passed"]
    assert [p["kind"] for p in decide_pills(_pr(), _wt(), pref=NudgePref())] == [
        "ci_passed"
    ]


def test_muted_full_scope_anchors_front():
    pref = NudgePref(disabled_categories=set(KNOWN_CATEGORIES))
    pills = decide_pills(_pr(ci="failed:lint", unaddressed=2), _wt(), pref=pref)
    assert pills[0] == {"kind": "muted", "scope": "all", "categories": []}


def test_muted_partial_scope_lists_sorted_categories():
    pref = NudgePref(disabled_categories={"comments", "ci"})
    pills = decide_pills(_pr(), _wt(), pref=pref)
    assert pills[0] == {
        "kind": "muted",
        "scope": "some",
        "categories": ["ci", "comments"],
    }


def test_muted_does_not_suppress_ci_passed_sentinel():
    pref = NudgePref(disabled_categories={"ci"})
    kinds = [p["kind"] for p in decide_pills(_pr(), _wt(), pref=pref)]
    assert kinds == ["muted", "ci_passed"]


def test_muted_coexists_with_actionable_pills():
    pref = NudgePref(disabled_categories={"ci"})
    pills = decide_pills(_pr(ci="failed:lint", unaddressed=2), _wt(), pref=pref)
    kinds = [p["kind"] for p in pills]
    assert kinds[0] == "muted"
    assert "ci_failed" in kinds
    assert "unaddressed" in kinds


def test_muted_with_expired_pref_clearing_via_load_pref(tmp_path, monkeypatch):
    # An expired pref returned by load_pref already has disabled_categories
    # cleared (see nudges.load_pref auto-expiry). Pass an explicit empty pref
    # to mirror that contract — no muted pill should appear.
    pref = NudgePref(disabled_categories=set(), until=None)
    assert all(p["kind"] != "muted" for p in decide_pills(_pr(), _wt(), pref=pref))
