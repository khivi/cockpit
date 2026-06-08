"""Tests for the worktree table cells (cockpit/tui/widgets/worktree_table.py).

`worktree_cells` is a pure function — no Textual. Seeds the same flat cache
cells the daemon writes, then asserts the per-column Rich Text. Columns are
Workspace | PR | State | CI | comments | Title; the repo is conveyed by tinting
the workspace name (not a column).
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


def _plain(wt, repo="repo", color=None, linear=False):
    return [c.plain for c in worktree_cells(wt, repo, color, linear)]


def test_cell_count_matches_columns(cache_dir):
    assert len(worktree_cells(_wt(), "r", None, False)) == len(COLUMN_LABELS)
    assert COLUMN_LABELS[0] == "Workspace"


def test_workspace_label_strips_prefix(cache_dir):
    # branch_prefix is threaded onto the Worktree from repo config in production.
    wt = _wt(branch="khivi/my-feature", branch_prefix="khivi/")
    assert _plain(wt)[0] == "my-feature"


def test_workspace_tinted_by_repo_color(cache_dir):
    wt = _wt(branch="khivi/c", branch_prefix="khivi/")
    # With a valid cmux colour the cell carries colour spans; without, it's plain bold.
    colored = worktree_cells(wt, "r", "Blue", False)[0]
    plain = worktree_cells(wt, "r", None, False)[0]
    assert colored.plain == "c" == plain.plain
    assert colored.spans  # Text.from_ansi(colorizer(...)) → colour spans
    assert not plain.spans
    assert "bold" in str(plain.style)


def test_unknown_color_falls_back_to_plain(cache_dir):
    cell = worktree_cells(_wt(), "r", "NotAColor", False)[0]
    assert not cell.spans


def test_pr_columns(cache_dir):
    wt = _wt(branch="khivi/feat-pr")
    cache_mod.branch_cache("pr-num", wt.branch).write_text("123")
    cache_mod.branch_cache("pr-state", wt.branch).write_text("APPROVED")
    cache_mod.branch_cache("pr-checks", wt.branch).write_text("✓")
    cache_mod.branch_cache("pr-comments", wt.branch).write_text("2")
    cache_mod.branch_cache("pr-title", wt.branch).write_text("Add the thing")
    cells = _plain(wt)  # Workspace, PR, State, CI, 💬, Title
    assert cells[1] == "#123"
    assert cells[2] == "APPROVED"
    assert cells[3] == "✓"
    assert cells[4] == "2"
    assert cells[5] == "Add the thing"


def test_zero_comments_is_blank(cache_dir):
    wt = _wt(branch="khivi/zero")
    cache_mod.branch_cache("pr-comments", wt.branch).write_text("0")
    assert _plain(wt)[4] == ""


def test_no_pr_leaves_pr_columns_blank(cache_dir):
    cells = _plain(_wt(branch="khivi/bare"))
    assert cells[1] == "" and cells[2] == "" and cells[3] == ""


def test_state_colored(cache_dir):
    wt = _wt(branch="khivi/cr")
    cache_mod.branch_cache("pr-state", wt.branch).write_text("CHANGES_REQUESTED")
    state_cell = worktree_cells(wt, "r", None, False)[2]
    assert state_cell.plain == "CHANGES_REQUESTED"
    assert "red" in str(state_cell.style)


def test_linear_id_fills_empty_title_only_when_enabled(cache_dir, monkeypatch):
    wt = _wt(branch="khivi/lin")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {"linear": {"tickets": [{"id": "PE-1", "state": "x"}]}},
    )
    assert _plain(wt, linear=False)[5] == ""  # disabled → no Linear lookup
    assert "PE-1" in _plain(wt, linear=True)[5]


def test_long_title_truncated(cache_dir):
    wt = _wt(branch="khivi/long")
    cache_mod.branch_cache("pr-title", wt.branch).write_text("x" * 80)
    title = _plain(wt)[5]
    assert title.endswith("…")
    assert len(title) <= 49
