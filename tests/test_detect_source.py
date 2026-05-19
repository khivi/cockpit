"""Unit tests for spawn.detect_source (pure function, no I/O)."""

from __future__ import annotations

from spawn import detect_source


def test_pr_url_returns_pr_mode_and_nwo():
    mode, value, nwo = detect_source("https://github.com/owner/repo/pull/42")
    assert mode == "pr"
    assert value == "42"
    assert nwo == "owner/repo"


def test_pr_url_http_also_matches():
    mode, value, nwo = detect_source("http://github.com/owner/repo/pull/7")
    assert mode == "pr"
    assert value == "7"
    assert nwo == "owner/repo"


def test_hash_prefix_returns_pr_mode_no_nwo():
    mode, value, nwo = detect_source("#123")
    assert mode == "pr"
    assert value == "123"
    assert nwo is None


def test_bare_integer_is_branch_not_pr():
    mode, value, nwo = detect_source("123")
    assert mode == "branch"
    assert value == "123"
    assert nwo is None


def test_branch_name_returns_branch_mode():
    mode, value, nwo = detect_source("khivi/my-feature")
    assert mode == "branch"
    assert value == "khivi/my-feature"
    assert nwo is None
