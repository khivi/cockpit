"""Regression: ci must aggregate every check run, not the first N.

The old query used `statusCheckRollup.contexts(first: 30)`, which silently
truncated repos that had more than 30 checks per PR. A failing check that
landed in slot 31+ was missed and `_pr_from_node` reported ci="passed" on a
red PR. The fix switched to `checkSuites { checkRuns(first: 100) }` plus
the legacy `status.contexts` array — together they cover every signal.
"""

from __future__ import annotations

from scripts.lib.gh import _pr_from_node


def _node(check_runs=None, legacy_contexts=None):
    """Build the minimal GraphQL response shape that _pr_from_node consumes."""
    return {
        "number": 1,
        "title": "t",
        "url": "u",
        "isDraft": False,
        "headRefName": "khivi/b",
        "mergeable": "MERGEABLE",
        "reviewDecision": "REVIEW_REQUIRED",
        "updatedAt": "",
        "state": "OPEN",
        "author": {"login": "khivi", "__typename": "User"},
        "reviewThreads": {"nodes": []},
        "reviews": {"nodes": []},
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "checkSuites": {
                            "nodes": [{"checkRuns": {"nodes": check_runs or []}}]
                        },
                        "status": (
                            {"contexts": legacy_contexts}
                            if legacy_contexts is not None
                            else None
                        ),
                    }
                }
            ]
        },
    }


def test_no_checks_yields_none():
    pr = _pr_from_node(_node())
    assert pr.ci == "none"


def test_all_passing():
    pr = _pr_from_node(_node([{"status": "COMPLETED", "conclusion": "SUCCESS"}] * 3))
    assert pr.ci == "passed"


def test_pending_overrides_failure():
    pr = _pr_from_node(
        _node(
            [
                {"status": "IN_PROGRESS", "conclusion": None},
                {"status": "COMPLETED", "conclusion": "FAILURE"},
            ]
        )
    )
    assert pr.ci == "pending"


def test_failure_detected_past_thirty_runs():
    """Real-world bug: 64 success + 1 failure was reported as ci=passed
    because the truncated query only saw the first 30 successes."""
    runs = [{"status": "COMPLETED", "conclusion": "SUCCESS"}] * 64
    runs.append({"status": "COMPLETED", "conclusion": "FAILURE"})
    pr = _pr_from_node(_node(runs))
    assert pr.ci == "failed:1"


def test_legacy_status_context_failure():
    pr = _pr_from_node(_node([], legacy_contexts=[{"state": "FAILURE"}]))
    assert pr.ci == "failed:1"


def test_legacy_status_context_pending():
    pr = _pr_from_node(_node([], legacy_contexts=[{"state": "PENDING"}]))
    assert pr.ci == "pending"


def test_mixed_check_run_and_legacy_failures_sum():
    pr = _pr_from_node(
        _node(
            [{"status": "COMPLETED", "conclusion": "FAILURE"}],
            legacy_contexts=[{"state": "FAILURE"}, {"state": "ERROR"}],
        )
    )
    assert pr.ci == "failed:3"
