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
        "baseRefName": "main",
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


def test_base_ref_parsed_from_node():
    node = _node()
    node["baseRefName"] = "stage"
    pr = _pr_from_node(node)
    assert pr is not None
    assert pr.base == "stage"


def test_base_ref_empty_when_absent():
    node = _node()
    del node["baseRefName"]
    pr = _pr_from_node(node)
    assert pr is not None
    assert pr.base == ""


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
    """A reviewer who COMMENTED then later APPROVED has signed off — only their
    most recent review counts, for BOTH unresolved and total: the reviewer is a
    single review item (now addressed), not two."""
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
    assert total == 1


def test_unaddressed_same_reviewer_multiple_reviews_counted_once():
    """A reviewer who posts several substantive reviews is one review item, not
    N — total must not exceed unresolved by double-counting their history."""
    reviews = [
        {
            "author": {"login": "alice", "__typename": "User"},
            "state": "COMMENTED",
            "body": "First pass: concern A.",
        },
        {
            "author": {"login": "alice", "__typename": "User"},
            "state": "CHANGES_REQUESTED",
            "body": "Second pass: concern B still stands.",
        },
    ]
    node = _pr_node_with_threads([], reviews=reviews)
    unresolved, total = _unaddressed(node, "khivi")
    assert unresolved == 1
    assert total == 1


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


def test_no_branch_protection_counts_all_failures():
    """Without branch protection, every failing check counts toward the CI
    roll-up — there is no skip-list to suppress noise checks."""
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


def test_required_checks_filter_excludes_noise():
    """When branch protection declares required checks, only those count —
    non-required noise (lint bots, optional workflows) is ignored."""
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


def test_empty_required_checks_counts_all():
    """A branch protection rule with no required checks (rule exists but
    empty list) behaves like no rule at all — every check counts."""
    runs = [
        {"name": "Tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "lint-bot", "status": "COMPLETED", "conclusion": "FAILURE"},
    ]
    pr = _pr_from_node(_node(runs, required_contexts=[]))
    assert pr is not None
    assert pr.ci == "failed:1"


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


def test_trunk_headed_pr_gets_synthesized_branch():
    """A PR whose head IS the trunk (merge main → feature) must not surface with
    branch `main` — that collapses onto the primary checkout. `_pr_from_node`
    synthesizes `pr-<N>-<base-slug>` so it joins to its own worktree/row."""
    node = _node()
    node["number"] = 280
    node["headRefName"] = "main"
    node["baseRefName"] = "feature/new_onboarding"
    pr = _pr_from_node(node)
    assert pr is not None
    assert pr.branch == "pr-280-new-onboarding"
    assert pr.base == "feature/new_onboarding"  # real base still recorded


def test_feature_headed_pr_branch_unchanged():
    pr = _pr_from_node(_node())  # headRefName "khivi/b"
    assert pr is not None
    assert pr.branch == "khivi/b"


# ── reviewDecision fallback (rulesets return null on approved PRs) ──────────


def _reviews_node(*states: tuple[str, str], author: str = "khivi") -> dict:
    """`_node()` whose reviewDecision is null and whose reviews are `(login, state)`."""
    node: dict = _node()
    node["reviewDecision"] = None
    node["reviews"] = {
        "nodes": [
            {"author": {"login": login, "__typename": "User"}, "state": state}
            for login, state in states
        ]
    }
    node["author"] = {"login": author, "__typename": "User"}
    return node


def test_null_review_decision_falls_back_to_an_approval():
    """A PR approved under a ruleset reports reviewDecision=null; mapping that
    to REVIEW_REQUIRED lost the approval and with it the `approved` pill."""
    pr = _pr_from_node(_reviews_node(("alice", "APPROVED")))
    assert pr is not None
    assert pr.review_decision == "APPROVED"


def test_null_review_decision_ignores_comment_reviews():
    """COMMENTED is not a verdict — it leaves the PR awaiting review."""
    pr = _pr_from_node(_reviews_node(("alice", "COMMENTED")))
    assert pr is not None
    assert pr.review_decision == "REVIEW_REQUIRED"


def test_null_review_decision_keeps_approval_behind_later_comments():
    """The live shape that surfaced this: six COMMENTED reviews then APPROVED,
    and later COMMENTED reviews from other logins after it."""
    pr = _pr_from_node(
        _reviews_node(
            ("alice", "COMMENTED"),
            ("alice", "APPROVED"),
            ("khivi", "COMMENTED"),
        )
    )
    assert pr is not None
    assert pr.review_decision == "APPROVED"


def test_null_review_decision_changes_requested_wins():
    pr = _pr_from_node(
        _reviews_node(("alice", "APPROVED"), ("bob", "CHANGES_REQUESTED"))
    )
    assert pr is not None
    assert pr.review_decision == "CHANGES_REQUESTED"


def test_null_review_decision_dismissal_clears_the_approval():
    pr = _pr_from_node(_reviews_node(("alice", "APPROVED"), ("alice", "DISMISSED")))
    assert pr is not None
    assert pr.review_decision == "REVIEW_REQUIRED"


def test_null_review_decision_ignores_the_authors_own_review():
    pr = _pr_from_node(_reviews_node(("khivi", "APPROVED")))
    assert pr is not None
    assert pr.review_decision == "REVIEW_REQUIRED"


def test_null_review_decision_ignores_bot_approvals():
    node = _reviews_node()
    node["reviews"] = {
        "nodes": [
            {
                "author": {"login": "copilot[bot]", "__typename": "Bot"},
                "state": "APPROVED",
            }
        ]
    }
    pr = _pr_from_node(node)
    assert pr is not None
    assert pr.review_decision == "REVIEW_REQUIRED"


def test_reported_review_decision_is_trusted_over_the_reviews():
    """GitHub's own verdict accounts for required counts, code owners and
    dismissals — none of which the review list can express, so it always wins."""
    node = _reviews_node(("alice", "APPROVED"))
    node["reviewDecision"] = "REVIEW_REQUIRED"
    pr = _pr_from_node(node)
    assert pr is not None
    assert pr.review_decision == "REVIEW_REQUIRED"
