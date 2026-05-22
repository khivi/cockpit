"""Pill decision tests targeting scripts/lib/pills.py.

`decide_pills` is the single source of truth for which pills a PR/worktree
combination should surface. These tests pin the decisions; consumer-side
mapping (cmux) lives in tests/lib/test_cmux.py.
"""

from __future__ import annotations

from pathlib import Path


from lib.gh import PR
from lib.git import Worktree
from lib.pills import decide_pills


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


def test_clean_open_pr_with_passing_ci_emits_ci_passed():
    # All-green PR: surface a sentinel ✓ so the sidebar isn't empty.
    assert decide_pills(_pr(), _wt()) == [{"kind": "ci_passed"}]


def test_clean_open_pr_without_ci_emits_no_pills():
    # No CI configured (or not yet queued) — no sentinel.
    assert decide_pills(_pr(ci="none"), _wt()) == []


def test_ci_passed_suppressed_when_other_pills_present():
    # `approved` already conveys readiness; don't double up with ci_passed.
    pills = decide_pills(_pr(review_decision="APPROVED"), _wt())
    kinds = [p["kind"] for p in pills]
    assert kinds == ["approved"]


def test_ci_passed_suppressed_when_unaddressed_present():
    pills = decide_pills(_pr(unaddressed=1), _wt())
    kinds = [p["kind"] for p in pills]
    assert "ci_passed" not in kinds
    assert "unaddressed" in kinds


def test_ci_passed_suppressed_for_merged_pr():
    # State pill (cmux-dropped) still counts as "other pill" → no sentinel.
    pills = decide_pills(_pr(state="MERGED"), _wt())
    kinds = [p["kind"] for p in pills]
    assert "ci_passed" not in kinds
    assert kinds == ["state"]


def test_ci_failed_carries_phase():
    pills = decide_pills(_pr(ci="failed:lint"), _wt())
    assert pills == [{"kind": "ci_failed", "phase": "lint"}]


def test_ci_failed_without_phase_marker():
    # `ci` is "failed" with no `:phase`; phase becomes empty string.
    pills = decide_pills(_pr(ci="failed"), _wt())
    assert pills == [{"kind": "ci_failed", "phase": ""}]


def test_ci_pending():
    assert decide_pills(_pr(ci="pending"), _wt()) == [{"kind": "ci_pending"}]


def test_unaddressed_supersedes_changes_requested():
    pills = decide_pills(_pr(unaddressed=3, review_decision="CHANGES_REQUESTED"), _wt())
    kinds = [p["kind"] for p in pills]
    assert "unaddressed" in kinds
    assert "changes_requested" not in kinds


def test_changes_requested_alone():
    pills = decide_pills(_pr(review_decision="CHANGES_REQUESTED"), _wt())
    assert pills == [{"kind": "changes_requested"}]


def test_conflict_pill():
    pills = decide_pills(_pr(mergeable="CONFLICTING"), _wt())
    assert pills == [{"kind": "conflict"}]


def test_draft_and_approved_coexist():
    pills = decide_pills(_pr(is_draft=True, review_decision="APPROVED"), _wt())
    kinds = [p["kind"] for p in pills]
    assert kinds == ["draft", "approved"]


def test_state_pill_only_for_non_open():
    # OPEN + ci=none → no pills; MERGED/CLOSED → state pill (and ci_passed is
    # suppressed by the state pill, see test_ci_passed_suppressed_for_merged_pr).
    assert decide_pills(_pr(state="OPEN", ci="none"), _wt()) == []
    assert decide_pills(_pr(state="MERGED", ci="none"), _wt()) == [
        {"kind": "state", "state": "MERGED"}
    ]
    assert decide_pills(_pr(state="CLOSED", ci="none"), _wt()) == [
        {"kind": "state", "state": "CLOSED"}
    ]


def test_worktree_pills_independent_of_pr():
    pills = decide_pills(_pr(), _wt(rebasing=True, dirty=4))
    assert pills == [
        {"kind": "rebase"},
        {"kind": "wip", "count": 4},
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
