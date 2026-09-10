"""Tests for cockpit/lib/linear.py — regex, extract_ticket, footer parsing,
and the direct GraphQL network surface (`fetch_ticket_states` and friends).

The Linear ticket *body* (title, description) is still fetched by Claude via the
Linear MCP from the spawned workspace; see `test_spawn.py` for the spawn-side
dispatch. The daemon's direct Linear calls are exercised below with a mocked
`urlopen`.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from cockpit.lib.linear import (
    LINEAR_ISSUE_URL_RE,
    LINEAR_RE,
    LINEAR_RE_CI,
    extract_ticket,
    fetch_my_open,
    fetch_team_states,
    fetch_ticket_meta,
    fetch_ticket_project,
    fetch_ticket_states,
    fetch_ticket_titles,
    fetch_viewer_id,
    parse_linear_footer_links,
    parse_linear_footers,
    update_ticket_state,
)


def test_linear_re_matches_uppercase_only():
    assert LINEAR_RE.search("khivi/PE-1234-foo")
    assert not LINEAR_RE.search("khivi/pe-1234-foo")


def test_linear_re_ci_matches_either_case():
    assert LINEAR_RE_CI.fullmatch("PE-1234")
    assert LINEAR_RE_CI.fullmatch("pe-1234")
    assert LINEAR_RE_CI.fullmatch("EnG-99")


def test_linear_re_ci_rejects_out_of_bound_prefix():
    assert not LINEAR_RE_CI.fullmatch("TOOLONG-1")  # 7-char prefix
    assert not LINEAR_RE_CI.fullmatch("A-1")  # 1-char prefix


def test_linear_issue_url_re_captures_the_id():
    m = LINEAR_ISSUE_URL_RE.match("https://linear.app/acme/issue/TOOLS-1300/some-slug")
    assert m and m.group(1) == "TOOLS-1300"


def test_linear_issue_url_re_ignores_non_issue_paths():
    assert not LINEAR_ISSUE_URL_RE.match("https://linear.app/acme/team/PE/all")
    assert not LINEAR_ISSUE_URL_RE.match("https://linear.app/acme/issue/")


def test_extract_ticket_returns_first_match():
    assert extract_ticket("khivi/PE-1234-add-foo") == "PE-1234"


def test_extract_ticket_handles_lowercase_prefix():
    # Linear generates branch names with lowercase prefixes (e.g. pe-1234)
    assert extract_ticket("khivi/pe-1234-add-foo") == "PE-1234"


def test_extract_ticket_double_ticket_returns_first():
    # Branch names like pe-4547-pe-4176-foo contain two ticket ids; return the first
    assert extract_ticket("khivi/pe-4547-pe-4176-async-lifecycle-follow") == "PE-4547"


def test_extract_ticket_empty_returns_empty():
    assert extract_ticket("") == ""
    assert extract_ticket("khivi/no-ticket") == ""


# ────────────────────────────────────────────────────────────────────────────
# parse_linear_footers — strict delivery signal (PR-body footer only)
# ────────────────────────────────────────────────────────────────────────────


def test_parse_footers_single():
    body = "Some description.\n\n---\nLinear: [PE-1234](https://linear.app/x/PE-1234)"
    assert parse_linear_footers(body) == ["PE-1234"]


def test_parse_footers_multiple_preserves_order_and_dedups():
    body = "Linear: [PE-100](u)\nLinear: [ENG-5](u)\nLinear: [PE-100](u)\n"  # dup
    assert parse_linear_footers(body) == ["PE-100", "ENG-5"]


def test_parse_footers_case_insensitive_normalizes_to_upper():
    # The `Linear:` label and the id can be any case (branch slugs lowercase the
    # id); the footer still counts as delivery and the id is canonicalised upper.
    body = "lINeaR: [pe-4698](https://linear.app/x/PE-4698)"
    assert parse_linear_footers(body) == ["PE-4698"]


def test_parse_footers_dedups_across_case():
    body = "Linear: [PE-100](u)\n" "linear: [pe-100](u)\n"
    assert parse_linear_footers(body) == ["PE-100"]


def test_parse_footers_ignores_inline_mentions():
    # Only a line-anchored `Linear:` footer counts — a prose mention of a
    # predecessor / follow-up ticket is NOT a delivery signal.
    body = "Reapplies PE-9999 and supersedes PE-8888. See Linear ticket PE-7777."
    assert parse_linear_footers(body) == []


def test_parse_footers_empty_and_none():
    assert parse_linear_footers("") == []
    assert parse_linear_footers(None) == []  # type: ignore[arg-type]


def test_parse_footer_links_captures_url():
    body = "desc\n\nLinear: [PE-1234](https://linear.app/acme/issue/PE-1234)"
    assert parse_linear_footer_links(body) == [
        ("PE-1234", "https://linear.app/acme/issue/PE-1234")
    ]


def test_parse_footer_links_multiple_dedups_by_id():
    body = (
        "Linear: [PE-1](https://l/PE-1)\n"
        "Linear: [ENG-9](https://l/ENG-9)\n"
        "Linear: [PE-1](https://l/PE-1-again)\n"
    )
    assert parse_linear_footer_links(body) == [
        ("PE-1", "https://l/PE-1"),
        ("ENG-9", "https://l/ENG-9"),
    ]


def test_parse_footer_links_case_insensitive_normalizes_id():
    body = "linear: [pe-4698](https://l/pe-4698)"
    assert parse_linear_footer_links(body) == [("PE-4698", "https://l/pe-4698")]


def test_parse_footer_links_empty():
    assert parse_linear_footer_links("") == []
    assert parse_linear_footer_links("Linear: PE-1 no link") == []


# ────────────────────────────────────────────────────────────────────────────
# fetch_ticket_states — Linear GraphQL, batched form (one query per team,
# mocked urlopen)
# ────────────────────────────────────────────────────────────────────────────


class _FakeResp:
    """Minimal context-manager stand-in for urlopen's return."""

    def __init__(self, payload: dict | None = None, *, raw: bytes | None = None):
        self._body = raw if raw is not None else json.dumps(payload or {}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _batch_resp(nodes: list[dict]) -> _FakeResp:
    return _FakeResp({"data": {"issues": {"nodes": nodes}}})


def test_fetch_ticket_states_empty_input_no_network():
    with patch("cockpit.lib.linear.urllib.request.urlopen") as urlopen:
        assert fetch_ticket_states([], api_key="k") == {}
    urlopen.assert_not_called()


def test_fetch_ticket_titles_batched_and_none_on_missing():
    def fake_urlopen(req, timeout=None):
        return _batch_resp(
            [{"identifier": "PE-1", "title": "Fix the login flow"}]
        )  # PE-2 absent → stays None

    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=fake_urlopen):
        out = fetch_ticket_titles(["PE-1", "PE-2"], api_key="k")
    assert out == {"PE-1": "Fix the login flow", "PE-2": None}


