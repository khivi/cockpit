"""Tests for scripts/lib/slack.py — URL parser only.

No network surface here: Slack thread bodies are fetched by Claude via the
Slack MCP from the spawned workspace, not by cockpit. See `test_spawn.py`
for the spawn-side dispatch (which uses the parsed channel + ts to derive
a branch name and seed an MCP-instructing prompt).
"""

from __future__ import annotations

from scripts.lib.slack import parse_url


def test_parse_url_extracts_channel_and_ts():
    got = parse_url("https://acme.slack.com/archives/C0123ABC/p1700000000123456")
    assert got == ("C0123ABC", "1700000000.123456")


def test_parse_url_http_also_matches():
    got = parse_url("http://acme.slack.com/archives/C0123ABC/p1700000000123456")
    assert got == ("C0123ABC", "1700000000.123456")


def test_parse_url_thread_ts_overrides_path_ts():
    """A URL with `?thread_ts=…` (Slack-generated reply links) resolves to the
    root ts, not the reply's ts in the path."""
    url = (
        "https://acme.slack.com/archives/C0123ABC/p1700000000999999"
        "?thread_ts=1700000000.123456&cid=C0123ABC"
    )
    assert parse_url(url) == ("C0123ABC", "1700000000.123456")


def test_parse_url_returns_none_for_non_slack():
    assert parse_url("https://github.com/owner/repo/pull/1") is None
    assert parse_url("https://acme.slack.com/team/U123") is None
    assert parse_url("not a url") is None


def test_parse_url_returns_none_for_short_ts():
    """A timestamp tail under 7 chars can't carry six fractional digits."""
    assert parse_url("https://acme.slack.com/archives/C1/p1234") is None
