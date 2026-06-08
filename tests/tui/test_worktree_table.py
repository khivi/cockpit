"""Tests for the worktree table cells (cockpit/tui/widgets/worktree_table.py).

`worktree_cells` is a pure function — no Textual. Seeds the same flat cache
cells the daemon writes, then asserts the per-column Rich Text. Columns are
Workspace | PR | (Ticket) | Approval | CI | comments | ✎ | (Status) | Title —
the Linear Ticket/Status columns appear only when configured, Ticket after PR and
Status before Title. The repo is conveyed by tinting the workspace name (not a
column). The Dirty column (icon header) reads the per-cwd `git-status` cell
(`"<staged> <unstaged> <untracked>"`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import cockpit.lib.cache as cache_mod
from cockpit.lib.git import Worktree
from cockpit.tui.widgets.worktree_table import (
    _DIRTY_ICON,
    ICON_PR_MUTED,
    column_labels,
    worktree_cells,
)


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
    cols = column_labels(show_linear=False)
    assert len(_plain(_wt())) == len(cols) == 7
    assert cols[0] == "Workspace" and cols[2] == "Approval"
    assert cols[5] == _DIRTY_ICON  # Dirty column header is now an icon
    # Ticket + Status columns added only when show_linear, interleaved:
    # Ticket right after PR, Status right before Title.
    lin = column_labels(show_linear=True)
    assert len(_plain(_wt(), show_linear=True)) == len(lin) == 9
    assert lin[2] == "Ticket"
    assert lin[7] == "Status"
    assert lin[-1] == "Title"


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
    cells = _plain(wt)  # Workspace, PR, Approval, CI, 💬, Dirty, Title
    assert cells[1] == "#123"
    assert cells[2] == "Approved"  # friendly label, not the raw enum
    assert cells[3] == "✓"
    assert cells[4] == "2"
    assert cells[5] == ""  # clean tree (no git-status cell)
    assert cells[6] == "Add the thing"


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
    title = _plain(wt)[6]
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
    ticket, status = cells[2], cells[7]  # Ticket after PR, Status before Title
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
    assert cells[2].plain == "" and cells[7].plain == ""  # Ticket, Status


def test_no_linear_columns_when_not_configured(cache_dir):
    # show_linear False → no Ticket/Status cells
    assert len(_plain(_wt(), linear=True, show_linear=False)) == 7


def test_muted_pr_prefixes_workspace_glyph(cache_dir):
    wt = _wt(branch="khivi/silence", branch_prefix="khivi/")
    cache_mod.branch_cache("pr-muted", wt.branch).write_text("muted")
    cell = worktree_cells(wt, "r", None, False, show_linear=False)[0]
    assert cell.plain == f"{ICON_PR_MUTED} silence"


def test_unmuted_pr_has_no_glyph(cache_dir):
    wt = _wt(branch="khivi/loud", branch_prefix="khivi/")
    cell = worktree_cells(wt, "r", None, False, show_linear=False)[0]
    assert cell.plain == "loud"


def test_dirty_column_renders_counts(cache_dir):
    wt = _wt(path="/tmp/dirtywt", branch="khivi/dirty")
    cache_mod.cwd_cache("git-status", wt.path).write_text("1 2 3")
    dirty = worktree_cells(wt, "r", None, False, show_linear=False)[5]
    # ●1 ✎2 ✚3 with the footer's glyphs
    assert dirty.plain == "●1 ✎2 ✚3"
    assert "green" in str(dirty.spans[0].style)  # staged
    assert "yellow" in str(dirty.spans[1].style)  # unstaged


def test_dirty_column_omits_zero_segments(cache_dir):
    wt = _wt(path="/tmp/partialdirty", branch="khivi/partial")
    cache_mod.cwd_cache("git-status", wt.path).write_text("0 0 4")
    dirty = worktree_cells(wt, "r", None, False, show_linear=False)[5]
    assert dirty.plain == "✚4"  # only untracked shown


def test_dirty_column_blank_when_clean(cache_dir):
    wt = _wt(path="/tmp/cleanwt", branch="khivi/clean")
    cache_mod.cwd_cache("git-status", wt.path).write_text("0 0 0")
    dirty = worktree_cells(wt, "r", None, False, show_linear=False)[5]
    assert dirty.plain == ""


def test_dirty_column_blank_when_cell_missing(cache_dir):
    # Cold start: daemon hasn't written the git-status cell yet.
    wt = _wt(path="/tmp/coldwt", branch="khivi/cold")
    dirty = worktree_cells(wt, "r", None, False, show_linear=False)[5]
    assert dirty.plain == ""
