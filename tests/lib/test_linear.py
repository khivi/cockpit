"""Tests for cockpit/lib/linear.py — regex, extract_ticket, footer parsing,
and the one network surface (`fetch_ticket_state`).

The Linear ticket *body* (title, description) is still fetched by Claude via the
Linear MCP from the spawned workspace; see `test_spawn.py` for the spawn-side
dispatch. The daemon's only direct Linear call is `fetch_ticket_state`, exercised
below with a mocked `urlopen`.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
from io import BytesIO
from unittest.mock import patch

from cockpit.lib.linear import (
    LINEAR_RE,
    LINEAR_RE_CI,
    extract_ticket,
    fetch_team_states,
    fetch_ticket_meta,
    fetch_ticket_state,
    fetch_ticket_states,
    fetch_viewer_id,
    linear_mcp_available,
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
# linear_mcp_available — pre-flight against `claude mcp list`
# ────────────────────────────────────────────────────────────────────────────


def _fake_completed(
    stdout: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude", "mcp", "list"], returncode=returncode, stdout=stdout, stderr=""
    )


def test_linear_mcp_available_returns_none_when_claude_missing():
    """No `claude` on PATH → FileNotFoundError → None (can't tell)."""
    with patch("cockpit.lib.linear.subprocess.run", side_effect=FileNotFoundError):
        assert linear_mcp_available() is None


def test_linear_mcp_available_returns_none_on_timeout():
    with patch(
        "cockpit.lib.linear.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=3),
    ):
        assert linear_mcp_available() is None


def test_linear_mcp_available_returns_none_on_nonzero_exit():
    """`claude mcp list` ran but failed → can't tell → None."""
    with patch(
        "cockpit.lib.linear.subprocess.run",
        return_value=_fake_completed(stdout="", returncode=1),
    ):
        assert linear_mcp_available() is None


def test_linear_mcp_available_true_when_output_contains_linear():
    with patch(
        "cockpit.lib.linear.subprocess.run",
        return_value=_fake_completed(
            stdout="linear: https://mcp.linear.app/sse (HTTP)\n",
        ),
    ):
        assert linear_mcp_available() is True


def test_linear_mcp_available_case_insensitive():
    with patch(
        "cockpit.lib.linear.subprocess.run",
        return_value=_fake_completed(stdout="LINEAR Connector enabled\n"),
    ):
        assert linear_mcp_available() is True


def test_linear_mcp_available_false_when_no_linear_entry():
    with patch(
        "cockpit.lib.linear.subprocess.run",
        return_value=_fake_completed(stdout="github: gh-stuff\nfilesystem: fs-thing\n"),
    ):
        assert linear_mcp_available() is False


def test_linear_mcp_available_false_on_empty_output():
    with patch(
        "cockpit.lib.linear.subprocess.run",
        return_value=_fake_completed(stdout=""),
    ):
        assert linear_mcp_available() is False


def test_linear_mcp_available_returns_none_on_oserror():
    with patch(
        "cockpit.lib.linear.subprocess.run",
        side_effect=OSError("permission denied"),
    ):
        assert linear_mcp_available() is None


def test_linear_mcp_available_uses_bumped_timeout():
    """The pre-flight budget must outlast a managed-connector handshake
    (~6s typical, 30s+ under load) so a slow-but-connecting Linear MCP yields
    a definitive answer instead of timing out at the old 3s budget."""
    with patch(
        "cockpit.lib.linear.subprocess.run",
        return_value=_fake_completed(stdout="linear: ...\n"),
    ) as run:
        linear_mcp_available()
    assert run.call_args.kwargs["timeout"] >= 15


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
# fetch_ticket_state — Linear GraphQL (mocked urlopen)
# ────────────────────────────────────────────────────────────────────────────


class _FakeResp:
    """Minimal context-manager stand-in for urlopen's return."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _state_payload(state_name: str) -> dict:
    return {
        "data": {
            "issues": {"nodes": [{"identifier": "PE-1", "state": {"name": state_name}}]}
        }
    }


def test_fetch_ticket_state_no_key_skips_network():
    """No LINEAR_API_KEY (and no override) → None, and urlopen is never called."""
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("cockpit.lib.linear.urllib.request.urlopen") as urlopen,
    ):
        assert fetch_ticket_state("PE-1") is None
    urlopen.assert_not_called()


def test_fetch_ticket_state_happy_path():
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_FakeResp(_state_payload("Dev Done")),
    ):
        assert fetch_ticket_state("PE-1234", api_key="lin_xxx") == "Dev Done"


def test_fetch_ticket_state_rejects_non_ticket():
    with patch("cockpit.lib.linear.urllib.request.urlopen") as urlopen:
        assert fetch_ticket_state("not-a-ticket", api_key="k") is None
    urlopen.assert_not_called()


def test_fetch_ticket_state_no_matching_issue_is_none():
    with patch(
        "cockpit.lib.linear.urllib.request.urlopen",
        return_value=_FakeResp({"data": {"issues": {"nodes": []}}}),
    ):
        assert fetch_ticket_state("PE-9", api_key="k") is None


def test_fetch_ticket_state_http_error_is_none():
    err = urllib.error.HTTPError("u", 401, "unauthorized", {}, BytesIO(b""))  # type: ignore[arg-type]
    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=err):
        assert fetch_ticket_state("PE-1", api_key="k") is None


def test_fetch_ticket_state_timeout_is_none():
    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=TimeoutError()):
        assert fetch_ticket_state("PE-1", api_key="k") is None


def test_fetch_ticket_state_sends_team_and_number_variables():
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        captured["auth"] = req.headers.get("Authorization")
        return _FakeResp(_state_payload("In Progress"))

    with patch("cockpit.lib.linear.urllib.request.urlopen", side_effect=fake_urlopen):
        fetch_ticket_state("eng-42", api_key="secret-key")

    assert captured["auth"] == "secret-key"  # raw key, no Bearer prefix
    assert captured["body"]["variables"] == {"team": "ENG", "number": 42.0}


# ────────────────────────────────────────────────────────────────────────────
# fetch_ticket_states — batched form (one query per team, mocked urlopen)
# ────────────────────────────────────────────────────────────────────────────


def _batch_resp(nodes: list[dict]) -> _FakeResp:
    return _FakeResp({"data": {"issues": {"nodes": nodes}}})


def test_fetch_ticket_states_empty_input_no_network():
    with patch("cockpit.lib.linear.urllib.request.urlopen") as urlopen:
        assert fetch_ticket_states([], api_key="k") == {}
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
