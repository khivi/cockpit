"""Tests for cockpit/lib/jira.py — footer parsing and the REST surfaces
(`fetch_issue_statuses`, `fetch_myself`, `fetch_issue_meta`, `transition_issue`).

The Jira issue *body* is fetched by Claude via the Atlassian MCP from the spawned
workspace; see `test_spawn.py` for the spawn-side dispatch. The daemon's direct
Jira calls are exercised below with a mocked `urlopen` (the Linear leaf test's
pattern) — degrade-never-raise on every failure path, and Basic-auth header
correctness.
"""

from __future__ import annotations

import base64
import json
import urllib.error
from io import BytesIO
from unittest.mock import patch

from cockpit.lib.jira import (
    fetch_issue_meta,
    fetch_issue_statuses,
    fetch_myself,
    parse_jira_footer_links,
    parse_jira_footers,
    transition_issue,
)

# ────────────────────────────────────────────────────────────────────────────
# footer parsing — the strict delivery signal
# ────────────────────────────────────────────────────────────────────────────


def test_parse_footers_uppercases_and_dedups():
    body = (
        "Some description.\n"
        "Jira: [proj-1](https://x/browse/PROJ-1)\n"
        "Jira: [ENG-9](https://x/browse/ENG-9)\n"
        "Jira: [R2D2-7](https://x/browse/R2D2-7)\n"  # digit after first letter
        "Jira: [PROJ-1](https://x/browse/PROJ-1-again)\n"
    )
    assert parse_jira_footers(body) == ["PROJ-1", "ENG-9", "R2D2-7"]


def test_parse_footers_requires_line_anchored_footer():
    # A bare mention in prose is NOT a delivery footer.
    assert parse_jira_footers("see PROJ-123 for context") == []
    assert parse_jira_footers("text before Jira: [PROJ-1](u)") == []


def test_parse_footers_empty():
    assert parse_jira_footers("") == []
    assert parse_jira_footers("no footer here") == []


def test_parse_footer_links_normalizes_id_keeps_url():
    body = "jira: [proj-4](https://acme.atlassian.net/browse/PROJ-4)"
    assert parse_jira_footer_links(body) == [
        ("PROJ-4", "https://acme.atlassian.net/browse/PROJ-4")
    ]


def test_parse_footer_links_empty_without_link():
    assert parse_jira_footer_links("Jira: PROJ-1 no link") == []
    assert parse_jira_footer_links("") == []


# ────────────────────────────────────────────────────────────────────────────
# REST surfaces — mocked urlopen
# ────────────────────────────────────────────────────────────────────────────

SITE = "https://acme.atlassian.net"
EMAIL = "me@acme.com"
TOKEN = "tok_xxx"


