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

import pytest

from scripts.lib.gh import (
    _PR_LIGHT_FIELDS,
    OpenPRHead,
    _graphql,
    _relevant_pr_query,
    fetch_merged_branches,
    list_open_pr_heads,
    require_gh,
)


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


def _page(nodes: list[dict], *, end: str = "", more: bool = False) -> dict:
    return {
        "data": {
            "search": {
                "pageInfo": {"endCursor": end, "hasNextPage": more},
                "nodes": nodes,
            }
        }
    }


def _node(num: int, branch: str, oid: str) -> dict:
    return {"number": num, "headRefName": branch, "headRefOid": oid}


def test_fetch_merged_branches_single_page():
    pages = [_page([_node(1, "feat/a", "sha-a"), _node(2, "feat/b", "sha-b")])]
    with patch("scripts.lib.gh._graphql", side_effect=pages):
        result = fetch_merged_branches("o", "n")
    assert result == {"feat/a": "sha-a", "feat/b": "sha-b"}


def test_fetch_merged_branches_pages_until_no_next_page():
    """Cross-page accumulation: keep paginating while hasNextPage is True."""
    pages = [
        _page([_node(10, "feat/a", "sha-a1")], end="c1", more=True),
        _page([_node(11, "feat/b", "sha-b1")], end="c2", more=True),
        _page([_node(12, "feat/c", "sha-c1")]),  # hasNextPage=False
    ]
    with patch("scripts.lib.gh._graphql", side_effect=pages) as m:
        result = fetch_merged_branches("o", "n", max_pages=10)
    assert m.call_count == 3
    assert result == {"feat/a": "sha-a1", "feat/b": "sha-b1", "feat/c": "sha-c1"}
    # Second call onward must carry the previous endCursor.
    assert m.call_args_list[1].args[1]["cursor"] == "c1"
    assert m.call_args_list[2].args[1]["cursor"] == "c2"


def test_fetch_merged_branches_stops_at_max_pages_cap():
    """`max_pages` cap stops pagination even when hasNextPage is True."""
    pages = [
        _page([_node(1, "feat/a", "sha-a")], end="c1", more=True),
        _page([_node(2, "feat/b", "sha-b")], end="c2", more=True),
        _page([_node(3, "feat/c", "sha-c")], end="c3", more=True),
    ]
    with patch("scripts.lib.gh._graphql", side_effect=pages) as m:
        result = fetch_merged_branches("o", "n", max_pages=2)
    assert m.call_count == 2
    assert result == {"feat/a": "sha-a", "feat/b": "sha-b"}


def test_fetch_merged_branches_highest_pr_wins_across_pages():
    """When a branch appears in multiple merged PRs, keep the highest PR
    number. The headRefOid for the older merge is stale — autoclose must gate
    on the most recent merge.
    """
    pages = [
        _page([_node(50, "feat/a", "sha-new")], end="c1", more=True),
        _page([_node(10, "feat/a", "sha-old")]),
    ]
    with patch("scripts.lib.gh._graphql", side_effect=pages):
        result = fetch_merged_branches("o", "n")
    assert result == {"feat/a": "sha-new"}


def test_fetch_merged_branches_empty_search_returns_empty_map():
    pages = [_page([])]
    with patch("scripts.lib.gh._graphql", side_effect=pages):
        assert fetch_merged_branches("o", "n") == {}


# ── list_open_pr_heads ───────────────────────────────────────────────────────


def _pr_head_node(num: int, branch: str, author: str | None) -> dict:
    return {
        "number": num,
        "headRefName": branch,
        "author": {"login": author} if author is not None else None,
    }


def test_list_open_pr_heads_single_page():
    pages = [
        _page(
            [
                _pr_head_node(1, "coworker/a", "coworker"),
                _pr_head_node(2, "khivi/b", "khivi"),
            ]
        )
    ]
    with patch("scripts.lib.gh._graphql", side_effect=pages) as m:
        result = list_open_pr_heads("o", "n")
    assert result == [
        OpenPRHead(1, "coworker/a", "coworker"),
        OpenPRHead(2, "khivi/b", "khivi"),
    ]
    # The search must drop the author filter — review_prs wants ALL open PRs.
    assert m.call_args_list[0].args[1]["search"] == "repo:o/n is:pr is:open"


