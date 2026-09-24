"""Regression: $owner/$name must not be declared when unused.

gh's GraphQL validator rejects unused variable declarations. When a repo has
no local worktree branches, the `repo:` sub-block collapses to empty and the
only remaining reference is `$search`. Declaring `$owner`/`$name` anyway
caused the whole reconcile row to be skipped.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from cockpit.lib.gh import (
    _PR_LIGHT_FIELDS,
    OpenPRHead,
    _collect_nodes,
    _fetch_light_phase,
    _graphql,
    _hydrate_stale,
    _identify_stale,
    _one_pr_per_branch,
    _relevant_pr_query,
    fetch_merged_branches,
    fetch_pr_state_for_branch,
    list_open_pr_heads,
    list_relevant_prs,
    pr_worktree_branch,
    repo_nwo,
    require_gh,
    resolve_pr_branch,
    update_pull_request_branch,
)
from cockpit.lib.git import branch_label


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
    with patch("cockpit.lib.gh.run", return_value=payload):
        data = _graphql("query { mine }", {})
    assert data["errors"][0]["type"] == "SERVICE_UNAVAILABLE"


def test_graphql_returns_data_when_no_errors():
    payload = json.dumps({"data": {"mine": {"nodes": []}}})
    with patch("cockpit.lib.gh.run", return_value=payload):
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
    with patch("cockpit.lib.gh._graphql", side_effect=pages):
        result = fetch_merged_branches("o", "n")
    assert result == {"feat/a": "sha-a", "feat/b": "sha-b"}


def test_fetch_merged_branches_pages_until_no_next_page():
    """Cross-page accumulation: keep paginating while hasNextPage is True."""
    pages = [
        _page([_node(10, "feat/a", "sha-a1")], end="c1", more=True),
        _page([_node(11, "feat/b", "sha-b1")], end="c2", more=True),
        _page([_node(12, "feat/c", "sha-c1")]),  # hasNextPage=False
    ]
    with patch("cockpit.lib.gh._graphql", side_effect=pages) as m:
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
    with patch("cockpit.lib.gh._graphql", side_effect=pages) as m:
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
    with patch("cockpit.lib.gh._graphql", side_effect=pages):
        result = fetch_merged_branches("o", "n")
    assert result == {"feat/a": "sha-new"}


def test_fetch_merged_branches_empty_search_returns_empty_map():
    pages = [_page([])]
    with patch("cockpit.lib.gh._graphql", side_effect=pages):
        assert fetch_merged_branches("o", "n") == {}


# ── list_open_pr_heads ───────────────────────────────────────────────────────


def _pr_head_node(
    num: int, branch: str, author: str | None, association: str | None = None
) -> dict:
    node = {
        "number": num,
        "headRefName": branch,
        "author": {"login": author} if author is not None else None,
    }
    if association is not None:
        node["authorAssociation"] = association
    return node


def test_list_open_pr_heads_single_page():
    pages = [
        _page(
            [
                _pr_head_node(1, "coworker/a", "coworker"),
                _pr_head_node(2, "khivi/b", "khivi"),
            ]
        )
    ]
    with patch("cockpit.lib.gh._graphql", side_effect=pages) as m:
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
    with patch("cockpit.lib.gh._graphql", side_effect=pages) as m:
        result = list_open_pr_heads("o", "n")
    assert m.call_count == 3
    assert [h.number for h in result] == [10, 11, 12]
    assert m.call_args_list[1].args[1]["cursor"] == "c1"
    assert m.call_args_list[2].args[1]["cursor"] == "c2"


def test_list_open_pr_heads_null_author_becomes_empty_string():
    """Bots (Copilot/dependabot) return author=null — reported as "" so the
    caller can decide to skip or include them explicitly."""
    pages = [_page([_pr_head_node(5, "dependabot/x", None)])]
    with patch("cockpit.lib.gh._graphql", side_effect=pages):
        result = list_open_pr_heads("o", "n")
    assert result == [OpenPRHead(5, "dependabot/x", "")]


def test_list_open_pr_heads_returns_author_association():
    """`authorAssociation` is fetched and threaded onto each candidate — the
    `review_prs` spawn gate needs it to skip non-collaborator PRs."""
    pages = [
        _page(
            [
                _pr_head_node(1, "coworker/a", "coworker", "COLLABORATOR"),
                _pr_head_node(2, "outside/b", "rando", "CONTRIBUTOR"),
            ]
        )
    ]
    with patch("cockpit.lib.gh._graphql", side_effect=pages):
        result = list_open_pr_heads("o", "n")
    assert result == [
        OpenPRHead(1, "coworker/a", "coworker", "COLLABORATOR"),
        OpenPRHead(2, "outside/b", "rando", "CONTRIBUTOR"),
    ]


def test_list_open_pr_heads_missing_author_association_becomes_empty_string():
    """A node without `authorAssociation` (e.g. an older gh/API shape) reports
    "" rather than raising — mirrors the null-author "" contract."""
    pages = [_page([_pr_head_node(6, "coworker/c", "coworker")])]
    with patch("cockpit.lib.gh._graphql", side_effect=pages):
        result = list_open_pr_heads("o", "n")
    assert result == [OpenPRHead(6, "coworker/c", "coworker", "")]


def test_list_open_pr_heads_empty_on_graphql_failure():
    """Degrades per its documented contract. The call chain (`_graphql` →
    `gh_json` → `run()`) raises RuntimeError on a `gh` failure, never
    CalledProcessError — the except clause must match what's actually raised
    or this degrade path is dead and a transient gh failure aborts the whole
    repo cycle instead.
    """
    with patch(
        "cockpit.lib.gh._graphql",
        side_effect=RuntimeError("gh api graphql failed"),
    ):
        assert list_open_pr_heads("o", "n") == []


def test_fetch_merged_branches_graphql_failure_returns_empty_map():
    """Same dead-path concern as list_open_pr_heads: `run()` raises
    RuntimeError, so that's what fetch_merged_branches must catch."""
    with patch(
        "cockpit.lib.gh._graphql", side_effect=RuntimeError("gh api graphql failed")
    ):
        assert fetch_merged_branches("o", "n") == {}


