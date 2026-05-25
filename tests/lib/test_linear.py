"""Tests for scripts/lib/linear.py — regex + GraphQL resolver.

Mocks at the `urllib.request.urlopen` boundary (the lib's only IO) so the
tests run offline and exercise every fail-soft path: missing env, network
error, HTTP error, GraphQL error, malformed JSON, empty issue.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch

import pytest

import scripts.lib.linear as linear_mod
from scripts.lib.linear import (
    LINEAR_RE,
    LINEAR_RE_CI,
    ResolvedIssue,
    extract_ticket,
    resolve_issue,
)

# ── regex / extract_ticket ────────────────────────────────────────────────


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


def test_extract_ticket_empty_returns_empty():
    assert extract_ticket("") == ""
    assert extract_ticket("khivi/no-ticket") == ""


# ── resolve_issue fail-soft paths ─────────────────────────────────────────


def _fake_urlopen(payload: dict | bytes, *, raise_exc: Exception | None = None):
    """Build a context-manager mock for `urllib.request.urlopen`."""

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


def test_resolve_issue_returns_none_without_api_key(monkeypatch, capsys):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    assert resolve_issue("PE-1234") is None
    err = capsys.readouterr().err
    assert "LINEAR_API_KEY not set" in err


def test_resolve_issue_returns_none_on_empty_api_key(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "   ")
    assert resolve_issue("PE-1234") is None


@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.URLError("nope"),
        TimeoutError("slow"),
        OSError("network down"),
    ],
)
def test_resolve_issue_returns_none_on_network_error(monkeypatch, capsys, exc):
    monkeypatch.setenv("LINEAR_API_KEY", "key")
    with patch.object(
        linear_mod.urllib.request, "urlopen", _fake_urlopen({}, raise_exc=exc)
    ):
        assert resolve_issue("PE-1234") is None
    assert "lookup failed" in capsys.readouterr().err


def test_resolve_issue_returns_none_on_malformed_json(monkeypatch, capsys):
    monkeypatch.setenv("LINEAR_API_KEY", "key")
    with patch.object(
        linear_mod.urllib.request, "urlopen", _fake_urlopen(b"not-json{")
    ):
        assert resolve_issue("PE-1234") is None
    assert "malformed response" in capsys.readouterr().err


def test_resolve_issue_returns_none_on_graphql_error(monkeypatch, capsys):
    monkeypatch.setenv("LINEAR_API_KEY", "key")
    payload = {"errors": [{"message": "invalid token"}]}
    with patch.object(linear_mod.urllib.request, "urlopen", _fake_urlopen(payload)):
        assert resolve_issue("PE-1234") is None
    err = capsys.readouterr().err
    assert "GraphQL error" in err
    assert "invalid token" in err


def test_resolve_issue_returns_none_when_issue_missing(monkeypatch, capsys):
    monkeypatch.setenv("LINEAR_API_KEY", "key")
    payload = {"data": {"issue": None}}
    with patch.object(linear_mod.urllib.request, "urlopen", _fake_urlopen(payload)):
        assert resolve_issue("PE-1234") is None
    assert "no issue" in capsys.readouterr().err


def test_resolve_issue_happy_path(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "key")
    payload = {
        "data": {
            "issue": {
                "identifier": "PE-1234",
                "title": "Add login flow",
                "description": "Users need to log in.",
                "url": "https://linear.app/team/issue/PE-1234",
                "branchName": "khivi/pe-1234-add-login-flow",
            }
        }
    }
    with patch.object(linear_mod.urllib.request, "urlopen", _fake_urlopen(payload)):
        issue = resolve_issue("PE-1234")
    assert isinstance(issue, ResolvedIssue)
    assert issue.identifier == "PE-1234"
    assert issue.title == "Add login flow"
    assert issue.description == "Users need to log in."
    assert issue.url == "https://linear.app/team/issue/PE-1234"
    assert issue.branch_name == "khivi/pe-1234-add-login-flow"


def test_resolve_issue_tolerates_missing_optional_fields(monkeypatch):
    """API responses may omit url/branchName for issues created without them.
    Resolver must coerce nulls to empty strings, not propagate `None`."""
    monkeypatch.setenv("LINEAR_API_KEY", "key")
    payload = {
        "data": {
            "issue": {
                "identifier": "PE-1",
                "title": "t",
                "description": None,
                "url": None,
                "branchName": None,
            }
        }
    }
    with patch.object(linear_mod.urllib.request, "urlopen", _fake_urlopen(payload)):
        issue = resolve_issue("PE-1")
    assert issue is not None
    assert issue.description == ""
    assert issue.url == ""
    assert issue.branch_name == ""


def test_resolve_issue_sends_authorization_header(monkeypatch):
    """The Linear API expects the raw key in the Authorization header (no
    `Bearer ` prefix). Regression guard."""
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    captured: dict = {}

    def _capture(req, *_a, **_kw):
        captured["headers"] = dict(req.header_items())
        captured["data"] = req.data

        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(
                    {"data": {"issue": {"identifier": "PE-1", "title": "t"}}}
                ).encode()

        return _R()

    with patch.object(linear_mod.urllib.request, "urlopen", _capture):
        resolve_issue("PE-1")

    # urllib lowercases header names in `header_items()`.
    lower = {k.lower(): v for k, v in captured["headers"].items()}
    assert lower["authorization"] == "test-key"
    body = json.loads(captured["data"])
    assert body["variables"]["id"] == "PE-1"
