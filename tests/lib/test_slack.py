"""Tests for scripts/lib/slack.py — URL parser + thread resolver.

Mocks at `urllib.request.urlopen`. Covers URL parsing edge cases (bad URL,
short ts, thread_ts override) and every fail-soft path on resolve.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch

import pytest

import scripts.lib.slack as slack_mod
from scripts.lib.slack import (
    ResolvedThread,
    parse_url,
    resolve_thread,
)

# ── parse_url ──────────────────────────────────────────────────────────────


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


# ── resolve_thread fail-soft paths ─────────────────────────────────────────


def _fake_urlopen(payload: dict | bytes, *, raise_exc: Exception | None = None):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self_inner):
            if isinstance(payload, bytes):
                return payload
            return json.dumps(payload).encode()

    def _factory(*_a, **_kw):
        if raise_exc is not None:
            raise raise_exc
        return _Resp()

    return _factory


def test_resolve_thread_returns_none_on_unparsable_url(monkeypatch, capsys):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    assert resolve_thread("not a slack url") is None
    assert "unparsable URL" in capsys.readouterr().err


def test_resolve_thread_returns_none_without_token(monkeypatch, capsys):
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    url = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
    assert resolve_thread(url) is None
    assert "SLACK_TOKEN not set" in capsys.readouterr().err


@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.URLError("nope"),
        TimeoutError("slow"),
        OSError("network down"),
    ],
)
def test_resolve_thread_returns_none_on_network_error(monkeypatch, capsys, exc):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    url = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
    with patch.object(
        slack_mod.urllib.request, "urlopen", _fake_urlopen({}, raise_exc=exc)
    ):
        assert resolve_thread(url) is None
    assert "lookup failed" in capsys.readouterr().err


def test_resolve_thread_returns_none_on_malformed_json(monkeypatch, capsys):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    url = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
    with patch.object(slack_mod.urllib.request, "urlopen", _fake_urlopen(b"junk")):
        assert resolve_thread(url) is None
    assert "malformed response" in capsys.readouterr().err


def test_resolve_thread_returns_none_on_api_error(monkeypatch, capsys):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    url = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
    payload = {"ok": False, "error": "not_in_channel"}
    with patch.object(slack_mod.urllib.request, "urlopen", _fake_urlopen(payload)):
        assert resolve_thread(url) is None
    err = capsys.readouterr().err
    assert "API error" in err
    assert "not_in_channel" in err


def test_resolve_thread_returns_none_on_empty_messages(monkeypatch, capsys):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    url = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
    with patch.object(
        slack_mod.urllib.request,
        "urlopen",
        _fake_urlopen({"ok": True, "messages": []}),
    ):
        assert resolve_thread(url) is None
    assert "no messages" in capsys.readouterr().err


def test_resolve_thread_happy_path(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    url = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
    payload = {
        "ok": True,
        "messages": [
            {
                "text": "Investigate login flake",
                "reply_count": 3,
                "ts": "1700000000.123456",
            }
        ],
    }
    with patch.object(slack_mod.urllib.request, "urlopen", _fake_urlopen(payload)):
        thread = resolve_thread(url)
    assert isinstance(thread, ResolvedThread)
    assert thread.channel == "C0123ABC"
    assert thread.ts == "1700000000.123456"
    assert thread.text == "Investigate login flake"
    assert thread.reply_count == 3
    assert thread.permalink == url


def test_resolve_thread_tolerates_missing_optional_fields(monkeypatch):
    """File-only or attachment-only first messages may have empty text and no
    reply_count. Resolver must coerce to empty/zero, not crash."""
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    url = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
    payload = {"ok": True, "messages": [{}]}
    with patch.object(slack_mod.urllib.request, "urlopen", _fake_urlopen(payload)):
        thread = resolve_thread(url)
    assert thread is not None
    assert thread.text == ""
    assert thread.reply_count == 0


def test_resolve_thread_sends_bearer_token(monkeypatch):
    """Slack expects `Bearer <token>` in Authorization (unlike Linear)."""
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")
    url = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
    captured: dict = {}

    def _capture(req, *_a, **_kw):
        captured["headers"] = dict(req.header_items())
        captured["full_url"] = req.full_url

        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"ok": True, "messages": [{"text": "t"}]}).encode()

        return _R()

    with patch.object(slack_mod.urllib.request, "urlopen", _capture):
        resolve_thread(url)

    lower = {k.lower(): v for k, v in captured["headers"].items()}
    assert lower["authorization"] == "Bearer xoxb-test"
    # Channel + ts must be in the query string.
    assert "channel=C0123ABC" in captured["full_url"]
    assert "ts=1700000000.123456" in captured["full_url"]