def test_fetch_merged_branches_search_includes_date_window():
    """The `merged:>=<date>` qualifier scopes the search to recent merges. The
    date is computed from `cutoff_days` and must be present in the search var.
    """
    captured: dict[str, str] = {}

    def _capture(_query: str, variables: dict[str, str]) -> dict:
        captured.update(variables)
        return _page([])

    with patch("cockpit.lib.gh._graphql", side_effect=_capture):
        fetch_merged_branches("acme", "widgets", cutoff_days=7)
    assert "repo:acme/widgets is:pr is:merged merged:>=" in captured["search"]
    # No cursor on the first page request.
    assert "cursor" not in captured


def test_fetch_merged_branches_clamps_an_all_time_cutoff_to_the_epoch():
    """A `cutoff_days` meaning "all time" must not produce a pre-epoch date.

    GitHub search answers `merged:>=1926-10-11` with a zero-result page rather
    than an error, so the map came back empty and read as "no merged PRs" —
    which left `_reap_branch_refs` with no reason to delete any merged branch,
    on every repo, permanently.
    """
    captured: dict[str, str] = {}

    def _capture(_query: str, variables: dict[str, str]) -> dict:
        captured.update(variables)
        return _page([])

    with patch("cockpit.lib.gh._graphql", side_effect=_capture):
        fetch_merged_branches("acme", "widgets", cutoff_days=36500)
    assert "merged:>=1970-01-01" in captured["search"]


def test_fetch_merged_branches_keeps_a_normal_cutoff_unclamped():
    """The clamp is a floor, not a rewrite — an ordinary window is unchanged."""
    captured: dict[str, str] = {}
    expected = (datetime.now(UTC) - timedelta(days=14)).strftime("%Y-%m-%d")

    def _capture(_query: str, variables: dict[str, str]) -> dict:
        captured.update(variables)
        return _page([])

    with patch("cockpit.lib.gh._graphql", side_effect=_capture):
        fetch_merged_branches("acme", "widgets", cutoff_days=14)
    assert f"merged:>={expected}" in captured["search"]


