"""Tests for the worktree table cells (cockpit/tui/widgets/worktree_table.py).

`worktree_cells` is a pure function — no Textual. Seeds the same flat cache
cells the daemon writes, then asserts the per-column Rich Text. Columns are
Workspace | PR | Author | (Ticket) | 🔀 | (Status) | CI | comments | ✎ | Title —
the Author column is always present (blank for self-authored PRs, the coworker
login for a review PR); the Linear Ticket/Status columns appear only when
configured, Ticket after Author and Status right after the PR-state column. The
repo is conveyed by a group-header row plus a tint on the workspace name (not a
column). The Dirty column (icon header)
reads the per-cwd `git-status` cell (`"<staged> <unstaged> <untracked>"`).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

import cockpit.lib.cache as cache_mod
from cockpit.lib.git import Worktree
from cockpit.tui.widgets.worktree_table import (
    _APPROVAL_ICON,
    _DIRTY_ICON,
    _LINEAR_STATUS_FALLBACK,
    _PR_STATE_ICON,
    _STATUS_ICON,
    DEVDONE_ICON,
    HEADER_KEY_PREFIX,
    ICON_PR_MUTED,
    ICON_PR_NUDGE,
    WorktreeTable,
    _comments_cell,
    _header_cells,
    _linear_status_icon,
    column_labels,
    row_capabilities,
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


def _plain(wt, repo="repo", color=None, linear=False, show_tickets=False):
    cells = worktree_cells(wt, repo, color, linear, show_tickets=show_tickets)
    return [c.plain for c in cells]


def test_cell_count_matches_columns(cache_dir):
    cols = column_labels(show_tickets=False)
    assert len(_plain(_wt())) == len(cols) == 8
    assert cols[0] == "Workspace" and cols[1] == "PR" and cols[2] == "Author"
    assert cols[3] == _APPROVAL_ICON
    assert cols[6] == _DIRTY_ICON  # Dirty column header is now an icon
    # Ticket + Status columns added only when show_tickets, interleaved:
    # Ticket right after Author, Status right after the PR-state column.
    lin = column_labels(show_tickets=True)
    assert len(_plain(_wt(), show_tickets=True)) == len(lin) == 10
    assert lin[3] == "Ticket"
    assert lin[4] == _APPROVAL_ICON
    assert lin[5] == _STATUS_ICON  # Status column header is now an icon, after PR state
    assert lin[-1] == "Title"


def test_workspace_label_strips_prefix(cache_dir):
    # branch_prefix is threaded onto the Worktree from repo config in production.
    wt = _wt(branch="khivi/my-feature", branch_prefix="khivi/")
    assert _plain(wt)[0] == "my-feature"


def test_workspace_tinted_by_repo_color(cache_dir):
    wt = _wt(branch="khivi/c", branch_prefix="khivi/")
    colored = worktree_cells(wt, "r", "Blue", False, show_tickets=False)[0]
    plain = worktree_cells(wt, "r", None, False, show_tickets=False)[0]
    assert colored.plain == "c" == plain.plain
    assert colored.spans  # Text.from_ansi(colorizer(...)) → colour spans
    assert not plain.spans
    assert "bold" in str(plain.style)


def test_unknown_color_falls_back_to_plain(cache_dir):
    cell = worktree_cells(_wt(), "r", "NotAColor", False, show_tickets=False)[0]
    assert not cell.spans


def test_pr_columns_with_state_icon(cache_dir):
    wt = _wt(branch="khivi/feat-pr")
    cache_mod.branch_cache("pr-num", wt.branch).write_text("123")
    cache_mod.branch_cache("pr-state", wt.branch).write_text("APPROVED")
    cache_mod.branch_cache("pr-checks", wt.branch).write_text("✓")
    cache_mod.branch_cache("pr-comments", wt.branch).write_text("2")
    cache_mod.branch_cache("pr-title", wt.branch).write_text("Add the thing")
    cells = _plain(wt)  # Workspace, PR, Author, state-icon, CI, 💬, Dirty, Title
    assert cells[1] == "#123"
    assert cells[2] == ""  # self-authored → no author shown
    assert cells[3] == _PR_STATE_ICON["APPROVED"]  # icon, not a text label
    assert cells[4] == "✓"
    assert cells[5] == "2"
    assert cells[6] == ""  # clean tree (no git-status cell)
    assert cells[7] == "Add the thing"


@pytest.mark.parametrize(
    "raw", ["DRAFT", "REVIEW_REQUIRED", "CHANGES_REQUESTED", "MERGED"]
)
def test_approval_state_icons(cache_dir, raw):
    wt = _wt(branch=f"khivi/{raw.lower()}")
    cache_mod.branch_cache("pr-state", wt.branch).write_text(raw)
    assert _plain(wt)[3] == _PR_STATE_ICON[raw]


def test_changes_requested_colored_red(cache_dir):
    wt = _wt(branch="khivi/cr")
    cache_mod.branch_cache("pr-state", wt.branch).write_text("CHANGES_REQUESTED")
    cell = worktree_cells(wt, "r", None, False, show_tickets=False)[3]
    assert cell.plain == _PR_STATE_ICON["CHANGES_REQUESTED"]
    assert "red" in str(cell.style)


def test_author_column_shows_coworker_login(cache_dir):
    # The daemon writes `pr-author` only for other-authored PRs; the table
    # renders it `@login`.
    wt = _wt(branch="coworker/feat")
    cache_mod.branch_cache("pr-author", wt.branch).write_text("octocat")
    cell = worktree_cells(wt, "r", None, False, show_tickets=False)[2]
    assert cell.plain == "@octocat"
    assert "cyan" in str(cell.style)


def test_author_column_blank_for_self_authored(cache_dir):
    # Self-authored PR → daemon leaves `pr-author` empty → blank Author cell.
    wt = _wt(branch="khivi/mine")
    assert _plain(wt)[2] == ""


def test_zero_comments_is_blank(cache_dir):
    wt = _wt(branch="khivi/zero")
    cache_mod.branch_cache("pr-comments", wt.branch).write_text("0")
    assert _plain(wt)[5] == ""


@pytest.mark.parametrize(
    "unaddressed,total,expected",
    [
        ("", "", ""),  # no PR / no threads → blank
        ("0", "5", ""),  # all addressed → blank (column = "needs attention")
        ("2", "", "2"),  # total cell empty → bare count
        ("2", "0", "2"),  # total zero → bare count
        ("2", "2", "2"),  # every thread fresh → denominator adds nothing
        ("2", "5", "2/5"),  # some addressed → ratio
        ("3", "2", "3"),  # total < unaddressed (stale) → bare count, no ratio
        ("bad", "5", ""),  # unparsable → blank, never raises
    ],
)
def test_comments_cell_ratio(unaddressed, total, expected):
    cell = _comments_cell(unaddressed, total)
    assert cell.plain == expected
    if expected:
        assert "red" in str(cell.style)


def test_comments_ratio_through_worktree_cells(cache_dir):
    wt = _wt(branch="khivi/ratio")
    cache_mod.branch_cache("pr-comments", wt.branch).write_text("2")
    cache_mod.branch_cache("pr-comments-total", wt.branch).write_text("5")
    assert _plain(wt)[5] == "2/5"


def test_no_pr_leaves_columns_blank(cache_dir):
    cells = _plain(_wt(branch="khivi/bare"))
    # PR, Author, state-icon all blank with no PR cells seeded.
    assert cells[1] == "" and cells[2] == "" and cells[3] == ""


def test_long_title_truncated(cache_dir):
    wt = _wt(branch="khivi/long")
    cache_mod.branch_cache("pr-title", wt.branch).write_text("x" * 80)
    title = _plain(wt)[7]
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
    cells = worktree_cells(wt, "r", None, True, show_tickets=True)
    ticket, status = cells[3], cells[5]  # Ticket after Author, Status after PR state
    assert ticket.plain == "PE-1"
    assert status.plain == DEVDONE_ICON  # "Dev Done" → 🏁 icon, not text
    assert any("green" in str(s.style) for s in status.spans)  # dev-done → green


@pytest.mark.parametrize(
    "state,icon,style",
    [
        ("Dev Done", DEVDONE_ICON, "green"),  # specific beats bare "done"
        ("Done", "🟢", "green"),
        ("In Review", "🔍", "yellow"),
        ("In Progress", "🚧", "cyan"),
        ("Backlog", "📋", "grey50"),
        ("Todo", "⬜", "grey50"),
        ("Canceled", "🚫", "red"),
        # GitHub-issue states (the `tickets: github` provider's open/closed).
        ("closed", "🟢", "green"),
        ("open", "🚧", "cyan"),
    ],
)
def test_linear_status_icon_mapping(state, icon, style):
    assert _linear_status_icon(state) == (icon, style)


def test_linear_status_icon_unknown_falls_back(cache_dir):
    assert _linear_status_icon("Some Custom Workflow") == _LINEAR_STATUS_FALLBACK


def test_status_cell_one_icon_per_ticket(cache_dir, monkeypatch):
    wt = _wt(branch="khivi/multi")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {
            "linear": {
                "tickets": [
                    {"id": "PE-1", "state": "In Review"},
                    {"id": "PE-2", "state": "Done"},
                ]
            }
        },
    )
    cells = worktree_cells(wt, "r", None, True, show_tickets=True)
    ticket, status = cells[3], cells[5]  # Ticket after Author, Status after PR state
    assert ticket.plain == "PE-1, PE-2"  # ids still comma-joined
    assert status.plain == "🔍 🟢"  # one icon per ticket, space-joined


def test_status_cell_unresolved_state_flags_red(cache_dir, monkeypatch):
    # Provider configured + ticket delivered, but the fetch couldn't resolve a
    # state (state=None, how every provider degrades an unreachable/creds-missing
    # fetch) → red "!", distinct from the neutral ◎ an unmapped real state gets.
    wt = _wt(branch="khivi/down")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {"linear": {"tickets": [{"id": "PE-1", "state": None}]}},
    )
    status = worktree_cells(wt, "r", None, True, show_tickets=True)[5]
    assert status.plain == "!"
    assert any("red" in str(s.style) for s in status.spans)


def test_ticket_status_blank_for_non_linear_repo(cache_dir, monkeypatch):
    wt = _wt(branch="khivi/nl")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {"linear": {"tickets": [{"id": "PE-9", "state": "x"}]}},
    )
    # columns exist (some other repo is Linear) but this row's repo isn't
    cells = worktree_cells(wt, "r", None, False, show_tickets=True)
    assert cells[3].plain == "" and cells[5].plain == ""  # Ticket, Status


def test_no_linear_columns_when_not_configured(cache_dir):
    # show_tickets False → no Ticket/Status cells
    assert len(_plain(_wt(), linear=True, show_tickets=False)) == 8


def test_row_capabilities_pr_muted_ticket(cache_dir, monkeypatch):
    # The footer's per-row gating tokens, read from the same daemon-written cells
    # the cells render from: `pr` (pr-num), `muted` (pr-muted), `ticket`
    # (delivered ticket in the cached block, only when the repo is provider-on).
    wt = _wt(branch="khivi/caps")
    cache_mod.branch_cache("pr-num", wt.branch).write_text("7")
    cache_mod.branch_cache("pr-muted", wt.branch).write_text("muted")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {"linear": {"tickets": [{"id": "#42", "state": "open"}]}},
    )
    assert row_capabilities(wt, "r", True) == frozenset({"pr", "muted", "ticket"})
    # tickets disabled for this repo → no ticket token even with a cached block
    assert row_capabilities(wt, "r", False) == frozenset({"pr", "muted"})


def test_row_capabilities_empty_without_pr(cache_dir, monkeypatch):
    wt = _wt(branch="khivi/bare")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: None,
    )
    assert row_capabilities(wt, "r", True) == frozenset()


def test_row_capabilities_workspace_and_primary(cache_dir, monkeypatch):
    # `workspace` reflects live state passed in by the app; `primary` marks the
    # repo's primary checkout (an in_place `master`), read off the Worktree.
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: None,
    )
    wt = _wt(branch="khivi/live")
    assert row_capabilities(wt, "r", False) == frozenset()
    assert row_capabilities(wt, "r", False, has_workspace=True) == frozenset(
        {"workspace"}
    )
    primary = _wt(branch="master", is_primary=True)
    assert row_capabilities(primary, "r", False) == frozenset({"primary"})
    assert row_capabilities(primary, "r", False, has_workspace=True) == frozenset(
        {"primary", "workspace"}
    )


def test_muted_pr_prefixes_workspace_glyph(cache_dir):
    wt = _wt(branch="khivi/silence", branch_prefix="khivi/")
    cache_mod.branch_cache("pr-muted", wt.branch).write_text("muted")
    cell = worktree_cells(wt, "r", None, False, show_tickets=False)[0]
    assert cell.plain == f"{ICON_PR_MUTED} silence"


def test_unmuted_pr_has_no_glyph(cache_dir):
    wt = _wt(branch="khivi/loud", branch_prefix="khivi/")
    cell = worktree_cells(wt, "r", None, False, show_tickets=False)[0]
    assert cell.plain == "loud"


def test_nudge_pr_prefixes_bell_glyph(cache_dir):
    """An actionable, unmuted PR (the `pr-nudge` cell holds its issue category)
    prefixes the workspace name with 🔔."""
    wt = _wt(branch="khivi/ringing", branch_prefix="khivi/")
    cache_mod.branch_cache("pr-nudge", wt.branch).write_text("ci")
    cell = worktree_cells(wt, "r", None, False, show_tickets=False)[0]
    assert cell.plain == f"{ICON_PR_NUDGE} ringing"


def test_mute_wins_over_nudge_glyph(cache_dir):
    """A muted PR fires no nudge, so the mute glyph wins even when the daemon
    still wrote a `pr-nudge` value (mute is orthogonal to the issue state)."""
    wt = _wt(branch="khivi/quiet", branch_prefix="khivi/")
    cache_mod.branch_cache("pr-muted", wt.branch).write_text("muted")
    cache_mod.branch_cache("pr-nudge", wt.branch).write_text("comments")
    cell = worktree_cells(wt, "r", None, False, show_tickets=False)[0]
    assert cell.plain == f"{ICON_PR_MUTED} quiet"


def test_empty_nudge_cell_has_no_glyph(cache_dir):
    """A blank `pr-nudge` cell (no actionable issue) shows no bell."""
    wt = _wt(branch="khivi/calm", branch_prefix="khivi/")
    cache_mod.branch_cache("pr-nudge", wt.branch).write_text("")
    cell = worktree_cells(wt, "r", None, False, show_tickets=False)[0]
    assert cell.plain == "calm"


def test_dirty_column_renders_counts(cache_dir):
    wt = _wt(path="/tmp/dirtywt", branch="khivi/dirty")
    cache_mod.cwd_cache("git-status", wt.path).write_text("1 2 3")
    dirty = worktree_cells(wt, "r", None, False, show_tickets=False)[6]
    # ●1 ✎2 ✚3 with the footer's glyphs
    assert dirty.plain == "●1 ✎2 ✚3"
    assert "green" in str(dirty.spans[0].style)  # staged
    assert "yellow" in str(dirty.spans[1].style)  # unstaged


def test_dirty_column_omits_zero_segments(cache_dir):
    wt = _wt(path="/tmp/partialdirty", branch="khivi/partial")
    cache_mod.cwd_cache("git-status", wt.path).write_text("0 0 4")
    dirty = worktree_cells(wt, "r", None, False, show_tickets=False)[6]
    assert dirty.plain == "✚4"  # only untracked shown


def test_dirty_column_blank_when_clean(cache_dir):
    wt = _wt(path="/tmp/cleanwt", branch="khivi/clean")
    cache_mod.cwd_cache("git-status", wt.path).write_text("0 0 0")
    dirty = worktree_cells(wt, "r", None, False, show_tickets=False)[6]
    assert dirty.plain == ""


def test_dirty_column_blank_when_cell_missing(cache_dir):
    # Cold start: daemon hasn't written the git-status cell yet.
    wt = _wt(path="/tmp/coldwt", branch="khivi/cold")
    dirty = worktree_cells(wt, "r", None, False, show_tickets=False)[6]
    assert dirty.plain == ""


def test_label_stays_bare_across_repos(cache_dir):
    # Same-named worktrees in different repos render bare — the group-header row
    # disambiguates them, not a `repo/` prefix.
    wt = _wt(branch="master")
    assert worktree_cells(wt, "Cockpit", None, False, show_tickets=False)[0].plain == (
        "master"
    )
    assert worktree_cells(wt, "dotfiles", None, False, show_tickets=False)[0].plain == (
        "master"
    )


def test_header_cells_repo_name_and_blank_tail():
    ncols = len(column_labels(show_tickets=False))
    cells = _header_cells("Cockpit", None, ncols)
    assert len(cells) == ncols
    assert cells[0].plain == "▸ Cockpit"
    assert "bold" in str(cells[0].style)
    assert all(c.plain == "" for c in cells[1:])


def test_header_cells_tinted_by_repo_color():
    ncols = len(column_labels(show_tickets=False))
    tinted = _header_cells("Cockpit", "Blue", ncols)[0]
    plain = _header_cells("Cockpit", None, ncols)[0]
    assert tinted.plain == plain.plain == "▸ Cockpit"
    assert tinted.spans  # colorizer ANSI → colour spans
    assert not plain.spans


def test_header_key_prefix_is_nul_led():
    # The sentinel must never collide with a real worktree path key.
    assert HEADER_KEY_PREFIX.startswith("\x00")


class _Host(App[None]):
    def compose(self) -> ComposeResult:
        yield WorktreeTable(id="table")


@pytest.mark.asyncio
async def test_cursor_skips_past_consecutive_header_rows(cache_dir):
    # Regression: with rows [header(A), header(B), wt] the cursor auto-skip
    # only advanced one row off a header, landing on header(B) instead of the
    # worktree row below it — a header row hides every row-targeted footer key.
    wt = _wt(path="/tmp/consecutive-headers-wt", branch="khivi/feat-x")
    app = _Host()
    async with app.run_test() as pilot:
        table = app.query_one(WorktreeTable)
        table.update_inventory(
            [
                ("A", None, False, []),  # empty repo -> header row only
                ("B", None, False, [wt]),  # header row followed by one worktree
            ]
        )
        await pilot.pause()
        assert table.current_path() == str(wt.path)