def test_fetch_ticket_titles_no_key_all_none_no_network():
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("cockpit.lib.linear.urllib.request.urlopen") as urlopen,
    ):
        assert fetch_ticket_titles(["PE-1"]) == {"PE-1": None}
    urlopen.assert_not_called()


def test_fetch_ticket_states_no_key_all_none_no_network():
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("cockpit.lib.linear.urllib.request.urlopen") as urlopen,
    ):
        assert fetch_ticket_states(["PE-1", "ENG-2"]) == {"PE-1": None, "ENG-2": None}
    urlopen.assert_not_called()


def test_fetch_ticket_states_single_team_one_query():
    captured: list[dict] = []

    def fake_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode()))
        return _batch_resp(
            [
                {"identifier": "PE-1", "state": {"name": "Dev Done"}},
                {"identifier": "PE-2", "state": {"name": "In Progress"}},
            ]
        )

    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=fake_urlopen):
        out = fetch_ticket_states(["PE-1", "PE-2"], api_key="k")

    assert out == {"PE-1": "Dev Done", "PE-2": "In Progress"}
    assert len(captured) == 1  # one team → one round-trip
    assert captured[0]["variables"] == {"team": "PE", "numbers": [1.0, 2.0]}


def test_fetch_ticket_states_groups_by_team():
    seen_teams: list[str] = []

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode())
        team = body["variables"]["team"]
        seen_teams.append(team)
        node = {"PE": "PE-1", "ENG": "ENG-9"}[team]
        return _batch_resp([{"identifier": node, "state": {"name": "Dev Done"}}])

    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=fake_urlopen):
        out = fetch_ticket_states(["PE-1", "ENG-9"], api_key="k")

    assert out == {"PE-1": "Dev Done", "ENG-9": "Dev Done"}
    assert sorted(seen_teams) == ["ENG", "PE"]  # one query per team


