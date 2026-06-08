"""Tests for the read-only workspace card markup (cockpit/tui/widgets/workspace_card.py).

Pure function — no Textual. Seeds the same flat cache cells the daemon writes,
then asserts the rendered markup. Verifies the card is read-only by construction:
it only reads cells we seed here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import cockpit.lib.cache as cache_mod
from cockpit.lib.git import Worktree
from cockpit.tui.widgets.workspace_card import card_markup


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    cdir = tmp_path / "cockpit-cache"
    cdir.mkdir()
    monkeypatch.setattr(cache_mod, "FLAT_CACHE_DIR", cdir)
    return cdir


def _wt(path="/tmp/feat", branch="khivi/feat-x", **kw):
    return Worktree(path=Path(path), branch=branch, **kw)


def test_title_and_no_pr(cache_dir):
    wt = _wt(branch="khivi/my-feature")
    out = card_markup(wt, "repo", linear_enabled=False)
    assert "my-feature" in out  # branch_label strips the khivi/ prefix
    assert "no PR" in out


def test_primary_suffix(cache_dir):
    wt = _wt(is_primary=True)
    assert "(primary)" in card_markup(wt, "repo", linear_enabled=False)


def test_git_state_glyphs(cache_dir):
    wt = _wt()
    cache_mod.cwd_cache("git-branch", wt.path).write_text("main")
    cache_mod.cwd_cache("git-status", wt.path).write_text(
        "1 2 3"
    )  # staged unstaged untracked
    cache_mod.cwd_cache("git-sync", wt.path).write_text("4 5")  # ahead behind
    out = card_markup(wt, "repo", linear_enabled=False)
    assert "⎇ main" in out
    assert "↑4" in out and "↓5" in out
    assert "●1" in out and "✎2" in out and "✚3" in out


def test_pr_line(cache_dir):
    wt = _wt(branch="khivi/feat-pr")
    cache_mod.branch_cache("pr-num", wt.branch).write_text("123")
    cache_mod.branch_cache("pr-state", wt.branch).write_text("APPROVED")
    cache_mod.branch_cache("pr-checks", wt.branch).write_text("✓")
    cache_mod.branch_cache("pr-comments", wt.branch).write_text("2")
    cache_mod.branch_cache("pr-title", wt.branch).write_text("Add the thing")
    out = card_markup(wt, "repo", linear_enabled=False)
    assert "#123" in out
    assert "APPROVED" in out
    assert "💬2" in out
    assert "Add the thing" in out
    assert "no PR" not in out


def test_zero_comments_omitted(cache_dir):
    wt = _wt(branch="khivi/feat-zero")
    cache_mod.branch_cache("pr-num", wt.branch).write_text("9")
    cache_mod.branch_cache("pr-comments", wt.branch).write_text("0")
    assert "💬" not in card_markup(wt, "repo", linear_enabled=False)


def test_linear_line_only_when_enabled(cache_dir, monkeypatch):
    wt = _wt(branch="khivi/feat-linear")
    payload = {"linear": {"tickets": [{"id": "PE-1234", "state": "Dev Done"}]}}
    monkeypatch.setattr(
        "cockpit.tui.widgets.workspace_card.find_pr_payload",
        lambda branch, repo: payload,
    )
    # disabled → no Linear line even though a payload exists
    assert "Linear:" not in card_markup(wt, "repo", linear_enabled=False)
    # enabled → ticket id + state shown
    out = card_markup(wt, "repo", linear_enabled=True)
    assert "Linear:" in out
    assert "PE-1234" in out
    assert "Dev Done" in out


def test_markup_escapes_dynamic_branch(cache_dir):
    # A bracket in a branch/title must not be interpreted as Rich markup.
    wt = _wt(branch="khivi/feat-[brackets]")
    out = card_markup(wt, "repo", linear_enabled=False)
    assert "\\[brackets]" in out
