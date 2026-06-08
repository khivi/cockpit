"""Regression: ci must aggregate every check run, not the first N.

The old query used `statusCheckRollup.contexts(first: 30)`, which silently
truncated repos that had more than 30 checks per PR. A failing check that
landed in slot 31+ was missed and `_pr_from_node` reported ci="passed" on a
red PR. The fix switched to `checkSuites { checkRuns(first: 100) }` plus
the legacy `status.contexts` array — together they cover every signal.
"""

from __future__ import annotations

from cockpit.lib.gh import _pr_from_node, _unaddressed


def _node(check_runs=None, legacy_contexts=None, required_contexts=None):
    """Build the minimal GraphQL response shape that _pr_from_node consumes.

    `required_contexts` (list of strings) sets baseRef.branchProtectionRule's
    requiredStatusChecks — when present, _pr_from_node uses it as the
    authoritative filter and ignores the skip-list.
    """
    base_ref: dict
    if required_contexts is None:
        base_ref = {"branchProtectionRule": None}
    else:
        base_ref = {
            "branchProtectionRule": {
                "requiredStatusChecks": [{"context": c} for c in required_contexts]
            }
        }
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
        "baseRef": base_ref,
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
    assert pr is not None
    assert pr.ci == "none"


def test_all_passing():
    pr = _pr_from_node(_node([{"status": "COMPLETED", "conclusion": "SUCCESS"}] * 3))
    assert pr is not None
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
    assert pr is not None
    assert pr.ci == "pending"


def test_failure_detected_past_thirty_runs():
    """Real-world bug: 64 success + 1 failure was reported as ci=passed
    because the truncated query only saw the first 30 successes."""
    runs = [{"status": "COMPLETED", "conclusion": "SUCCESS"}] * 64
    runs.append({"status": "COMPLETED", "conclusion": "FAILURE"})
    pr = _pr_from_node(_node(runs))
    assert pr is not None
    assert pr.ci == "failed:1"


def test_legacy_status_context_failure():
    pr = _pr_from_node(_node([], legacy_contexts=[{"state": "FAILURE"}]))
    assert pr is not None
    assert pr.ci == "failed:1"


def test_legacy_status_context_pending():
    pr = _pr_from_node(_node([], legacy_contexts=[{"state": "PENDING"}]))
    assert pr is not None
    assert pr.ci == "pending"


def test_mixed_check_run_and_legacy_failures_sum():
    pr = _pr_from_node(
        _node(
            [{"status": "COMPLETED", "conclusion": "FAILURE"}],
            legacy_contexts=[{"state": "FAILURE"}, {"state": "ERROR"}],
        )
    )
    assert pr is not None
    assert pr.ci == "failed:3"


def _thread(*, resolved: bool, authors: list) -> dict:
    return {
        "isResolved": resolved,
        "comments": {"nodes": [{"author": a} for a in authors]},
    }


def _pr_node_with_threads(
    threads: list, *, author: str = "khivi", reviews: list | None = None
) -> dict:
    return {
        "author": {"login": author, "__typename": "User"},
        "reviewThreads": {"nodes": threads},
        "reviews": {"nodes": reviews if reviews is not None else []},
    }


def test_unaddressed_copilot_null_author_counted():
    """GitHub Copilot inline review threads have author=null; null authors are
    actionable and count toward the unaddressed total."""
    node = _pr_node_with_threads([_thread(resolved=False, authors=[None])])
    unresolved, total = _unaddressed(node, "khivi")
    assert unresolved == 1
    assert total == 1


def test_unaddressed_copilot_null_author_resolved_not_counted():
    """Resolved null-author thread counts toward total but not unresolved."""
    node = _pr_node_with_threads([_thread(resolved=True, authors=[None])])
    unresolved, total = _unaddressed(node, "khivi")
    assert unresolved == 0
    assert total == 1


def test_unaddressed_author_replied_after_copilot_not_unresolved():
    """Thread started by null-author Copilot counts (total==1) but is addressed
    because the PR author replied last (unresolved==0)."""
    node = _pr_node_with_threads(
        [
            _thread(
                resolved=False, authors=[None, {"login": "khivi", "__typename": "User"}]
            )
        ]
    )
    unresolved, total = _unaddressed(node, "khivi")
    assert unresolved == 0
    assert total == 1