def test_fetch_ticket_states_team_failure_isolated():
    """One team's query failing leaves only that team's ids None; other teams
    keep their fetched states."""

    def fake_urlopen(req, timeout=None):
        team = json.loads(req.data.decode())["variables"]["team"]
        if team == "ENG":
            raise TimeoutError()
        return _batch_resp([{"identifier": "PE-1", "state": {"name": "Dev Done"}}])

    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=fake_urlopen):
        out = fetch_ticket_states(["PE-1", "ENG-9"], api_key="k")

    assert out == {"PE-1": "Dev Done", "ENG-9": None}


def test_fetch_ticket_states_missing_issue_stays_none():
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_batch_resp([{"identifier": "PE-1", "state": {"name": "Done"}}]),
    ):
        out = fetch_ticket_states(["PE-1", "PE-2"], api_key="k")
    assert out == {"PE-1": "Done", "PE-2": None}


def test_fetch_ticket_states_unparsable_id_stays_none_not_queried():
    captured: list[dict] = []

    def fake_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode()))
        return _batch_resp([{"identifier": "PE-1", "state": {"name": "Done"}}])

    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=fake_urlopen):
        out = fetch_ticket_states(["PE-1", "not-a-ticket"], api_key="k")

    assert out == {"PE-1": "Done", "not-a-ticket": None}
    assert captured[0]["variables"]["numbers"] == [1.0]  # bad id never grouped


def test_fetch_ticket_states_matches_case_insensitively():
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_batch_resp([{"identifier": "ENG-42", "state": {"name": "Done"}}]),
    ):
        out = fetch_ticket_states(["eng-42"], api_key="k")
    assert out == {"eng-42": "Done"}


# ────────────────────────────────────────────────────────────────────────────
# merge-transition helpers — viewer / meta / team-states / mutation
# ────────────────────────────────────────────────────────────────────────────


def test_fetch_viewer_id_no_key_skips_network():
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("cockpit.lib.linear.urllib.request.urlopen") as urlopen,
    ):
        assert fetch_viewer_id() is None
    urlopen.assert_not_called()


def test_fetch_viewer_id_happy_path():
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_FakeResp({"data": {"viewer": {"id": "u-123"}}}),
    ):
        assert fetch_viewer_id(api_key="k") == "u-123"


def test_fetch_viewer_id_error_is_none():
    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=TimeoutError()):
        assert fetch_viewer_id(api_key="k") is None


def test_fetch_viewer_id_malformed_json_is_none():
    # A 200 with an unparsable body — exercises `_post_graphql`'s own
    # json.loads (shared by all the merge-transition helpers below).
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_FakeResp(raw=b"not json {"),
    ):
        assert fetch_viewer_id(api_key="k") is None


def _meta_payload(
    *, state="Dev Done", state_type="completed", assignee="u-1", team="t-1"
) -> dict:
    return {
        "data": {
            "issues": {
                "nodes": [
                    {
                        "id": "issue-uuid",
                        "identifier": "PE-1",
                        "state": {"name": state, "type": state_type},
                        "assignee": {"id": assignee} if assignee else None,
                        "team": {"id": team},
                    }
                ]
            }
        }
    }


def test_fetch_ticket_meta_happy_path():
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_FakeResp(_meta_payload()),
    ):
        meta = fetch_ticket_meta("PE-1234", api_key="k")
    assert meta == {
        "id": "issue-uuid",
        "state": "Dev Done",
        "type": "completed",
        "assignee_id": "u-1",
        "team_id": "t-1",
    }


def test_fetch_ticket_meta_unassigned_is_none_assignee():
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_FakeResp(_meta_payload(assignee=None)),
    ):
        meta = fetch_ticket_meta("PE-1", api_key="k")
    assert meta is not None
    assert meta["assignee_id"] is None


def test_fetch_ticket_meta_no_key_skips_network():
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("cockpit.lib.linear.urllib.request.urlopen") as urlopen,
    ):
        assert fetch_ticket_meta("PE-1") is None
    urlopen.assert_not_called()


