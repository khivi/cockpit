"""Tests for the worktree table cells (cockpit/tui/widgets/worktree_table.py).

`worktree_cells` is a pure function — no Textual. Seeds the same flat cache
cells the daemon writes, then asserts the per-column Rich Text. Columns are
Workspace | PR | Approval | CI | comments | Title (+ Linear when configured); the
repo is conveyed by tinting the workspace name (not a column).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import cockpit.lib.cache as cache_mod
from cockpit.lib.git import Worktree
from cockpit.tui.widgets.worktree_table import _BASE_COLUMNS, worktree_cells


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    cdir = tmp_path / "cockpit-cache"
    cdir.mkdir()
    monkeypatch.setattr(cache_mod, "FLAT_CACHE_DIR", cdir)
    return cdir


def _wt(path="/tmp/feat", branch="khivi/feat-x", **kw):
    return Worktree(path=Path(path), branch=branch, **kw)


def _plain(wt, repo="repo", color=None, linear=False, show_linear=False):
    cells = worktree_cells(wt, repo, color, linear, show_linear=show_linear)
    return [c.plain for c in cells]


def test_cell_count_matches_columns(cache_dir):
    assert len(_plain(_wt())) == len(_BASE_COLUMNS) == 6
    assert _BASE_COLUMNS[0] == "Workspace" and _BASE_COLUMNS[2] == "Approval"
    # Ticket + Status columns appended only when show_linear
    assert len(_plain(_wt(), show_linear=True)) == 8


def test_workspace_label_strips_prefix(cache_dir):
    # branch_prefix is threaded onto the Worktree from repo config in production.
    wt = _wt(branch="khivi/my-feature", branch_prefix="khivi/")
    assert _plain(wt)[0] == "my-feature"


def test_workspace_tinted_by_repo_color(cache_dir):
    wt = _wt(branch="khivi/c", branch_prefix="khivi/")
    colored = worktree_cells(wt, "r", "Blue", False, show_linear=False)[0]
    plain = worktree_cells(wt, "r", None, False, show_linear=False)[0]
    assert colored.plain == "c" == plain.plain
    assert colored.spans  # Text.from_ansi(colorizer(...)) → colour spans
    assert not plain.spans
    assert "bold" in str(plain.style)


def test_unknown_color_falls_back_to_plain(cache_dir):
    cell = worktree_cells(_wt(), "r", "NotAColor", False, show_linear=False)[0]
    assert not cell.spans


def test_pr_columns_with_friendly_approval(cache_dir):
    wt = _wt(branch="khivi/feat-pr")
    cache_mod.branch_cache("pr-num", wt.branch).write_text("123")
    cache_mod.branch_cache("pr-state", wt.branch).write_text("APPROVED")
    cache_mod.branch_cache("pr-checks", wt.branch).write_text("✓")
    cache_mod.branch_cache("pr-comments", wt.branch).write_text("2")
    cache_mod.branch_cache("pr-title", wt.branch).write_text("Add the thing")
    cells = _plain(wt)  # Workspace, PR, Approval, CI, 💬, Title
    assert cells[1] == "#123"
    assert cells[2] == "Approved"  # friendly label, not the raw enum
    assert cells[3] == "✓"
    assert cells[4] == "2"
    assert cells[5] == "Add the thing"


@pytest.mark.parametrize(
    "raw,label",
    [
        ("DRAFT", "Draft"),
        ("REVIEW_REQUIRED", "Waiting"),
        ("CHANGES_REQUESTED", "Changes"),
        ("MERGED", "Merged"),
    ],
)
def test_approval_friendly_labels(cache_dir, raw, label):
    wt = _wt(branch=f"khivi/{raw.lower()}")
    cache_mod.branch_cache("pr-state", wt.branch).write_text(raw)
    assert _plain(wt)[2] == label


def test_changes_requested_colored_red(cache_dir):
    wt = _wt(branch="khivi/cr")
    cache_mod.branch_cache("pr-state", wt.branch).write_text("CHANGES_REQUESTED")
    cell = worktree_cells(wt, "r", None, False, show_linear=False)[2]
    assert cell.plain == "Changes"
    assert "red" in str(cell.style)


def test_zero_comments_is_blank(cache_dir):
    wt = _wt(branch="khivi/zero")
    cache_mod.branch_cache("pr-comments", wt.branch).write_text("0")
    assert _plain(wt)[4] == ""


def test_no_pr_leaves_columns_blank(cache_dir):
    cells = _plain(_wt(branch="khivi/bare"))
    assert cells[1] == "" and cells[2] == "" and cells[3] == ""


def test_long_title_truncated(cache_dir):
    wt = _wt(branch="khivi/long")
    cache_mod.branch_cache("pr-title", wt.branch).write_text("x" * 80)
    title = _plain(wt)[5]
    assert title.endswith("…")
    assert len(title) <= 49


def test_ticket_and_status_columns_when_enabled(cache_dir, monkeypatch):
    wt = _wt(branch="khivi/lin")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {
            "linear": {"tickets": [{"id": "PE-1", "state": "Dev Done"}]}
        },
    )
    cells = worktree_cells(wt, "r", None, True, show_linear=True)
    ticket, status = cells[6], cells[7]  # Ticket, Status
    assert ticket.plain == "PE-1"
    assert status.plain == "Dev Done"
    assert "green" in str(status.style)  # all tickets done → green status


def test_ticket_status_blank_for_non_linear_repo(cache_dir, monkeypatch):
    wt = _wt(branch="khivi/nl")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {"linear": {"tickets": [{"id": "PE-9", "state": "x"}]}},
    )
    # columns exist (some other repo is Linear) but this row's repo isn't
    cells = worktree_cells(wt, "r", None, False, show_linear=True)
    assert cells[6].plain == "" and cells[7].plain == ""


def test_no_linear_columns_when_not_configured(cache_dir):
    # show_linear False → no Ticket/Status cells
    assert len(_plain(_wt(), linear=True, show_linear=False)) == 6