class _FakeResp:
    """Minimal context-manager stand-in for urlopen's return."""

    def __init__(self, payload: dict | None, *, raw: bytes | None = None):
        self._body = raw if raw is not None else json.dumps(payload or {}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _expected_basic() -> str:
    return "Basic " + base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()


def test_fetch_issue_statuses_happy_path_and_basic_auth():
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["auth"] = req.headers.get("Authorization")
        captured["url"] = req.full_url
        return _FakeResp({"fields": {"status": {"name": "Dev Done"}}})

    with patch("cockpit.lib.jira.urllib.request.urlopen", side_effect=fake_urlopen):
        out = fetch_issue_statuses(["PROJ-1"], site_url=SITE, email=EMAIL, token=TOKEN)
    assert out == {"PROJ-1": "Dev Done"}
    assert captured["auth"] == _expected_basic()  # base64(email:token), no Bearer
    assert captured["url"] == f"{SITE}/rest/api/3/issue/PROJ-1?fields=status"


def test_fetch_issue_statuses_trailing_slash_site_no_double_slash():
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp({"fields": {"status": {"name": "Done"}}})

    with patch("cockpit.lib.jira.urllib.request.urlopen", side_effect=fake_urlopen):
        fetch_issue_statuses(["X-1"], site_url=SITE + "/", email=EMAIL, token=TOKEN)
    assert "//rest" not in captured["url"]
    assert captured["url"].startswith(f"{SITE}/rest/api/3/issue/X-1")


def test_fetch_issue_statuses_no_creds_skips_network():
    # No token (env-less) → all None, no call.
    with (
        patch("cockpit.lib.jira.urllib.request.urlopen") as urlopen,
        patch.dict("os.environ", {}, clear=True),
    ):
        out = fetch_issue_statuses(["PROJ-1"], site_url=SITE, email=EMAIL)
    assert out == {"PROJ-1": None}
    urlopen.assert_not_called()


def test_fetch_issue_statuses_no_site_skips_network():
    with patch("cockpit.lib.jira.urllib.request.urlopen") as urlopen:
        out = fetch_issue_statuses(["PROJ-1"], site_url="", email=EMAIL, token=TOKEN)
    assert out == {"PROJ-1": None}
    urlopen.assert_not_called()


def test_fetch_issue_statuses_failure_isolated_per_key():
    def fake_urlopen(req, timeout=None):
        if "BAD-9" in req.full_url:
            raise TimeoutError()
        return _FakeResp({"fields": {"status": {"name": "Done"}}})

    with patch("cockpit.lib.jira.urllib.request.urlopen", side_effect=fake_urlopen):
        out = fetch_issue_statuses(
            ["OK-1", "BAD-9"], site_url=SITE, email=EMAIL, token=TOKEN
        )
    assert out == {"OK-1": "Done", "BAD-9": None}


def test_fetch_issue_statuses_malformed_json_is_none():
    # A 200 with an unparsable body must degrade like any other failure, not
    # raise json.JSONDecodeError out of `_request`.
    with patch(
        "cockpit.lib.jira.urllib.request.urlopen",
        return_value=_FakeResp(None, raw=b"not json {"),
    ):
        out = fetch_issue_statuses(["PROJ-1"], site_url=SITE, email=EMAIL, token=TOKEN)
    assert out == {"PROJ-1": None}


def test_fetch_issue_statuses_http_error_is_none():
    err = urllib.error.HTTPError("u", 401, "unauthorized", {}, BytesIO(b""))  # type: ignore[arg-type]
    with patch("cockpit.lib.jira.urllib.request.urlopen", side_effect=err):
        out = fetch_issue_statuses(["P-1"], site_url=SITE, email=EMAIL, token=TOKEN)
    assert out == {"P-1": None}


def test_fetch_myself_returns_account_id():
    with patch(
        "cockpit.lib.jira.urllib.request.urlopen",
        return_value=_FakeResp({"accountId": "acc-123"}),
    ):
        assert fetch_myself(site_url=SITE, email=EMAIL, token=TOKEN) == "acc-123"


def test_fetch_myself_no_creds_is_none():
    with (
        patch("cockpit.lib.jira.urllib.request.urlopen") as urlopen,
        patch.dict("os.environ", {}, clear=True),
    ):
        assert fetch_myself(site_url=SITE, email=EMAIL) is None
    urlopen.assert_not_called()


def test_fetch_issue_meta_status_and_assignee():
    payload = {
        "fields": {
            "status": {"name": "Dev Done"},
            "assignee": {"accountId": "acc-7"},
        }
    }
    with patch(
        "cockpit.lib.jira.urllib.request.urlopen", return_value=_FakeResp(payload)
    ):
        meta = fetch_issue_meta("PROJ-1", site_url=SITE, email=EMAIL, token=TOKEN)
    assert meta == {"status": "Dev Done", "assignee_id": "acc-7"}


def test_fetch_issue_meta_unassigned_is_none_assignee():
    payload = {"fields": {"status": {"name": "Todo"}, "assignee": None}}
    with patch(
        "cockpit.lib.jira.urllib.request.urlopen", return_value=_FakeResp(payload)
    ):
        meta = fetch_issue_meta("PROJ-1", site_url=SITE, email=EMAIL, token=TOKEN)
    assert meta == {"status": "Todo", "assignee_id": None}


def test_transition_issue_finds_matching_transition_and_posts():
    calls: list[tuple[str, str]] = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.method, req.full_url))
        if req.method == "GET":
            return _FakeResp(
                {
                    "transitions": [
                        {"id": "11", "to": {"name": "In Progress"}},
                        {"id": "31", "to": {"name": "Done"}},
                    ]
                }
            )
        # POST transition → 204 no content
        body = json.loads(req.data.decode())
        assert body == {"transition": {"id": "31"}}
        return _FakeResp(None, raw=b"")

    with patch("cockpit.lib.jira.urllib.request.urlopen", side_effect=fake_urlopen):
        ok = transition_issue("PROJ-1", "done", site_url=SITE, email=EMAIL, token=TOKEN)
    assert ok is True
    assert calls[0][0] == "GET" and calls[1][0] == "POST"


def test_transition_issue_no_matching_target_is_false():
    with patch(
        "cockpit.lib.jira.urllib.request.urlopen",
        return_value=_FakeResp({"transitions": [{"id": "11", "to": {"name": "Todo"}}]}),
    ):
        assert (
            transition_issue("P-1", "Done", site_url=SITE, email=EMAIL, token=TOKEN)
            is False
        )


def test_transition_issue_get_failure_is_false():
    with patch("cockpit.lib.jira.urllib.request.urlopen", side_effect=TimeoutError()):
        assert (
            transition_issue("P-1", "Done", site_url=SITE, email=EMAIL, token=TOKEN)
            is False
        )


def test_transition_issue_no_creds_is_false():
    with (
        patch("cockpit.lib.jira.urllib.request.urlopen") as urlopen,
        patch.dict("os.environ", {}, clear=True),
    ):
        assert transition_issue("P-1", "Done", site_url=SITE, email=EMAIL) is False
    urlopen.assert_not_called()