def test_unaddressed_named_bot_inline_thread_counted():
    """Bot inline code-review threads are actionable and count toward unaddressed."""
    node = _pr_node_with_threads(
        [
            _thread(
                resolved=False,
                authors=[{"login": "dependabot[bot]", "__typename": "Bot"}],
            )
        ]
    )
    unresolved, total = _unaddressed(node, "khivi")
    assert unresolved == 1
    assert total == 1


def test_unaddressed_bot_summary_review_not_counted():
    """Bot summary reviews (e.g. Copilot "I reviewed N files") are excluded
    from total — only inline threads from bots count."""
    reviews = [
        {
            "author": {"login": "copilot[bot]", "__typename": "Bot"},
            "body": "I reviewed 5 files.",
        }
    ]
    node = _pr_node_with_threads([], reviews=reviews)
    unresolved, total = _unaddressed(node, "khivi")
    assert unresolved == 0
    assert total == 0


def test_unaddressed_human_comment_review_counted():
    """A human reviewer's COMMENTED summary body is unaddressed even when the
    PR's reviewDecision stays REVIEW_REQUIRED (COMMENT reviews don't flip it)."""
    reviews = [
        {
            "author": {"login": "alice", "__typename": "User"},
            "state": "COMMENTED",
            "body": "Missing permissions block; daily-summary is broken.",
        }
    ]
    node = _pr_node_with_threads([], reviews=reviews)
    unresolved, total = _unaddressed(node, "khivi")
    assert unresolved == 1
    assert total == 1


def test_unaddressed_human_changes_requested_review_counted():
    reviews = [
        {
            "author": {"login": "alice", "__typename": "User"},
            "state": "CHANGES_REQUESTED",
            "body": "Please fix the SARIF upload.",
        }
    ]
    node = _pr_node_with_threads([], reviews=reviews)
    unresolved, total = _unaddressed(node, "khivi")
    assert unresolved == 1
    assert total == 1


def test_unaddressed_human_approved_review_not_unresolved():
    """An APPROVED summary review carries no pending feedback."""
    reviews = [
        {
            "author": {"login": "alice", "__typename": "User"},
            "state": "APPROVED",
            "body": "LGTM, nice work.",
        }
    ]
    node = _pr_node_with_threads([], reviews=reviews)
    unresolved, total = _unaddressed(node, "khivi")
    assert unresolved == 0
    assert total == 1


def test_unaddressed_later_approval_clears_earlier_comment():
    """A reviewer who COMMENTED then later APPROVED has signed off — their
    most recent review wins, so the earlier feedback no longer counts."""
    reviews = [
        {
            "author": {"login": "alice", "__typename": "User"},
            "state": "COMMENTED",
            "body": "A few concerns here.",
        },
        {
            "author": {"login": "alice", "__typename": "User"},
            "state": "APPROVED",
            "body": "Resolved, approving.",
        },
    ]
    node = _pr_node_with_threads([], reviews=reviews)
    unresolved, total = _unaddressed(node, "khivi")
    assert unresolved == 0
    assert total == 2


def test_unaddressed_empty_body_comment_not_unresolved():
    """A COMMENTED review with no body carries no actionable text."""
    reviews = [
        {
            "author": {"login": "alice", "__typename": "User"},
            "state": "COMMENTED",
            "body": "",
        }
    ]
    node = _pr_node_with_threads([], reviews=reviews)
    unresolved, total = _unaddressed(node, "khivi")
    assert unresolved == 0
    assert total == 0


def test_copilot_reviewer_failure_ignored():
    """copilot-pull-request-reviewer crashes due to GitHub API bugs unrelated
    to the PR's code; its failure must not count toward ci=failed when the
    repo's per-repo ci_skip_checks lists it."""
    runs = [
        {"name": "Tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {
            "name": "copilot-pull-request-reviewer",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
        },
    ]
    pr = _pr_from_node(_node(runs), skip_checks={"copilot-pull-request-reviewer"})
    assert pr is not None
    assert pr.ci == "passed"


