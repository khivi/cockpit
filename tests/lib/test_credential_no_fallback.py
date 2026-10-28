"""An empty resolved per-repo credential means "no credential": the helpers must
not substitute the default env var and must make no request."""

from __future__ import annotations

from unittest.mock import patch

from cockpit.lib import jira, linear, tickets, trello


def test_jira_empty_token_does_not_fall_back_to_default(monkeypatch):
    monkeypatch.setenv("JIRA_API_TOKEN", "default-org-token")
    monkeypatch.delenv("ORGB_JIRA", raising=False)
    cfg = {
        "tickets": {
            "provider": "jira",
            "site_url": "https://b.example.net",
            "email": "b@example.com",
            "token_env": "ORGB_JIRA",
        }
    }
    with patch("urllib.request.urlopen") as urlopen:
        out = tickets._jira_fetch_states(
            ["ABC-1"], repo_nwo="", repo_dir="", cfg=cfg, repo_entry={}
        )
    urlopen.assert_not_called()
    assert out.get("ABC-1") is None


def test_helpers_keep_default_when_none(monkeypatch):
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    monkeypatch.setenv("TRELLO_API_KEY", "k")
    monkeypatch.setenv("TRELLO_API_TOKEN", "tt")
    assert jira._creds("e@x", None) == ("e@x", "t")
    assert jira._creds("e@x", "") is None
    assert trello._creds(None, None) == ("k", "tt")
    assert trello._creds("", None) is None


def test_linear_empty_key_makes_no_request(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "default")
    with patch("urllib.request.urlopen") as urlopen:
        out = linear.fetch_ticket_states(["ENG-1"], api_key="")
    urlopen.assert_not_called()
    assert out == {"ENG-1": None}