def test_list_open_pr_heads_paginates_until_no_next_page():
    pages = [
        _page([_pr_head_node(10, "feat/a", "a")], end="c1", more=True),
        _page([_pr_head_node(11, "feat/b", "b")], end="c2", more=True),
        _page([_pr_head_node(12, "feat/c", "c")]),
    ]
    with patch("scripts.lib.gh._graphql", side_effect=pages) as m:
        result = list_open_pr_heads("o", "n")
    assert m.call_count == 3
    assert [h.number for h in result] == [10, 11, 12]
    assert m.call_args_list[1].args[1]["cursor"] == "c1"
    assert m.call_args_list[2].args[1]["cursor"] == "c2"


def test_list_open_pr_heads_null_author_becomes_empty_string():
    """Bots (Copilot/dependabot) return author=null — reported as "" so the
    caller can decide to skip or include them explicitly."""
    pages = [_page([_pr_head_node(5, "dependabot/x", None)])]
    with patch("scripts.lib.gh._graphql", side_effect=pages):
        result = list_open_pr_heads("o", "n")
    assert result == [OpenPRHead(5, "dependabot/x", "")]


def test_list_open_pr_heads_empty_on_graphql_failure():
    import subprocess

    with patch(
        "scripts.lib.gh._graphql",
        side_effect=subprocess.CalledProcessError(1, "gh"),
    ):
        assert list_open_pr_heads("o", "n") == []


def test_fetch_merged_branches_graphql_failure_returns_empty_map():
    import subprocess as _sp

    err = _sp.CalledProcessError(1, ["gh", "api", "graphql"])
    with patch("scripts.lib.gh._graphql", side_effect=err):
        assert fetch_merged_branches("o", "n") == {}


def test_fetch_merged_branches_search_includes_date_window():
    """The `merged:>=<date>` qualifier scopes the search to recent merges. The
    date is computed from `cutoff_days` and must be present in the search var.
    """
    captured: dict[str, str] = {}

    def _capture(_query: str, variables: dict[str, str]) -> dict:
        captured.update(variables)
        return _page([])

    with patch("scripts.lib.gh._graphql", side_effect=_capture):
        fetch_merged_branches("acme", "widgets", cutoff_days=7)
    assert "repo:acme/widgets is:pr is:merged merged:>=" in captured["search"]
    # No cursor on the first page request.
    assert "cursor" not in captured


def test_require_gh_exits_when_missing(monkeypatch, capsys):
    """A missing `gh` binary surfaces a structured install hint and exit code 2,
    not a bare FileNotFoundError deep inside a daemon cycle.
    """

    def _raise_fnf(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("scripts.lib.gh.subprocess.run", _raise_fnf)
    with pytest.raises(SystemExit) as excinfo:
        require_gh()
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "gh" in err
    assert "https://cli.github.com" in err


def test_require_gh_returns_when_present(monkeypatch):
    monkeypatch.setattr("scripts.lib.gh.subprocess.run", lambda *_a, **_kw: None)
    require_gh()


# ── _pr_from_node head_oid parsing (reused-branch suppression signal) ───────


def _full_pr_node(**over: object) -> dict:
    node: dict = {
        "number": 7,
        "title": "t",
        "url": "u",
        "isDraft": False,
        "headRefName": "khivi/feat",
        "headRefOid": "cafef00d",
        "mergeable": "MERGEABLE",
        "reviewDecision": "APPROVED",
        "updatedAt": "2025-01-01",
        "state": "MERGED",
        "author": {"login": "khivi"},
        "baseRef": {"branchProtectionRule": None},
        "reviewThreads": {"nodes": []},
        "reviews": {"nodes": []},
        "commits": {
            "nodes": [{"commit": {"checkSuites": {"nodes": []}, "status": None}}]
        },
    }
    node.update(over)
    return node


def test_pr_from_node_parses_head_oid():
    from scripts.lib.gh import _pr_from_node

    pr = _pr_from_node(_full_pr_node())
    assert pr is not None
    assert pr.head_oid == "cafef00d"


def test_pr_from_node_head_oid_absent_is_none():
    """An old PR node lacking headRefOid yields head_oid=None — the suppression
    gate then falls through (never hides a real PR)."""
    from scripts.lib.gh import _pr_from_node

    node = _full_pr_node()
    del node["headRefOid"]
    pr = _pr_from_node(node)
    assert pr is not None and pr.head_oid is None


def test_pr_fields_query_includes_head_ref_oid():
    from scripts.lib.gh import _PR_FIELDS

    assert "headRefOid" in _PR_FIELDS
