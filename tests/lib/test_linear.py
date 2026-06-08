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
    fetch_ticket_state,
    linear_mcp_available,
    parse_linear_footer_links,
    parse_linear_footers,
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
    body = (
        "Linear: [PE-100](u)\n" "Linear: [ENG-5](u)\n" "Linear: [PE-100](u)\n"  # dup
    )
    assert parse_linear_footers(body) == ["PE-100", "ENG-5"]


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