def test_fetch_ticket_meta_rejects_non_ticket():
    with patch("cockpit.lib.linear.urllib.request.urlopen") as urlopen:
        assert fetch_ticket_meta("nope", api_key="k") is None
    urlopen.assert_not_called()


def test_fetch_ticket_meta_no_match_is_none():
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_FakeResp({"data": {"issues": {"nodes": []}}}),
    ):
        assert fetch_ticket_meta("PE-9", api_key="k") is None


def test_fetch_team_states_builds_casefolded_map():
    payload = {
        "data": {
            "team": {
                "states": {
                    "nodes": [
                        {"id": "s-done", "name": "Done"},
                        {"id": "s-prog", "name": "In Progress"},
                    ]
                }
            }
        }
    }
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen", return_value=_FakeResp(payload)
    ):
        states = fetch_team_states("t-1", api_key="k")
    assert states == {"done": "s-done", "in progress": "s-prog"}


def test_fetch_team_states_no_key_or_team_skips_network():
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("cockpit.lib.linear.urllib.request.urlopen") as urlopen,
    ):
        assert fetch_team_states("t-1") is None  # no key (env cleared)
        assert fetch_team_states("", api_key="k") is None  # no team
    urlopen.assert_not_called()


def test_fetch_team_states_error_is_none():
    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=TimeoutError()):
        assert fetch_team_states("t-1", api_key="k") is None


def test_update_ticket_state_success():
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return _FakeResp({"data": {"issueUpdate": {"success": True}}})

    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=fake_urlopen):
        ok = update_ticket_state("issue-uuid", "s-done", api_key="k")
    assert ok is True
    assert captured["body"]["variables"] == {"id": "issue-uuid", "stateId": "s-done"}


def test_update_ticket_state_unsuccessful_is_false():
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_FakeResp({"data": {"issueUpdate": {"success": False}}}),
    ):
        assert update_ticket_state("i", "s", api_key="k") is False


def test_update_ticket_state_no_key_or_args_skips_network():
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("cockpit.lib.linear.urllib.request.urlopen") as urlopen,
    ):
        assert update_ticket_state("i", "s") is False  # no key (env cleared)
        assert update_ticket_state("", "s", api_key="k") is False  # no issue
        assert update_ticket_state("i", "", api_key="k") is False  # no state
    urlopen.assert_not_called()


def test_update_ticket_state_error_is_false():
    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=TimeoutError()):
        assert update_ticket_state("i", "s", api_key="k") is False


# ── fetch_ticket_project (the ticket→repo routing tiebreaker) ───────────────


def test_fetch_ticket_project_happy_path():
    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode())
        assert body["variables"] == {"team": "PE", "number": 1234.0}
        return _batch_resp([{"project": {"name": "Payments API"}}])

    with patch("cockpit.lib.linear.urllib.request.urlopen", fake_urlopen):
        assert fetch_ticket_project("PE-1234", api_key="k") == "Payments API"


def test_fetch_ticket_project_lowercase_id_uppercases_team():
    def fake_urlopen(req, timeout=None):
        assert json.loads(req.data.decode())["variables"]["team"] == "PE"
        return _batch_resp([{"project": {"name": "Payments API"}}])

    with patch("cockpit.lib.linear.urllib.request.urlopen", fake_urlopen):
        assert fetch_ticket_project("pe-1234", api_key="k") == "Payments API"


def test_fetch_ticket_project_unprojected_issue_is_none():
    # `Issue.project` is nullable — an issue filed outside any project.
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_batch_resp([{"project": None}]),
    ):
        assert fetch_ticket_project("PE-1234", api_key="k") is None


def test_fetch_ticket_project_no_match_is_none():
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_batch_resp([]),
    ):
        assert fetch_ticket_project("PE-1234", api_key="k") is None


def test_fetch_ticket_project_error_is_none():
    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=TimeoutError()):
        assert fetch_ticket_project("PE-1234", api_key="k") is None


def test_fetch_ticket_project_no_key_or_bad_id_skips_network():
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("cockpit.lib.linear.urllib.request.urlopen") as urlopen,
    ):
        assert fetch_ticket_project("PE-1234") is None  # no key (env cleared)
        assert fetch_ticket_project("not-a-ticket", api_key="k") is None
        assert fetch_ticket_project("", api_key="k") is None
    urlopen.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# fetch_my_open — the ticket inbox's one query per Linear workspace