def test_copilot_reviewer_excluded_but_real_failure_still_counted():
    runs = [
        {"name": "Tests", "status": "COMPLETED", "conclusion": "FAILURE"},
        {
            "name": "copilot-pull-request-reviewer",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
        },
    ]
    pr = _pr_from_node(_node(runs), skip_checks={"copilot-pull-request-reviewer"})
    assert pr is not None
    assert pr.ci == "failed:1"


def test_no_skip_checks_counts_all_failures():
    """ci_skip_checks is per-repo only — there is no implicit global default.
    With no skip_checks passed, every failing check counts, including the
    copilot reviewer."""
    runs = [
        {"name": "Tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {
            "name": "copilot-pull-request-reviewer",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
        },
    ]
    pr = _pr_from_node(_node(runs))
    assert pr is not None
    assert pr.ci == "failed:1"


def test_required_checks_filter_overrides_skip_list():
    """When branch protection declares required checks, only those count —
    non-required noise (lint bots, optional workflows) is ignored regardless
    of whether the skip-list mentions them."""
    runs = [
        {"name": "Tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "lint-bot", "status": "COMPLETED", "conclusion": "FAILURE"},
        {"name": "optional-flake", "status": "IN_PROGRESS", "conclusion": None},
    ]
    pr = _pr_from_node(_node(runs, required_contexts=["Tests"]))
    assert pr is not None
    assert pr.ci == "passed"


def test_required_checks_failure_still_counts():
    """A required check that fails must produce ci=failed even if other
    non-required checks pass."""
    runs = [
        {"name": "Tests", "status": "COMPLETED", "conclusion": "FAILURE"},
        {"name": "lint-bot", "status": "COMPLETED", "conclusion": "SUCCESS"},
    ]
    pr = _pr_from_node(_node(runs, required_contexts=["Tests"]))
    assert pr is not None
    assert pr.ci == "failed:1"


def test_required_checks_overrides_skip_list_for_required_failure():
    """If a required check name happens to match a skip-list entry, branch
    protection still wins — the required failure must surface."""
    runs = [
        {
            "name": "copilot-pull-request-reviewer",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
        },
    ]
    pr = _pr_from_node(
        _node(runs, required_contexts=["copilot-pull-request-reviewer"]),
        skip_checks={"copilot-pull-request-reviewer"},
    )
    assert pr is not None
    assert pr.ci == "failed:1"


def test_no_required_checks_falls_back_to_skip_list():
    """Repos without branch protection (branchProtectionRule=null) use the
    skip-list as before."""
    runs = [
        {"name": "Tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {
            "name": "copilot-pull-request-reviewer",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
        },
    ]
    pr = _pr_from_node(_node(runs), skip_checks={"copilot-pull-request-reviewer"})
    assert pr is not None
    assert pr.ci == "passed"


def test_empty_required_checks_falls_back_to_skip_list():
    """A branch protection rule with no required checks (rule exists but
    empty list) behaves like no rule at all — fall back to skip-list."""
    runs = [
        {"name": "Tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {
            "name": "copilot-pull-request-reviewer",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
        },
    ]
    pr = _pr_from_node(
        _node(runs, required_contexts=[]),
        skip_checks={"copilot-pull-request-reviewer"},
    )
    assert pr is not None
    assert pr.ci == "passed"


def test_required_checks_filter_legacy_contexts():
    """Legacy status contexts are also filtered by the required-checks set
    when branch protection is configured."""
    pr = _pr_from_node(
        _node(
            [],
            legacy_contexts=[
                {"context": "ci/required", "state": "FAILURE"},
                {"context": "ci/optional", "state": "FAILURE"},
            ],
            required_contexts=["ci/required"],
        )
    )
    assert pr is not None
    assert pr.ci == "failed:1"


def test_null_check_suites_yields_unknown():
    """checkSuites is a non-null connection type in GH's GraphQL schema, so an
    explicit `null` only happens when the field resolver errored (typically a
    GH Actions outage). Surface that as ci="unknown" — not ci="none" — so the
    sidebar/footer render an explicit error indicator instead of silently
    hiding the CI signal."""
    node = _node()
    node["commits"]["nodes"][0]["commit"]["checkSuites"] = None
    pr = _pr_from_node(node)
    assert pr is not None
    assert pr.ci == "unknown"
