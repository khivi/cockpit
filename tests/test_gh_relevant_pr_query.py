"""Regression: $owner/$name must not be declared when unused.

gh's GraphQL validator rejects unused variable declarations. When a repo has
no coworker branches checked out locally, the `repo:` sub-block collapses to
empty and the only remaining reference is `$search`. Declaring `$owner`/
`$name` anyway caused the whole reconcile row to be skipped.
"""

from __future__ import annotations

import re

from lib.gh import _PR_LIGHT_FIELDS, _relevant_pr_query


def _referenced_vars(query: str) -> set[str]:
    return set(re.findall(r"\$(\w+)", query))


def _declared_vars(query: str) -> set[str]:
    header = query.split("{", 1)[0]
    return set(re.findall(r"\$(\w+)", header))


def test_no_coworkers_omits_owner_name():
    query, variables = _relevant_pr_query(
        "khivi", "cockpit", "khivi", [], _PR_LIGHT_FIELDS
    )
    assert "$owner" not in query
    assert "$name" not in query
    assert "owner" not in variables
    assert "name" not in variables
    assert variables["search"] == "repo:khivi/cockpit is:pr is:open author:khivi"
    assert _declared_vars(query) == _referenced_vars(query) >= {"search"}


def test_with_coworkers_declares_owner_name():
    query, variables = _relevant_pr_query(
        "khivi", "cockpit", "khivi", ["coworker/feature"], _PR_LIGHT_FIELDS
    )
    assert "$owner" in query
    assert "$name" in query
    assert variables["owner"] == "khivi"
    assert variables["name"] == "cockpit"
    assert variables["cw0"] == "coworker/feature"
    declared = _declared_vars(query)
    referenced = _referenced_vars(query)
    assert declared == referenced
    assert {"owner", "name", "search", "cw0"} <= declared


def test_all_declared_vars_are_referenced_no_coworkers():
    query, _ = _relevant_pr_query("o", "n", "u", [], _PR_LIGHT_FIELDS)
    assert _declared_vars(query) == _referenced_vars(query)


def test_all_declared_vars_are_referenced_many_coworkers():
    query, _ = _relevant_pr_query(
        "o", "n", "u", ["a/b", "c/d", "e/f"], _PR_LIGHT_FIELDS
    )
    assert _declared_vars(query) == _referenced_vars(query)