# ────────────────────────────────────────────────────────────────────────────


def _field(tickets: list[dict] | None, key: str = "id") -> list[str]:
    """`key` off each ticket, asserting the fetch was answered — `None` is the
    "couldn't ask" case and never what these cases exercise."""
    assert tickets is not None
    return [t[key] for t in tickets]


def _issue_node(identifier: str, **over) -> dict:
    node = {
        "identifier": identifier,
        "title": f"Work on {identifier}",
        "url": f"https://linear.app/acme/issue/{identifier}",
        "updatedAt": "2026-09-09T10:00:00.000Z",
        "state": {"name": "Todo"},
        "team": {"key": identifier.split("-")[0]},
    }
    node.update(over)
    return node


def test_fetch_my_open_normalizes_every_field():
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_batch_resp([_issue_node("PE-412")]),
    ):
        out = fetch_my_open(["PE"], api_key="k")

    assert out == [
        {
            "id": "PE-412",
            "team": "PE",
            "title": "Work on PE-412",
            "state": "Todo",
            "url": "https://linear.app/acme/issue/PE-412",
            "updated_at": "2026-09-09T10:00:00.000Z",
        }
    ]


def test_fetch_my_open_is_one_round_trip_for_many_teams():
    """One query per *workspace*, not per team — the whole point of the union."""
    captured: list[dict] = []

    def fake_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode()))
        return _batch_resp([_issue_node("PE-1"), _issue_node("ENG-9")])

    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=fake_urlopen):
        out = fetch_my_open(["pe", "eng"], api_key="k")

    assert len(captured) == 1
    assert captured[0]["variables"] == {"keys": ["PE", "ENG"]}  # upper-cased
    assert _field(out) == ["PE-1", "ENG-9"]


def test_fetch_my_open_without_keys_drops_the_team_filter():
    """An empty union can't be `key:{in:[]}` — Linear would match nothing, which
    is the opposite of "this credential declares no teams, show me all of mine"."""
    captured: list[dict] = []

    def fake_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode()))
        return _batch_resp([_issue_node("PE-1")])

    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=fake_urlopen):
        assert _field(fetch_my_open([], api_key="k")) == ["PE-1"]
        assert _field(fetch_my_open(None, api_key="k")) == ["PE-1"]

    for body in captured:
        assert body["variables"] == {}
        assert "team:" not in body["query"]
        assert "$keys" not in body["query"]


def test_fetch_my_open_filters_on_state_type_not_name():
    """State *names* are per-team and renameable; the six types are not."""
    captured: list[dict] = []

    def fake_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode()))
        return _batch_resp([])

    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=fake_urlopen):
        fetch_my_open(["PE"], api_key="k")

    query = captured[0]["query"]
    assert 'state:{type:{in:["unstarted","started"]}}' in query
    assert "assignee:{isMe:{eq:true}}" in query
    for dropped in ("backlog", "triage", "completed", "canceled"):
        assert dropped not in query


def test_fetch_my_open_skips_a_node_with_no_identifier():
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_batch_resp([_issue_node("PE-1"), {"title": "orphan"}]),
    ):
        out = fetch_my_open(["PE"], api_key="k")
    assert _field(out) == ["PE-1"]


def test_fetch_my_open_missing_fields_become_empty_strings():
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_batch_resp([{"identifier": "PE-1"}]),
    ):
        out = fetch_my_open(["PE"], api_key="k")
    assert out == [
        {
            "id": "PE-1",
            "team": "",
            "title": "",
            "state": "",
            "url": "",
            "updated_at": "",
        }
    ]


def test_fetch_my_open_failure_is_none_not_empty():
    """None is "couldn't ask"; [] is "asked, nothing assigned". A blip that read
    as the latter would blank the inbox."""
    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=TimeoutError()):
        assert fetch_my_open(["PE"], api_key="k") is None


def test_fetch_my_open_answered_with_nothing_is_empty_not_none():
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen", return_value=_batch_resp([])
    ):
        assert fetch_my_open(["PE"], api_key="k") == []


def test_fetch_my_open_no_key_is_empty_and_skips_network():
    """An unset key is the feature being off, not the API being unreachable."""
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("cockpit.lib.linear.urllib.request.urlopen") as urlopen,
    ):
        assert fetch_my_open(["PE"]) == []
    urlopen.assert_not_called()
