"""Regression: $owner/$name must not be declared when unused.

gh's GraphQL validator rejects unused variable declarations. When a repo has
no local worktree branches, the `repo:` sub-block collapses to empty and the
only remaining reference is `$search`. Declaring `$owner`/`$name` anyway
caused the whole reconcile row to be skipped.
"""

from __future__ import annotations

import json
import re
from unittest.mock import patch

from scripts.lib.gh import _PR_LIGHT_FIELDS, _graphql, _relevant_pr_query


def _referenced_vars(query: str) -> set[str]:
    return set(re.findall(r"\$(\w+)", query))


def _declared_vars(query: str) -> set[str]:
    header = query.split("{", 1)[0]
    return set(re.findall(r"\$(\w+)", header))


def test_no_branches_omits_owner_name():
    query, variables = _relevant_pr_query(
        "khivi", "cockpit", "khivi", [], _PR_LIGHT_FIELDS
    )
    assert "$owner" not in query
    assert "$name" not in query
    assert "owner" not in variables
    assert "name" not in variables
    assert variables["search"] == "repo:khivi/cockpit is:pr is:open author:khivi"
    assert _declared_vars(query) == _referenced_vars(query) >= {"search"}


def test_with_branches_declares_owner_name():
    query, variables = _relevant_pr_query(
        "khivi", "cockpit", "khivi", ["coworker/feature"], _PR_LIGHT_FIELDS
    )
    assert "$owner" in query
    assert "$name" in query
    assert variables["owner"] == "khivi"
    assert variables["name"] == "cockpit"
    assert variables["b0"] == "coworker/feature"
    declared = _declared_vars(query)
    referenced = _referenced_vars(query)
    assert declared == referenced
    assert {"owner", "name", "search", "b0"} <= declared


def test_all_declared_vars_are_referenced_no_branches():
    query, _ = _relevant_pr_query("o", "n", "u", [], _PR_LIGHT_FIELDS)
    assert _declared_vars(query) == _referenced_vars(query)


def test_all_declared_vars_are_referenced_many_branches():
    query, _ = _relevant_pr_query(
        "o", "n", "u", ["a/b", "c/d", "e/f"], _PR_LIGHT_FIELDS
    )
    assert _declared_vars(query) == _referenced_vars(query)


def test_graphql_passes_through_errors_field():
    """A GitHub outage returns 200 OK with partial `data` plus an `errors`
    array (e.g. checkSuites nulled out by a downstream timeout). _graphql must
    pass that response through so _pr_from_node can surface ci="unknown" on
    the affected PRs — raising here would drop the whole cycle and prevent the
    "ci error" pill/footer indicator from rendering.
    """
    payload = json.dumps(
        {
            "data": {"mine": {"nodes": []}},
            "errors": [{"type": "SERVICE_UNAVAILABLE", "message": "Actions down"}],
        }
    )
    with patch("scripts.lib.gh.run", return_value=payload):
        data = _graphql("query { mine }", {})
    assert data["errors"][0]["type"] == "SERVICE_UNAVAILABLE"


def test_graphql_returns_data_when_no_errors():
    payload = json.dumps({"data": {"mine": {"nodes": []}}})
    with patch("scripts.lib.gh.run", return_value=payload):
        data = _graphql("query { mine }", {})
    assert data == {"data": {"mine": {"nodes": []}}}


def test_per_branch_leg_is_any_state():
    """The per-branch alias must not filter by state — the daemon's tick
    refreshes the per-PR cache after OPEN→MERGED / OPEN→CLOSED transitions,
    so the statusline footer doesn't freeze at the last pre-merge snapshot.
    """
    query, _ = _relevant_pr_query(
        "khivi", "cockpit", "khivi", ["khivi/side"], _PR_LIGHT_FIELDS
    )
    assert "states: OPEN" not in query
    assert "states:" not in query
    # newest PR for the branch wins when multiple exist for the same head
    assert "orderBy: {field: CREATED_AT, direction: DESC}" in query
    assert "first: 1" in query
