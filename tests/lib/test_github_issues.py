"""Tests for the GitHub-issue ticket provider transport (`lib.github_issues`).

The `gh`-backed reads/writes mock `subprocess.run` (the transport boundary) —
the live `gh` against real issues isn't hermetic, mirroring how `test_linear.py`
mocks `urlopen`. The pure parsers (footer/URL/shorthand regexes) need no mocks.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from cockpit.lib import github_issues as gh

# ── parse_github_issue_refs (the PR-body delivery signal) ───────────────────


def test_parse_same_repo_closing_keyword():
    body = "Closes #123"
    assert gh.parse_github_issue_refs(body, "o/r") == ["#123"]


@pytest.mark.parametrize(
    "kw", ["close", "closes", "closed", "fix", "fixes", "fixed", "resolve", "resolves"]
)
def test_parse_all_closing_keywords(kw):
    assert gh.parse_github_issue_refs(f"{kw} #7", "o/r") == ["#7"]


def test_parse_keyword_case_insensitive_and_colon():
    assert gh.parse_github_issue_refs("FIXES: #9", "o/r") == ["#9"]


def test_parse_cross_repo_ref_keeps_nwo():
    assert gh.parse_github_issue_refs("Fixes other/repo#45", "o/r") == ["other/repo#45"]


def test_parse_same_repo_url_renders_short():
    body = "Resolves https://github.com/o/r/issues/8"
    assert gh.parse_github_issue_refs(body, "o/r") == ["#8"]


def test_parse_cross_repo_url_keeps_nwo():
    body = "Closes https://github.com/other/repo/issues/8"
    assert gh.parse_github_issue_refs(body, "o/r") == ["other/repo#8"]


def test_parse_bare_mention_without_keyword_ignored():
    # A `#123` not preceded by a closing keyword is NOT a delivery signal.
    assert gh.parse_github_issue_refs("see #123 for context", "o/r") == []


def test_parse_dedup_and_order_preserved():
    body = "Closes #1\nFixes #2\nalso closes #1 again"
    assert gh.parse_github_issue_refs(body, "o/r") == ["#1", "#2"]


def test_parse_empty_body():
    assert gh.parse_github_issue_refs("", "o/r") == []
    assert gh.parse_github_issue_refs(None, "o/r") == []  # type: ignore[arg-type]


# ── spawn-source regexes ────────────────────────────────────────────────────


def test_issue_url_regex_matches_issues_not_pulls():
    m = gh.GITHUB_ISSUE_URL_RE.fullmatch("https://github.com/o/r/issues/42")
    assert m and m.group(1) == "o/r" and m.group(2) == "42"
    assert gh.GITHUB_ISSUE_URL_RE.fullmatch("https://github.com/o/r/pull/42") is None


@pytest.mark.parametrize("token,num", [("i#5", "5"), ("gh#5", "5"), ("I#5", "5")])
def test_shorthand_regex(token, num):
    m = gh.GITHUB_ISSUE_SHORTHAND_RE.fullmatch(token)
    assert m and m.group(1) == num


def test_shorthand_regex_rejects_bare_hash():
    assert gh.GITHUB_ISSUE_SHORTHAND_RE.fullmatch("#5") is None


# ── fetch_issue / fetch_issues ──────────────────────────────────────────────


def _run(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout)


_ISSUE_JSON = (
    '{"number": 123, "state": "OPEN", '
    '"labels": [{"name": "Dev Done"}, {"name": "bug"}], '
    '"assignees": [{"login": "khivi"}], '
    '"url": "https://github.com/o/r/issues/123", "title": "Fix it"}'
)


def test_fetch_issue_happy_path():
    with patch.object(gh.subprocess, "run", return_value=_run(_ISSUE_JSON)) as run:
        out = gh.fetch_issue("#123", repo_nwo="o/r")
    assert out == {
        "ref": "#123",
        "nwo": "o/r",
        "number": 123,
        "state": "open",
        "labels": ["dev done", "bug"],
        "assignees": ["khivi"],
        "url": "https://github.com/o/r/issues/123",
        "title": "Fix it",
    }
    # routed to the right repo + number
    args = run.call_args[0][0]
    assert args[:3] == ["gh", "issue", "view"] and "123" in args and "o/r" in args


def test_fetch_issue_cross_repo_ref_uses_embedded_nwo():
    with patch.object(gh.subprocess, "run", return_value=_run(_ISSUE_JSON)) as run:
        out = gh.fetch_issue("other/repo#5", repo_nwo="o/r")
    assert out is not None and out["nwo"] == "other/repo"
    assert "other/repo" in run.call_args[0][0]


def test_fetch_issue_malformed_json_returns_none():
    # `gh` exits 0 but the body isn't valid JSON — `_gh_json` must degrade to
    # None like any other failure, not raise json.JSONDecodeError.
    with patch.object(gh.subprocess, "run", return_value=_run("not json {")):
        assert gh.fetch_issue("#1", repo_nwo="o/r") is None


def test_fetch_issue_gh_failure_returns_none():
    with patch.object(gh.subprocess, "run", return_value=_run("", returncode=1)):
        assert gh.fetch_issue("#1", repo_nwo="o/r") is None


def test_fetch_issue_timeout_returns_none():
    with patch.object(
        gh.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 15)
    ):
        assert gh.fetch_issue("#1", repo_nwo="o/r") is None


def test_fetch_issue_unparseable_ref_no_network():
    with patch.object(gh.subprocess, "run") as run:
        assert gh.fetch_issue("not-a-ref", repo_nwo="o/r") is None
    run.assert_not_called()


def test_fetch_issues_batches_and_dedups():
    with patch.object(gh.subprocess, "run", return_value=_run(_ISSUE_JSON)) as run:
        out = gh.fetch_issues(["#1", "#1", "#2"], repo_nwo="o/r")
    assert set(out) == {"#1", "#2"}
    assert run.call_count == 2  # one per distinct ref


# ── viewer_login / close_issue ──────────────────────────────────────────────


def test_viewer_login_happy_path():
    with patch.object(gh.subprocess, "run", return_value=_run('{"login": "khivi"}')):
        assert gh.viewer_login() == "khivi"


def test_viewer_login_failure_returns_none():
    with patch.object(gh.subprocess, "run", return_value=_run("", returncode=1)):
        assert gh.viewer_login() is None


def test_close_issue_success():
    with patch.object(gh.subprocess, "run", return_value=_run("done")) as run:
        assert gh.close_issue("#123", repo_nwo="o/r") is True
    args = run.call_args[0][0]
    assert args[:3] == ["gh", "issue", "close"] and "123" in args


def test_close_issue_failure():
    with patch.object(gh.subprocess, "run", return_value=_run("", returncode=1)):
        assert gh.close_issue("#1", repo_nwo="o/r") is False


def test_close_issue_unparseable_ref_no_network():
    with patch.object(gh.subprocess, "run") as run:
        assert gh.close_issue("nope", repo_nwo="o/r") is False
    run.assert_not_called()


# ── add_label (start-label write) ───────────────────────────────────────────


def test_add_label_success():
    with patch.object(gh.subprocess, "run", return_value=_run("ok")) as run:
        assert gh.add_label("#123", "accepted", repo_nwo="o/r") is True
    args = run.call_args[0][0]
    assert args[:3] == ["gh", "issue", "edit"]
    assert "123" in args and "--add-label" in args and "accepted" in args


def test_add_label_failure():
    with patch.object(gh.subprocess, "run", return_value=_run("", returncode=1)):
        assert gh.add_label("#1", "accepted", repo_nwo="o/r") is False


def test_add_label_empty_label_no_network():
    with patch.object(gh.subprocess, "run") as run:
        assert gh.add_label("#1", "  ", repo_nwo="o/r") is False
    run.assert_not_called()


def test_add_label_unparseable_ref_no_network():
    with patch.object(gh.subprocess, "run") as run:
        assert gh.add_label("nope", "accepted", repo_nwo="o/r") is False
    run.assert_not_called()


# ── issue_url (the `tickets: github` provider's ticket URL) ──────────────────


def test_issue_url_same_repo_resolves_nwo():
    assert gh.issue_url("#42", "o/r") == "https://github.com/o/r/issues/42"


def test_issue_url_cross_repo_keeps_own_nwo():
    assert gh.issue_url("other/x#9", "o/r") == "https://github.com/other/x/issues/9"


def test_issue_url_none_without_nwo_or_number():
    assert gh.issue_url("#5", None) is None
    assert gh.issue_url("nope", "o/r") is None
