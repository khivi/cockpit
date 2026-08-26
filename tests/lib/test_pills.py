"""Pill decision tests targeting cockpit/lib/pills.py.

`decide_pills` is the single source of truth for which pills a PR/worktree
combination should surface. These tests pin the decisions; consumer-side
mapping (cmux) lives in tests/lib/test_cmux.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cockpit.lib.gh import PR
from cockpit.lib.git import Worktree
from cockpit.lib.nudges import NudgePref
from cockpit.lib.pills import KIND_ORDER, decide_pills, pr_status

# `_pr()`'s defaults rendered as the trailing PR-identity pill. Every PR emits
# one, so the equality cases below all carry it.
OPEN_PR = {"kind": "pr", "number": 1, "status": "open"}


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
        ({}, {}, [{"kind": "ci_passed"}, OPEN_PR]),
        ({"ci": "none"}, {}, [OPEN_PR]),
        (
            {"review_decision": "APPROVED"},
            {},
            [{"kind": "ci_passed"}, {"kind": "approved"}, OPEN_PR],
        ),
        ({"ci": "failed:lint"}, {}, [{"kind": "ci_failed", "phase": "lint"}, OPEN_PR]),
        ({"ci": "failed"}, {}, [{"kind": "ci_failed", "phase": ""}, OPEN_PR]),
        ({"ci": "pending"}, {}, [{"kind": "ci_pending"}, OPEN_PR]),
        ({"ci": "unknown"}, {}, [{"kind": "ci_unknown"}, OPEN_PR]),
        (
            {"review_decision": "CHANGES_REQUESTED"},
            {},
            [{"kind": "ci_passed"}, {"kind": "changes_requested"}, OPEN_PR],
        ),
        (
            {"mergeable": "CONFLICTING"},
            {},
            [{"kind": "ci_passed"}, {"kind": "conflict"}, OPEN_PR],
        ),
        (
            {"state": "MERGED"},
            {},
            [
                {"kind": "ci_passed"},
                {"kind": "state", "state": "MERGED"},
                {"kind": "pr", "number": 1, "status": "merged"},
            ],
        ),
        (
            {},
            {"rebasing": True, "dirty": 4},
            [
                {"kind": "rebase"},
                {"kind": "wip", "count": 4},
                {"kind": "ci_passed"},
                OPEN_PR,
            ],
        ),
    ],
    ids=[
        "clean_open_pr_with_passing_ci_emits_ci_passed",
        "clean_open_pr_without_ci_emits_no_pills",
        "ci_passed_coexists_with_approved",
        "ci_failed_carries_phase",
        "ci_failed_without_phase_marker",
        "ci_pending",
        "ci_unknown_when_gh_errored",
        "changes_requested_alone",
        "conflict_pill",
        "ci_passed_coexists_with_merged_state",
        "worktree_pills_independent_of_pr",
    ],
)
def test_decide_pills_equality(pr_overrides, wt_kwargs, expected):
    assert decide_pills(_pr(**pr_overrides), _wt(**wt_kwargs)) == expected


@pytest.mark.parametrize(
    "pr_overrides,expected_kinds",
    [
        (
            {"is_draft": True, "review_decision": "APPROVED"},
            ["ci_passed", "draft", "approved", "pr"],
        ),
    ],
    ids=["draft_and_approved_coexist"],
)
def test_decide_pills_kinds(pr_overrides, expected_kinds):
    pills = decide_pills(_pr(**pr_overrides), _wt())
    assert [p["kind"] for p in pills] == expected_kinds


@pytest.mark.parametrize(
    "pr_overrides,must_have,must_not_have",
    [
        ({"unaddressed": 1}, ["ci_passed", "unaddressed"], []),
        (
            {"unaddressed": 3, "review_decision": "CHANGES_REQUESTED"},
            ["unaddressed"],
            ["changes_requested"],
        ),
    ],
    ids=[
        "ci_passed_coexists_with_unaddressed",
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
    # OPEN + ci=none → the pr pill alone; MERGED/CLOSED add `state`.
    # ci_passed is independent of state (see ci_passed_coexists_with_merged_state).
    assert decide_pills(_pr(state="OPEN", ci="none"), _wt()) == [OPEN_PR]
    assert decide_pills(_pr(state="MERGED", ci="none"), _wt()) == [
        {"kind": "state", "state": "MERGED"},
        {"kind": "pr", "number": 1, "status": "merged"},
    ]
    assert decide_pills(_pr(state="CLOSED", ci="none"), _wt()) == [
        {"kind": "state", "state": "CLOSED"},
        {"kind": "pr", "number": 1, "status": "closed"},
    ]


# ── pr pill ─────────────────────────────────────────────────────────────────


def test_pr_pill_is_last_and_names_the_pr():
    pills = decide_pills(_pr(number=332), _wt())
    assert pills[-1] == {"kind": "pr", "number": 332, "status": "open"}
    assert KIND_ORDER[-1] == "pr"


def test_pr_pill_emitted_for_every_state_including_open():
    for state, status in (("OPEN", "open"), ("MERGED", "merged"), ("CLOSED", "closed")):
        pills = decide_pills(_pr(state=state), _wt())
        assert pills[-1] == {"kind": "pr", "number": 1, "status": status}


def test_pr_status_draft_only_supersedes_open():
    # A draft is only ever OPEN on GitHub; if one is closed, `closed` is the
    # more useful label, and MERGED is unreachable for a draft.
    assert pr_status(_pr(is_draft=True, state="OPEN")) == "draft"
    assert pr_status(_pr(is_draft=True, state="CLOSED")) == "closed"
    assert pr_status(_pr(is_draft=False, state="OPEN")) == "open"


def test_draft_pill_still_emitted_alongside_pr_pill():
    # decide_pills keeps `draft` for the footer; only the cmux renderer drops it
    # (see tests/lib/test_cmux.py). Losing it here would take the footer's too.
    kinds = [p["kind"] for p in decide_pills(_pr(is_draft=True), _wt())]
    assert "draft" in kinds
    assert kinds[-1] == "pr"


def test_pr_pill_dropped_without_a_number():
    assert all(p["kind"] != "pr" for p in decide_pills(_pr(number=0), _wt()))


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
        "pr",
    ]


# ── muted pill ──────────────────────────────────────────────────────────────


def test_muted_first_in_kind_order():
    assert KIND_ORDER[0] == "muted"


def test_muted_pref_none_or_empty_emits_no_muted():
    assert [p["kind"] for p in decide_pills(_pr(), _wt(), pref=None)] == [
        "ci_passed",
        "pr",
    ]
    assert [p["kind"] for p in decide_pills(_pr(), _wt(), pref=NudgePref())] == [
        "ci_passed",
        "pr",
    ]


def test_muted_anchors_front():
    pref = NudgePref(muted=True)
    pills = decide_pills(_pr(ci="failed:lint", unaddressed=2), _wt(), pref=pref)
    assert pills[0] == {"kind": "muted"}


def test_muted_does_not_suppress_ci_passed_sentinel():
    pref = NudgePref(muted=True)
    kinds = [p["kind"] for p in decide_pills(_pr(), _wt(), pref=pref)]
    assert kinds == ["muted", "ci_passed", "pr"]


def test_muted_coexists_with_actionable_pills():
    pref = NudgePref(muted=True)
    pills = decide_pills(_pr(ci="failed:lint", unaddressed=2), _wt(), pref=pref)
    kinds = [p["kind"] for p in pills]
    assert kinds[0] == "muted"
    assert "ci_failed" in kinds
    assert "unaddressed" in kinds


def test_muted_with_expired_pref_clearing_via_load_pref(tmp_path, monkeypatch):
    # An expired pref returned by load_pref already has `muted` cleared (see
    # nudges.load_pref auto-expiry). Pass an explicit unmuted pref to mirror
    # that contract — no muted pill should appear.
    pref = NudgePref(muted=False, until=None)
    assert all(p["kind"] != "muted" for p in decide_pills(_pr(), _wt(), pref=pref))