def test_require_gh_exits_when_missing(monkeypatch, capsys):
    """A missing `gh` binary surfaces a structured install hint and exit code 2,
    not a bare FileNotFoundError deep inside a daemon cycle.
    """

    def _raise_fnf(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("cockpit.lib.gh.subprocess.run", _raise_fnf)
    with pytest.raises(SystemExit) as excinfo:
        require_gh()
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "gh" in err
    assert "https://cli.github.com" in err


def test_require_gh_returns_when_present(monkeypatch):
    monkeypatch.setattr("cockpit.lib.gh.subprocess.run", lambda *_a, **_kw: None)
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
    from cockpit.lib.gh import _pr_from_node

    pr = _pr_from_node(_full_pr_node())
    assert pr is not None
    assert pr.head_oid == "cafef00d"


def test_pr_from_node_head_oid_absent_is_none():
    """An old PR node lacking headRefOid yields head_oid=None — the suppression
    gate then falls through (never hides a real PR)."""
    from cockpit.lib.gh import _pr_from_node

    node = _full_pr_node()
    del node["headRefOid"]
    pr = _pr_from_node(node)
    assert pr is not None and pr.head_oid is None


def test_pr_fields_query_includes_head_ref_oid():
    from cockpit.lib.gh import _PR_FIELDS

    assert "headRefOid" in _PR_FIELDS


# ── _identify_stale — force a refetch on cached ci="unknown" ────────────────


def test_identify_stale_refetches_unknown_ci_even_when_updated_at_unchanged():
    """`_pr_from_node` can cache ci="unknown" on a transient checkSuites null.
    CI changes don't bump updatedAt, so without treating "unknown" like
    "pending" here, an unknown-CI PR would never refresh."""
    pr = _issue_pr(ci="unknown")
    cache = {1: (pr, "2025-01-01")}
    stale = _identify_stale({1: "2025-01-01"}, cache)
    assert stale == [1]


# ── one PR per head branch — the join key every downstream reader uses ──────


@pytest.mark.covers("gh.pr-rank.not-number-alone~1")
def test_a_closed_duplicate_never_wins_its_branch():
    """The live shape that churned a workspace every slow tick: a duplicate PR
    opened seconds after the real one and closed. It carries the *higher*
    number and the newer updatedAt, so any "newest wins" rule resolves the
    branch to the dead PR — `match_worktrees` then pairs the worktree with the
    open one that no branch map points at, and the cycle spawns it a workspace
    forever."""
    live = _issue_pr(number=335, state="OPEN", updated_at="2026-08-26T17:50:01Z")
    dupe = _issue_pr(number=336, state="CLOSED", updated_at="2026-08-26T17:50:12Z")
    assert [pr.number for pr in _one_pr_per_branch([live, dupe])] == [335]
    assert [pr.number for pr in _one_pr_per_branch([dupe, live])] == [335]


def test_the_sole_pr_on_a_branch_survives_whatever_its_state():
    """The per-branch fetch leg exists to keep a MERGED/CLOSED PR's cache fresh
    after the transition. Collapsing must not drop one that has no open rival."""
    merged = _issue_pr(number=10, state="MERGED", branch="khivi/done")
    assert _one_pr_per_branch([merged]) == [merged]


def test_distinct_branches_keep_every_pr_in_order():
    prs = [
        _issue_pr(number=1, branch="khivi/a"),
        _issue_pr(number=2, branch="khivi/b"),
        _issue_pr(number=3, branch="khivi/c"),
    ]
    assert _one_pr_per_branch(prs) == prs


def test_list_relevant_prs_returns_one_pr_per_branch():
    """The guarantee holds through the two-phase fetch, where the `author:self`
    search leg and the per-branch alias leg can each contribute one."""
    live = _issue_pr(number=335, state="OPEN", updated_at="a")
    dupe = _issue_pr(number=336, state="CLOSED", updated_at="b")
    cache = {335: (live, "a"), 336: (dupe, "b")}
    with patch("cockpit.lib.gh._fetch_light_phase", return_value={335: "a", 336: "b"}):
        prs = list_relevant_prs("o", "n", "khivi", ["khivi/feature"], cache=cache)
    assert [pr.number for pr in prs] == [335]


# ── PR.nudge_issue — single source for the nudge decision + TUI 🔔 ──────────


def _issue_pr(**overrides):
    from cockpit.lib.gh import PR

    base: dict = {
        "number": 1,
        "title": "t",
        "branch": "khivi/feature",
        "url": "https://example/pr/1",
        "author": "khivi",
        "is_draft": False,
        "review_decision": "REVIEW_REQUIRED",
        "mergeable": "MERGEABLE",
        "ci": "passed",
        "unaddressed": 0,
        "total_from_others": 0,
        "state": "OPEN",
    }
    base.update(overrides)
    return PR(**base)


@pytest.mark.parametrize(
    "overrides,expected",
    [
        # Actionable categories on an OPEN PR.
        ({"ci": "failed:lint"}, "ci"),
        ({"unaddressed": 2}, "comments"),
        # CHANGES_REQUESTED *with* an unresolved thread stays "comments".
        ({"review_decision": "CHANGES_REQUESTED", "unaddressed": 2}, "comments"),
        ({"mergeable": "CONFLICTING"}, "conflicts"),
        # Non-actionable display_issue values → no nudge.
        ({"review_decision": "APPROVED"}, ""),  # approved
        ({}, ""),  # clean
        # changes-requested with nothing unaddressed → display_issue
        # "changes-requested", which is NOT in ACTIONABLE_ISSUES.
        ({"review_decision": "CHANGES_REQUESTED", "unaddressed": 0}, ""),
    ],
)
def test_nudge_issue_open_pr(overrides, expected):
    assert _issue_pr(**overrides).nudge_issue == expected


@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_nudge_issue_non_open_is_empty(state):
    """A merged/closed PR is never actionable even with a failing issue — the
    OPEN gate stops the forever-nudge loop."""
    assert _issue_pr(ci="failed:lint", state=state).nudge_issue == ""


@pytest.mark.parametrize("overrides", [{"ci": "failed:lint"}, {"unaddressed": 2}])
def test_nudge_issue_coworker_pr_is_never_actionable(overrides):
    """A coworker's PR is reviewed, not authored — nudging it would tell the
    review session to fix someone else's CI or rewrite their branch."""
    pr = _issue_pr(author="alice", mine=False, **overrides)
    assert pr.display_issue in {"ci", "comments"}  # the issue is still surfaced
    assert pr.nudge_issue == ""


def test_pr_from_node_sets_mine_from_self_user():
    from cockpit.lib.gh import _pr_from_node

    node = _full_pr_node(author={"login": "alice"})

    def _mine(*args: str) -> bool:
        pr = _pr_from_node(node, *args)
        assert pr is not None
        return pr.mine

    assert _mine("khivi") is False
    assert _mine("alice") is True
    # No self_user given → can't prove it's a coworker's, so author-mode default.
    assert _mine() is True


def test_nudge_issue_matches_display_issue_when_actionable():
    """When actionable, nudge_issue is exactly display_issue (so the 🔔 and the
    nudge message can't diverge)."""
    pr = _issue_pr(ci="failed:lint")
    assert pr.nudge_issue == pr.display_issue == "ci"


# ── fetch_pr_state_for_branch: any-state live lookup for the close path ──────


def _completed(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout)


def test_fetch_pr_state_for_branch_merged():
    rows = '[{"state": "MERGED", "number": 7}]'
    with patch("cockpit.lib.gh.subprocess.run", return_value=_completed(rows)) as run:
        out = fetch_pr_state_for_branch("khivi/x", Path("/tmp/wt"))
    assert out == {"state": "MERGED", "number": 7}
    # any-state query (not open-only) + scoped to the branch head
    args = run.call_args[0][0]
    assert args[:3] == ["gh", "pr", "list"]
    assert "--state" in args and args[args.index("--state") + 1] == "all"
    assert "--head" in args and args[args.index("--head") + 1] == "khivi/x"


def test_fetch_pr_state_for_branch_no_pr_returns_none():
    with patch("cockpit.lib.gh.subprocess.run", return_value=_completed("[]")):
        assert fetch_pr_state_for_branch("khivi/x", Path("/tmp/wt")) is None


def test_fetch_pr_state_for_branch_gh_failure_returns_none():
    with patch(
        "cockpit.lib.gh.subprocess.run", return_value=_completed("", returncode=1)
    ):
        assert fetch_pr_state_for_branch("khivi/x", Path("/tmp/wt")) is None


def test_fetch_pr_state_for_branch_unparseable_returns_none():
    with patch("cockpit.lib.gh.subprocess.run", return_value=_completed("not json")):
        assert fetch_pr_state_for_branch("khivi/x", Path("/tmp/wt")) is None


# ---------------------------------------------------------------------------
# Trunk-headed PRs (head == main/master, e.g. "merge main into <feature>").
# The head ref can't be the worktree branch — it clobbers the local trunk and
# collapses onto the primary checkout. `pr_worktree_branch` synthesizes a
# `pr-<N>-<base-slug>` branch instead; the daemon rejoins it to GitHub by the
# embedded number rather than by headRefName.
# ---------------------------------------------------------------------------


def test_pr_worktree_branch_feature_head_unchanged():
    # The 99% case: a dedicated feature head is used verbatim.
    assert pr_worktree_branch(12, "khivi/fix-login", "main") == "khivi/fix-login"


def test_pr_worktree_branch_trunk_head_synthesized():
    assert (
        pr_worktree_branch(280, "main", "feature/new_onboarding")
        == "pr-280-new-onboarding"
    )
    assert pr_worktree_branch(9, "master", "develop") == "pr-9-develop"


def test_pr_worktree_branch_trunk_head_no_base():
    # Off-GitHub / missing base still yields a trunk-safe, number-bearing branch.
    assert pr_worktree_branch(5, "main", "") == "pr-5"


def test_synth_branch_label_reads_as_base_feature():
    # branch_label strips the `pr-<N>-` token so the workspace name is the
    # feature the PR merges INTO, not the trunk it came from.
    b = pr_worktree_branch(280, "main", "feature/new_onboarding")
    assert branch_label(b) == "new-onboarding"


@pytest.mark.covers("gh.trunk-head.no-raw-headrefname~1")
def test_relevant_pr_query_routes_synth_by_number():
    q, variables = _relevant_pr_query(
        "o",
        "n",
        "me",
        ["pr-280-new-onboarding", "khivi/feat-x"],
        "number updatedAt",
    )
    # synth branch → single-node pullRequest(number:), NOT a headRefName alias,
    # and NOT declared as a String variable (the number is interpolated).
    assert "pullRequest(number: 280)" in q
    assert "b0" not in variables
    # normal branch keeps the connection alias + its String variable.
    assert "headRefName: $b1" in q
    assert variables["b1"] == "khivi/feat-x"


def test_collect_nodes_mixes_single_and_connection():
    data = {
        "data": {
            "mine": {"nodes": []},
            "repo": {
                "b0": {"number": 280, "updatedAt": "t"},  # pullRequest(number:)
                "b1": {"nodes": [{"number": 12, "updatedAt": "u"}]},  # connection
                "b2": None,  # pullRequest(number:) miss — skipped, no crash
            },
        }
    }
    assert [n["number"] for n in _collect_nodes(data, 3)] == [280, 12]


def test_list_open_pr_heads_synthesizes_trunk_head():
    page = {
        "data": {
            "search": {
                "pageInfo": {"endCursor": None, "hasNextPage": False},
                "nodes": [
                    {
                        "number": 280,
                        "headRefName": "main",
                        "baseRefName": "feature/new_onboarding",
                        "author": {"login": "coworker"},
                        "authorAssociation": "COLLABORATOR",
                    },
                    {
                        "number": 12,
                        "headRefName": "khivi/feat-x",
                        "baseRefName": "main",
                        "author": {"login": "khivi"},
                        "authorAssociation": "OWNER",
                    },
                ],
            }
        }
    }
    with patch("cockpit.lib.gh._graphql", return_value=page):
        heads = list_open_pr_heads("o", "n")
    by_num = {h.number: h for h in heads}
    assert by_num[280].branch == "pr-280-new-onboarding"
    assert by_num[12].branch == "khivi/feat-x"  # feature head untouched


def test_resolve_pr_branch_synthesizes_trunk_head():
    def fake_run(args, **kwargs):
        if "nameWithOwner" in args:
            return _completed("o/n")
        return _completed(
            json.dumps(
                {
                    "number": 280,
                    "headRefName": "main",
                    "baseRefName": "feature/new_onboarding",
                }
            )
        )

    with patch("cockpit.lib.gh.subprocess.run", side_effect=fake_run):
        assert resolve_pr_branch("280") == "pr-280-new-onboarding"


# ── update_pull_request_branch: server-side "Update branch", never runs gh ──
# without both halves of the compare-and-swap, and treats a 200-OK GraphQL
# `errors` payload as the real refusal signal (see gh.py::update_pull_request_branch).


def _completed_std(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_update_branch_missing_node_id_short_circuits():
    with patch("cockpit.lib.gh.subprocess.run") as run:
        ok, detail = update_pull_request_branch("", "sha-a")
    assert (ok, detail) == (False, "missing node id or head oid")
    run.assert_not_called()


def test_update_branch_missing_expected_head_oid_short_circuits():
    with patch("cockpit.lib.gh.subprocess.run") as run:
        ok, detail = update_pull_request_branch("PR_node", "")
    assert (ok, detail) == (False, "missing node id or head oid")
    run.assert_not_called()


def test_update_branch_graphql_errors_payload_is_the_refusal():
    """`gh api graphql` exits 0 even when the mutation itself was refused (a
    stale `expectedHeadOid`), so the `errors` array — not the returncode — is
    the real verdict. Regressing this reports success on a refused update,
    and the caller's head-oid-keyed marker sticks forever.
    """
    payload = json.dumps({"errors": [{"message": "Head ref oid has changed"}]})
    with patch(
        "cockpit.lib.gh.subprocess.run",
        return_value=_completed_std(stdout=payload, returncode=0),
    ):
        ok, detail = update_pull_request_branch("PR_node", "sha-a")
    assert (ok, detail) == (False, "Head ref oid has changed")


def test_update_branch_graphql_errors_payload_falls_back_when_no_message():
    payload = json.dumps({"errors": [{}]})
    with patch(
        "cockpit.lib.gh.subprocess.run",
        return_value=_completed_std(stdout=payload, returncode=0),
    ):
        ok, detail = update_pull_request_branch("PR_node", "sha-a")
    assert (ok, detail) == (False, "graphql error")


def test_update_branch_nonzero_returncode_uses_last_stderr_line():
    with patch(
        "cockpit.lib.gh.subprocess.run",
        return_value=_completed_std(
            stderr="warning: ignore me\nerror: bad credentials", returncode=1
        ),
    ):
        ok, detail = update_pull_request_branch("PR_node", "sha-a")
    assert (ok, detail) == (False, "error: bad credentials")


def test_update_branch_nonzero_returncode_falls_back_to_stdout():
    with patch(
        "cockpit.lib.gh.subprocess.run",
        return_value=_completed_std(stdout="stdout tail line", returncode=1),
    ):
        ok, detail = update_pull_request_branch("PR_node", "sha-a")
    assert (ok, detail) == (False, "stdout tail line")


def test_update_branch_nonzero_returncode_empty_output_reports_gh_failed():
    with patch(
        "cockpit.lib.gh.subprocess.run",
        return_value=_completed_std(returncode=1),
    ):
        ok, detail = update_pull_request_branch("PR_node", "sha-a")
    assert (ok, detail) == (False, "gh failed")


def test_update_branch_unparsable_json_response():
    with patch(
        "cockpit.lib.gh.subprocess.run",
        return_value=_completed_std(stdout="not json", returncode=0),
    ):
        ok, detail = update_pull_request_branch("PR_node", "sha-a")
    assert (ok, detail) == (False, "unparsable response")


def test_update_branch_success_returns_new_head_oid():
    payload = json.dumps(
        {"data": {"updatePullRequestBranch": {"pullRequest": {"headRefOid": "sha-b"}}}}
    )
    with patch(
        "cockpit.lib.gh.subprocess.run",
        return_value=_completed_std(stdout=payload, returncode=0),
    ):
        ok, detail = update_pull_request_branch("PR_node", "sha-a")
    assert (ok, detail) == (True, "sha-b")


def test_update_branch_success_with_missing_nested_fields_yields_empty_oid():
    # The defensive `or {}` chain at each level of the response means a
    # success payload missing the nested fields still reports success, just
    # with no new oid to show for it.
    with patch(
        "cockpit.lib.gh.subprocess.run",
        return_value=_completed_std(stdout=json.dumps({"data": {}}), returncode=0),
    ):
        ok, detail = update_pull_request_branch("PR_node", "sha-a")
    assert (ok, detail) == (True, "")


def test_update_branch_subprocess_missing_binary_never_raises():
    with patch(
        "cockpit.lib.gh.subprocess.run",
        side_effect=FileNotFoundError("no gh on PATH"),
    ):
        ok, detail = update_pull_request_branch("PR_node", "sha-a")
    assert ok is False
    assert detail == "no gh on PATH"


def test_update_branch_subprocess_os_error_never_raises():
    with patch(
        "cockpit.lib.gh.subprocess.run",
        side_effect=OSError("permission denied"),
    ):
        ok, detail = update_pull_request_branch("PR_node", "sha-a")
    assert ok is False
    assert detail == "permission denied"


def test_update_branch_passes_method_through_verbatim():
    """The caller (the daemon) owns REBASE-vs-MERGE policy; this function must
    not second-guess it.
    """
    payload = json.dumps(
        {"data": {"updatePullRequestBranch": {"pullRequest": {"headRefOid": "sha-b"}}}}
    )
    with patch(
        "cockpit.lib.gh.subprocess.run",
        return_value=_completed_std(stdout=payload, returncode=0),
    ) as run:
        update_pull_request_branch("PR_node", "sha-a", method="MERGE")
    args = run.call_args[0][0]
    assert "method=MERGE" in args
    assert "method=REBASE" not in args


# ── _fetch_light_phase — cheap phase, {number: updatedAt} only ─────────────


def test_fetch_light_phase_queries_with_light_fields_not_heavy():
    """Must build its query via `_relevant_pr_query` with `_PR_LIGHT_FIELDS` —
    the whole point of the light phase is staying cheap."""
    expected_query, expected_vars = _relevant_pr_query(
        "o", "n", "u", ["khivi/feat"], _PR_LIGHT_FIELDS
    )
    with patch(
        "cockpit.lib.gh._graphql",
        return_value={"data": {"mine": {"nodes": []}}},
    ) as m:
        _fetch_light_phase("o", "n", "u", ["khivi/feat"])
    assert m.call_args.args[0] == expected_query
    assert m.call_args.args[1] == expected_vars


def test_fetch_light_phase_maps_number_to_updated_at():
    data = {"data": {"mine": {"nodes": [{"number": 1, "updatedAt": "2025-01-01"}]}}}
    with patch("cockpit.lib.gh._graphql", return_value=data):
        result = _fetch_light_phase("o", "n", "u", [])
    assert result == {1: "2025-01-01"}


def test_fetch_light_phase_drops_nodes_with_no_number():
    data = {
        "data": {
            "mine": {
                "nodes": [
                    {"number": None, "updatedAt": "2025-01-01"},
                    {"number": 2, "updatedAt": "2025-02-02"},
                ]
            }
        }
    }
    with patch("cockpit.lib.gh._graphql", return_value=data):
        result = _fetch_light_phase("o", "n", "u", [])
    assert result == {2: "2025-02-02"}


def test_fetch_light_phase_missing_updated_at_becomes_empty_string():
    """A node lacking `updatedAt` must map to "" — not None, which would make
    `_identify_stale`'s `prev[1] != updated` comparison behave differently on
    a re-fetch than on the initial cache miss."""
    data = {"data": {"mine": {"nodes": [{"number": 3}]}}}
    with patch("cockpit.lib.gh._graphql", return_value=data):
        result = _fetch_light_phase("o", "n", "u", [])
    assert result == {3: ""}


def test_fetch_light_phase_duplicate_number_first_node_wins():
    """`setdefault` means the first node for a given number sticks — a later
    duplicate (e.g. the same PR surfacing again via a per-branch alias) must
    not overwrite it."""
    data = {
        "data": {
            "mine": {
                "nodes": [
                    {"number": 4, "updatedAt": "first"},
                    {"number": 4, "updatedAt": "second"},
                ]
            }
        }
    }
    with patch("cockpit.lib.gh._graphql", return_value=data):
        result = _fetch_light_phase("o", "n", "u", [])
    assert result == {4: "first"}


# ── _hydrate_stale — one aliased heavy query over the stale numbers ────────


def test_hydrate_stale_builds_one_aliased_query_per_stale_number():
    with patch(
        "cockpit.lib.gh._graphql",
        return_value={"data": {"repository": {}}},
    ) as m:
        _hydrate_stale("o", "n", "u", [10, 20], {}, {})
    query = m.call_args.args[0]
    assert "pr0: pullRequest(number: 10)" in query
    assert "pr1: pullRequest(number: 20)" in query
    assert m.call_args.args[1] == {"owner": "o", "name": "n"}


def test_hydrate_stale_writes_pr_and_light_updated_at_into_cache():
    node10 = _full_pr_node(number=10)
    node20 = _full_pr_node(number=20, headRefName="khivi/other")
    data = {"data": {"repository": {"pr0": node10, "pr1": node20}}}
    light_by_number = {10: "2025-01-01", 20: "2025-02-02"}
    cache: dict = {}
    with patch("cockpit.lib.gh._graphql", return_value=data):
        _hydrate_stale("o", "n", "u", [10, 20], light_by_number, cache)
    assert cache[10][0].number == 10
    assert cache[10][1] == "2025-01-01"
    assert cache[20][0].number == 20
    assert cache[20][1] == "2025-02-02"


def test_hydrate_stale_missing_light_entry_defaults_to_empty_string():
    node = _full_pr_node(number=9)
    data = {"data": {"repository": {"pr0": node}}}
    cache: dict = {}
    with patch("cockpit.lib.gh._graphql", return_value=data):
        _hydrate_stale("o", "n", "u", [9], {}, cache)
    assert cache[9][1] == ""


def test_hydrate_stale_alias_index_lines_up_with_stale_list_order():
    """An off-by-one between the alias built for `stale[i]` and the node read
    back for that same `i` would silently file one PR's data under another's
    number — pin that pr0's node lands under stale[0] and pr1's under
    stale[1], not swapped."""
    node_a = _full_pr_node(number=100, title="PR A", headRefName="khivi/a")
    node_b = _full_pr_node(number=200, title="PR B", headRefName="khivi/b")
    data = {"data": {"repository": {"pr0": node_a, "pr1": node_b}}}
    cache: dict = {}
    with patch("cockpit.lib.gh._graphql", return_value=data):
        _hydrate_stale("o", "n", "u", [100, 200], {}, cache)
    assert cache[100][0].title == "PR A"
    assert cache[200][0].title == "PR B"


def test_hydrate_stale_missing_alias_leaves_cache_entry_alone():
    """A stale number whose alias comes back absent from the response (e.g.
    the PR was deleted mid-cycle) must not clobber whatever the cache already
    held for it."""
    from cockpit.lib.gh import _pr_from_node

    prior_pr = _pr_from_node(_full_pr_node(number=5))
    cache: dict = {5: (prior_pr, "stale-timestamp")}
    data: dict = {"data": {"repository": {}}}  # pr0 key entirely absent
    with patch("cockpit.lib.gh._graphql", return_value=data):
        _hydrate_stale("o", "n", "u", [5], {}, cache)
    assert cache[5] == (prior_pr, "stale-timestamp")


def test_hydrate_stale_null_alias_leaves_cache_entry_alone():
    prior = ("sentinel-pr", "sentinel-ts")
    cache: dict = {5: prior}
    data = {"data": {"repository": {"pr0": None}}}
    with patch("cockpit.lib.gh._graphql", return_value=data):
        _hydrate_stale("o", "n", "u", [5], {}, cache)
    assert cache[5] == prior


def test_hydrate_stale_node_that_fails_pr_from_node_leaves_cache_entry_alone():
    """A node missing `author` makes `_pr_from_node` return None (e.g. a
    since-deleted account) — the stale cache entry must survive rather than
    being overwritten with nothing."""
    prior = ("sentinel-pr", "sentinel-ts")
    cache: dict = {5: prior}
    bad_node = _full_pr_node(number=5, author=None)
    data = {"data": {"repository": {"pr0": bad_node}}}
    with patch("cockpit.lib.gh._graphql", return_value=data):
        _hydrate_stale("o", "n", "u", [5], {}, cache)
    assert cache[5] == prior


# ── repo_nwo ─────────────────────────────────────────────────────────────


def test_repo_nwo_returns_owner_and_name_on_success():
    payload = json.dumps({"owner": {"login": "khivi"}, "name": "cockpit"})
    with patch(
        "cockpit.lib.gh.subprocess.run",
        return_value=_completed_std(stdout=payload, returncode=0),
    ):
        assert repo_nwo(Path("/some/repo")) == ("khivi", "cockpit")


def test_repo_nwo_raises_runtime_error_naming_repo_dir_on_failure():
    with (
        patch(
            "cockpit.lib.gh.subprocess.run",
            return_value=_completed_std(stderr="not a repo", returncode=1),
        ),
        pytest.raises(RuntimeError, match=r"/some/repo"),
    ):
        repo_nwo(Path("/some/repo"))
