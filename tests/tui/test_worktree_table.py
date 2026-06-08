"""Tests for the worktree table cells (cockpit/tui/widgets/worktree_table.py).

`worktree_cells` is a pure function — no Textual. Seeds the same flat cache
cells the daemon writes, then asserts the per-column Rich Text. Verifies the
table is read-only by construction: it only reads cells we seed here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import cockpit.lib.cache as cache_mod
from cockpit.lib.git import Worktree
from cockpit.tui.widgets.worktree_table import COLUMN_LABELS, worktree_cells


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    cdir = tmp_path / "cockpit-cache"
    cdir.mkdir()
    monkeypatch.setattr(cache_mod, "FLAT_CACHE_DIR", cdir)
    return cdir


def _wt(path="/tmp/feat", branch="khivi/feat-x", **kw):
    return Worktree(path=Path(path), branch=branch, **kw)


def _plain(wt, repo="repo", linear=False, show_repo=True):
    return [c.plain for c in worktree_cells(wt, repo, linear, show_repo=show_repo)]


def test_cell_count_matches_columns(cache_dir):
    assert len(worktree_cells(_wt(), "r", False, show_repo=True)) == len(COLUMN_LABELS)


def test_repo_shown_only_on_group_head(cache_dir):
    assert _plain(_wt(), repo="needl", show_repo=True)[0] == "needl"
    assert _plain(_wt(), repo="needl", show_repo=False)[0] == ""


def test_workspace_label_strips_prefix(cache_dir):
    # branch_prefix is threaded onto the Worktree from repo config in production.
    wt = _wt(branch="khivi/my-feature", branch_prefix="khivi/")
    assert _plain(wt)[1] == "my-feature"


def test_branch_and_git_state(cache_dir):
    wt = _wt()
    cache_mod.cwd_cache("git-branch", wt.path).write_text("main")
    cache_mod.cwd_cache("git-status", wt.path).write_text(
        "1 2 3"
    )  # staged/unstaged/untracked
    cache_mod.cwd_cache("git-sync", wt.path).write_text("4 5")  # ahead behind
    branch = _plain(wt)[2]
    assert "⎇ main" in branch
    assert "↑4" in branch and "↓5" in branch and "●6" in branch  # dirty = 1+2+3


def test_pr_columns(cache_dir):
    wt = _wt(branch="khivi/feat-pr")
    cache_mod.branch_cache("pr-num", wt.branch).write_text("123")
    cache_mod.branch_cache("pr-state", wt.branch).write_text("APPROVED")
    cache_mod.branch_cache("pr-checks", wt.branch).write_text("✓")
    cache_mod.branch_cache("pr-comments", wt.branch).write_text("2")
    cache_mod.branch_cache("pr-title", wt.branch).write_text("Add the thing")
    cells = _plain(wt)  # Repo, Workspace, Branch, PR, State, CI, 💬, Title
    assert cells[3] == "#123"
    assert cells[4] == "APPROVED"
    assert cells[5] == "✓"
    assert cells[6] == "2"
    assert cells[7] == "Add the thing"


def test_zero_comments_is_blank(cache_dir):
    wt = _wt(branch="khivi/zero")
    cache_mod.branch_cache("pr-comments", wt.branch).write_text("0")
    assert _plain(wt)[6] == ""


def test_no_pr_leaves_pr_columns_blank(cache_dir):
    cells = _plain(_wt(branch="khivi/bare"))
    assert cells[3] == "" and cells[4] == "" and cells[5] == ""


def test_linear_id_fills_empty_title_only_when_enabled(cache_dir, monkeypatch):
    wt = _wt(branch="khivi/lin")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {"linear": {"tickets": [{"id": "PE-1", "state": "x"}]}},
    )
    assert _plain(wt, linear=False)[7] == ""  # disabled → no Linear lookup
    assert "PE-1" in _plain(wt, linear=True)[7]


def test_long_title_truncated(cache_dir):
    wt = _wt(branch="khivi/long")
    cache_mod.branch_cache("pr-title", wt.branch).write_text("x" * 80)
    title = _plain(wt)[7]
    assert title.endswith("…")
    assert len(title) <= 49
