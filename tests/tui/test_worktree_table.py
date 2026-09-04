"""Tests for the worktree table cells (cockpit/tui/widgets/worktree_table.py).

`worktree_cells` is a pure function — no Textual. Seeds the same flat cache
cells the daemon writes, then asserts the per-column Rich Text. Columns are
grouped by domain: Workspace | PR | ✎ | 📝 | 🔀 | CI | comments | (Ticket) |
(Status) | Author | Title — the local cluster (Dirty ✎, then 📝 pending
diff-viewer comments) sits right after PR, then the GitHub cluster
(🔀/CI/comments), then the ticket cluster (Ticket/Status, present only when
some repo has a ticket provider), then the rarely-populated Author (blank for
self-authored PRs, the coworker login for a review PR), then Title. Tests index
columns by label via `_col(...)` so a reorder doesn't touch every assertion. The
repo is conveyed by a group-header row plus a tint on the workspace name (not a
column). The Dirty column (icon header) reads the per-cwd `git-status` cell
(`"<staged> <unstaged> <untracked>"`); 📝 reads `diff-comments`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from rich.cells import cell_len
from textual.app import App, ComposeResult

import cockpit.lib.cache as cache_mod
from cockpit.lib.git import Worktree
from cockpit.tui.widgets.worktree_table import (
    _APPROVAL_ICON,
    _DIFF_COMMENT_ICON,
    _DIRTY_ICON,
    _HEADER_TOOLTIPS,
    _LABEL_MAX,
    _LINEAR_STATUS_FALLBACK,
    _PR_STATE_ICON,
    _RULE_WIDTH,
    _STATUS_ICON,
    _STATUS_SLOT,
    _TICKET_MAX,
    DEVDONE_ICON,
    FOLD_CAP,
    HEADER_KEY_PREFIX,
    ICON_PR_MUTED,
    ICON_PR_NUDGE,
    ROW_INDENT,
    SNOOZED_CAP,
    WorktreeTable,
    _comments_cell,
    _header_cells,
    _hidden_cells,
    _linear_status_icon,
    _snoozed_cells,
    _split_snoozed,
    _stack_rows,
    column_labels,
    row_capabilities,
    row_tooltips,
    snoozed_row_key,
    worktree_cells,
)

# Root every test's worktree paths under its own `tmp_path` (set by the autouse
# fixture below) rather than a shared `/tmp`. Nothing here is created on disk —
# a `Worktree.path` is only a row key and a `.resolve()` target — but a literal
# `/tmp/mine` is shared mutable state across the xdist workers the suite runs
# under, so it is one `open()` away from a real collision.
_WT_ROOT = Path("/tmp")


@pytest.fixture(autouse=True)
def _wt_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_WT_ROOT", tmp_path)


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    cdir = tmp_path / "cockpit-cache"
    cdir.mkdir()
    monkeypatch.setattr(cache_mod, "FLAT_CACHE_DIR", cdir)
    return cdir


def _wt(path="feat", branch="khivi/feat-x", **kw):
    """A Worktree whose `path` is `_WT_ROOT / path` — pass a bare name, not an
    absolute path, so it lands under the running test's own tmp dir."""
    return Worktree(path=_WT_ROOT / path, branch=branch, **kw)


def _plain(wt, repo="repo", color=None, provider="none", show_tickets=False):
    cells = worktree_cells(wt, repo, color, provider, show_tickets=show_tickets)
    return [c.plain for c in cells]


def _ws(label, *, depth=0, glyph=""):
    """The Workspace cell's expected plain text for `label` — every worktree row
    hangs under its repo header behind `ROW_INDENT`, then pays `_STATUS_SLOT`
    cells for its status glyph whether or not it has one. Kept in one place so a
    change to the indent or the slot doesn't churn every assertion here."""
    slot = glyph + " " * (_STATUS_SLOT - cell_len(glyph))
    return f"{ROW_INDENT}{'  └ ' if depth else ''}{slot}{label}"


def _col(label, *, show_tickets=False):
    """Column index of `label` in the current layout, so assertions stay off
    magic numbers — a reorder then touches only `column_labels`, not every test.
    Icon columns are looked up by their glyph constant (`_APPROVAL_ICON`, …)."""
    return column_labels(show_tickets=show_tickets).index(label)


def test_cell_count_matches_columns(cache_dir):
    cols = column_labels(show_tickets=False)
    assert len(_plain(_wt())) == len(cols) == 9
    # Local cluster (dirty / pending diff comments) sits next to Workspace,
    # then the GitHub cluster (PR / state / CI / comments); Author is parked
    # near the end (rarely populated), just before Title.
    assert cols == (
        "Workspace",
        "PR",
        _DIRTY_ICON,
        _DIFF_COMMENT_ICON,
        _APPROVAL_ICON,
        "CI",
        "💬",
        "Author",
        "Title",
    )
    # Ticket + Status columns added only when show_tickets, as one adjacent
    # cluster right after the GitHub cluster (Ticket then its Status icon).
    lin = column_labels(show_tickets=True)
    assert len(_plain(_wt(), show_tickets=True)) == len(lin) == 11
    assert lin == (
        "Workspace",
        "PR",
        _DIRTY_ICON,
        _DIFF_COMMENT_ICON,
        _APPROVAL_ICON,
        "CI",
        "💬",
        "Ticket",
        _STATUS_ICON,
        "Author",
        "Title",
    )


def test_workspace_label_strips_prefix(cache_dir):
    # branch_prefix is threaded onto the Worktree from repo config in production.
    wt = _wt(branch="khivi/my-feature", branch_prefix="khivi/")
    assert _plain(wt)[0] == _ws("my-feature")


def test_workspace_tinted_by_repo_color(cache_dir):
    wt = _wt(branch="khivi/c", branch_prefix="khivi/")
    colored = worktree_cells(wt, "r", "Blue", "none", show_tickets=False)[0]
    plain = worktree_cells(wt, "r", None, "none", show_tickets=False)[0]
    assert colored.plain == _ws("c") == plain.plain
    assert colored.spans  # Text.from_ansi(colorizer(...)) → colour spans
    # Untinted path carries no colour — only the bold span over the label
    # (`ROW_INDENT` is prepended unstyled, so the style is a span, not the
    # whole-Text style).
    assert [str(sp.style) for sp in plain.spans] == ["bold"]


def test_unknown_color_falls_back_to_plain(cache_dir):
    cell = worktree_cells(_wt(), "r", "NotAColor", "none", show_tickets=False)[0]
    assert [str(sp.style) for sp in cell.spans] == ["bold"]


def test_pr_columns_with_state_icon(cache_dir):
    wt = _wt(branch="khivi/feat-pr")
    cache_mod.cwd_cache("pr-num", wt.path).write_text("123")
    cache_mod.cwd_cache("pr-state", wt.path).write_text("APPROVED")
    cache_mod.cwd_cache("pr-checks", wt.path).write_text("✓")
    cache_mod.cwd_cache("pr-comments", wt.path).write_text("2")
    cache_mod.cwd_cache("pr-title", wt.path).write_text("Add the thing")
    cells = _plain(wt)
    assert cells[_col("PR")] == "#123"
    assert cells[_col("Author")] == ""  # self-authored → no author shown
    assert cells[_col(_APPROVAL_ICON)] == _PR_STATE_ICON["APPROVED"]  # icon, not text
    assert cells[_col("CI")] == "✓"
    assert cells[_col("💬")] == "2"
    assert cells[_col(_DIRTY_ICON)] == ""  # clean tree (no git-status cell)
    assert cells[_col("Title")] == "Add the thing"


@pytest.mark.parametrize(
    "raw", ["DRAFT", "REVIEW_REQUIRED", "CHANGES_REQUESTED", "MERGED"]
)
def test_approval_state_icons(cache_dir, raw):
    wt = _wt(branch=f"khivi/{raw.lower()}")
    cache_mod.cwd_cache("pr-state", wt.path).write_text(raw)
    assert _plain(wt)[_col(_APPROVAL_ICON)] == _PR_STATE_ICON[raw]


def test_changes_requested_colored_red(cache_dir):
    wt = _wt(branch="khivi/cr")
    cache_mod.cwd_cache("pr-state", wt.path).write_text("CHANGES_REQUESTED")
    cell = worktree_cells(wt, "r", None, "none", show_tickets=False)[
        _col(_APPROVAL_ICON)
    ]
    assert cell.plain == _PR_STATE_ICON["CHANGES_REQUESTED"]
    assert "red" in str(cell.style)


def test_author_column_shows_coworker_login(cache_dir):
    # The daemon writes `pr-author` only for other-authored PRs; the table
    # renders it `@login`.
    wt = _wt(branch="coworker/feat")
    cache_mod.cwd_cache("pr-author", wt.path).write_text("octocat")
    cell = worktree_cells(wt, "r", None, "none", show_tickets=False)[_col("Author")]
    assert cell.plain == "@octocat"
    assert "cyan" in str(cell.style)


def test_author_column_blank_for_self_authored(cache_dir):
    # Self-authored PR → daemon leaves `pr-author` empty → blank Author cell.
    wt = _wt(branch="khivi/mine")
    assert _plain(wt)[_col("Author")] == ""


def test_zero_comments_is_blank(cache_dir):
    wt = _wt(branch="khivi/zero")
    cache_mod.cwd_cache("pr-comments", wt.path).write_text("0")
    assert _plain(wt)[_col("💬")] == ""


def test_all_addressed_shows_green_ratio(cache_dir):
    wt = _wt(branch="khivi/addressed")
    cache_mod.cwd_cache("pr-comments", wt.path).write_text("0")
    cache_mod.cwd_cache("pr-comments-total", wt.path).write_text("7")
    assert _plain(wt)[_col("💬")] == "0/7"
    assert "green" in str(_comments_cell("0", "7").style)


@pytest.mark.parametrize(
    "unaddressed,total,expected",
    [
        ("", "", ""),  # no PR / no threads → blank
        ("0", "5", "0/5"),  # all addressed → green ratio, not blank
        ("0", "0", ""),  # no threads at all → blank
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
        expected_style = "green" if expected.startswith("0/") else "red"
        assert expected_style in str(cell.style)


def test_comments_ratio_through_worktree_cells(cache_dir):
    wt = _wt(branch="khivi/ratio")
    cache_mod.cwd_cache("pr-comments", wt.path).write_text("2")
    cache_mod.cwd_cache("pr-comments-total", wt.path).write_text("5")
    assert _plain(wt)[_col("💬")] == "2/5"


def test_no_pr_leaves_columns_blank(cache_dir):
    cells = _plain(_wt(branch="khivi/bare"))
    # PR, Author, state-icon all blank with no PR cells seeded.
    assert (
        cells[_col("PR")] == ""
        and cells[_col("Author")] == ""
        and cells[_col(_APPROVAL_ICON)] == ""
    )


def test_long_title_truncated(cache_dir):
    wt = _wt(branch="khivi/long")
    cache_mod.cwd_cache("pr-title", wt.path).write_text("x" * 80)
    title = _plain(wt)[_col("Title")]
    assert title.endswith("…")
    assert len(title) <= 49


def test_ticket_and_status_columns_when_enabled(cache_dir, monkeypatch):
    wt = _wt(branch="khivi/lin")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {
            "ticket": {"tickets": [{"id": "PE-1", "state": "Dev Done"}]}
        },
    )
    cells = worktree_cells(wt, "r", None, "linear", show_tickets=True)
    ticket = cells[_col("Ticket", show_tickets=True)]
    status = cells[_col(_STATUS_ICON, show_tickets=True)]
    assert ticket.plain == "PE-1"
    assert status.plain == DEVDONE_ICON  # "Dev Done" → 🏁 icon, not text
    assert any("green" in str(s.style) for s in status.spans)  # dev-done → green


def test_ticket_cell_trello_shows_card_number(cache_dir, monkeypatch):
    # Trello ids are opaque short links (e.g. VfqsfqUd) — the Ticket cell shows
    # the cached card number instead. Other providers keep their meaningful id.
    wt = _wt(branch="khivi/tr")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {
            "ticket": {
                "tickets": [{"id": "VfqsfqUd", "state": "Done", "title": "#122"}]
            }
        },
    )
    ticket = worktree_cells(wt, "r", None, "trello", show_tickets=True)[
        _col("Ticket", show_tickets=True)
    ]
    assert ticket.plain == "#122"


def test_ticket_cell_trello_falls_back_to_id_without_title(cache_dir, monkeypatch):
    wt = _wt(branch="khivi/tr2")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {
            "ticket": {"tickets": [{"id": "VfqsfqUd", "state": "Done"}]}
        },
    )
    ticket = worktree_cells(wt, "r", None, "trello", show_tickets=True)[
        _col("Ticket", show_tickets=True)
    ]
    assert ticket.plain == "VfqsfqUd"


def test_ticket_cell_non_trello_keeps_id_despite_title(cache_dir, monkeypatch):
    # A Linear ticket carries a title in the cache too, but its id is meaningful,
    # so the Ticket cell must still render the id — not the title.
    wt = _wt(branch="khivi/lin2")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {
            "ticket": {
                "tickets": [{"id": "PE-1", "state": "Done", "title": "Fix login"}]
            }
        },
    )
    ticket = worktree_cells(wt, "r", None, "linear", show_tickets=True)[
        _col("Ticket", show_tickets=True)
    ]
    assert ticket.plain == "PE-1"


# ── Cell hyperlinks (OSC 8) ─────────────────────────────────────────────────
# Every cell naming something on the web carries a terminal hyperlink, so the
# terminal (not cockpit) owns the click. The link is a Rich `link <url>` span,
# which Textual passes straight through to the terminal — see
# `_apply_links`. These assert the *mapping*: which column points where, and
# which deliberately point nowhere.

_PR_URL = "https://github.com/khivi/cockpit/pull/435"
_TICKET_URL = "https://linear.app/x/issue/PE-1/foo"


def _links(cell):
    """The URLs `cell` links to, from its Rich `link <url>` spans."""
    return {
        str(s.style).split("link ", 1)[1]
        for s in cell.spans
        if isinstance(s.style, str) and "link " in s.style
    }


def _linked_row(monkeypatch, cache_dir, *, payload=None, **cells):
    """A fully-populated row's cells, keyed by column label."""
    wt = _wt(branch="khivi/linked")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: payload
        if payload is not None
        else {
            "url": _PR_URL,
            "ticket": {
                "tickets": [{"id": "PE-1", "state": "Dev Done", "url": _TICKET_URL}]
            },
        },
    )
    seed = {
        "pr-num": "435",
        "pr-state": "OPEN",
        "pr-checks": "✗",
        "pr-comments": "2",
        "pr-title": "Make cells clickable",
        "pr-author": "kim",
        **cells,
    }
    for stem, value in seed.items():
        cache_mod.cwd_cache(stem, wt.path).write_text(value)
    labels = column_labels(show_tickets=True)
    built = worktree_cells(wt, "r", None, "linear", show_tickets=True)
    return wt, dict(zip(labels, built, strict=False))


@pytest.mark.parametrize(
    "label,url",
    [
        ("PR", _PR_URL),
        (_APPROVAL_ICON, _PR_URL),
        ("💬", _PR_URL),
        ("Title", _PR_URL),
        # CI is the one GitHub cell that does NOT point at the PR: a red ✗ is
        # the glyph you click *through*, so it lands on the checks page.
        ("CI", f"{_PR_URL}/checks"),
        ("Ticket", _TICKET_URL),
        (_STATUS_ICON, _TICKET_URL),
        ("Author", "https://github.com/kim"),
    ],
)
def test_cell_links_to_what_it_names(cache_dir, monkeypatch, label, url):
    _, cells = _linked_row(monkeypatch, cache_dir)
    assert _links(cells[label]) == {url}


@pytest.mark.parametrize("label", ["Workspace", _DIRTY_ICON, _DIFF_COMMENT_ICON])
def test_local_columns_carry_no_link(cache_dir, monkeypatch, label):
    """Workspace's gesture is already double-click → focus, the dirty count
    names nothing outside this machine, and diff-viewer comments are local to
    cmux — they never reach GitHub, so there's nothing to link to."""
    _, cells = _linked_row(monkeypatch, cache_dir)
    assert _links(cells[label]) == set()


def test_a_link_keeps_the_cell_s_own_colour(cache_dir, monkeypatch):
    """`stylize` adds a span rather than replacing the style, so a failing CI
    cell stays red *and* becomes a link. Losing the colour would trade the
    signal for the affordance."""
    _, cells = _linked_row(monkeypatch, cache_dir)
    ci = cells["CI"]
    assert _links(ci) == {f"{_PR_URL}/checks"}
    assert "red" in str(ci.style)  # the base style the cell was built with


def test_blank_cells_are_never_linked(cache_dir, monkeypatch):
    """A hyperlink over blank padding is a click target with nothing in it —
    and the columns most often empty (Author, CI, comments) sit right beside
    ones that aren't."""
    _, cells = _linked_row(
        monkeypatch, cache_dir, **{"pr-author": "", "pr-checks": "", "pr-comments": ""}
    )
    for label in ("Author", "CI", "💬"):
        assert cells[label].plain == ""
        assert _links(cells[label]) == set()


def test_no_pr_no_links(cache_dir, monkeypatch):
    """With no cached PR there is nothing to point at — not even a stale one.

    `Author` is the exception, and deliberately so: a login is a GitHub profile
    on its own, with no PR needed to resolve it."""
    _, cells = _linked_row(monkeypatch, cache_dir, payload={})
    linked = {label for label, c in cells.items() if _links(c)}
    assert linked == {"Author"}


def test_ticket_cell_unlinked_when_the_daemon_resolved_no_url(cache_dir, monkeypatch):
    """A block written before `url` existed (or one whose footer carries no
    link) renders the ticket plainly rather than guessing a URL — resolving one
    here would mean a `gh` call from a renderer."""
    _, cells = _linked_row(
        monkeypatch,
        cache_dir,
        payload={"url": _PR_URL, "ticket": {"tickets": [{"id": "PE-1"}]}},
    )
    assert cells["Ticket"].plain == "PE-1"
    assert _links(cells["Ticket"]) == set()
    assert _links(cells["PR"]) == {_PR_URL}  # the PR half is unaffected


def test_tooltip_names_the_link_destination(cache_dir, monkeypatch):
    """An OSC 8 link is invisible until hovered with a modifier down, so the
    hover text is where a cell admits it goes somewhere."""
    wt, _ = _linked_row(monkeypatch, cache_dir)
    tips = dict(
        zip(
            column_labels(show_tickets=True),
            row_tooltips(wt, "r", "linear", show_tickets=True),
            strict=False,
        )
    )
    assert tips["PR"] == _PR_URL  # had no hint of its own
    assert tips["CI"] == f"CI failing — {_PR_URL}/checks"  # decoded value kept
    assert tips[_DIRTY_ICON] is None  # unlinked columns unchanged


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
        # A Trello *list* name — the state is whatever the board calls it, and
        # "Ongoing" is a common spelling of in-progress that none of the Linear
        # needles ("progress"/"doing"/"started") catches.
        ("Ongoing", "🚧", "cyan"),
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
            "ticket": {
                "tickets": [
                    {"id": "PE-1", "state": "In Review"},
                    {"id": "PE-2", "state": "Done"},
                ]
            }
        },
    )
    cells = worktree_cells(wt, "r", None, "linear", show_tickets=True)
    ticket = cells[_col("Ticket", show_tickets=True)]
    status = cells[_col(_STATUS_ICON, show_tickets=True)]
    assert ticket.plain == "PE-1, PE-2"  # ids still comma-joined
    assert status.plain == "🔍 🟢"  # one icon per ticket, space-joined


def test_status_cell_unresolved_state_flags_red(cache_dir, monkeypatch):
    # Provider configured + ticket delivered, but the fetch couldn't resolve a
    # state (state=None, how every provider degrades an unreachable/creds-missing
    # fetch) → red "!", distinct from the neutral ◎ an unmapped real state gets.
    wt = _wt(branch="khivi/down")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {"ticket": {"tickets": [{"id": "PE-1", "state": None}]}},
    )
    status = worktree_cells(wt, "r", None, "linear", show_tickets=True)[
        _col(_STATUS_ICON, show_tickets=True)
    ]
    assert status.plain == "!"
    assert any("red" in str(s.style) for s in status.spans)


def test_ticket_status_blank_for_non_linear_repo(cache_dir, monkeypatch):
    wt = _wt(branch="khivi/nl")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {"ticket": {"tickets": [{"id": "PE-9", "state": "x"}]}},
    )
    # columns exist (some other repo is Linear) but this row's repo isn't
    cells = worktree_cells(wt, "r", None, "none", show_tickets=True)
    assert (
        cells[_col("Ticket", show_tickets=True)].plain == ""
        and cells[_col(_STATUS_ICON, show_tickets=True)].plain == ""
    )


def test_no_linear_columns_when_not_configured(cache_dir):
    # show_tickets False → no Ticket/Status cells
    assert len(_plain(_wt(), provider="linear", show_tickets=False)) == 9


def test_row_capabilities_pr_muted_ticket(cache_dir, monkeypatch):
    # The footer's per-row gating tokens, read from the same daemon-written cells
    # the cells render from: `pr` (pr-num), `muted` (pr-muted), `ticket`
    # (delivered ticket in the cached block, only when the repo is provider-on).
    wt = _wt(branch="khivi/caps")
    cache_mod.cwd_cache("pr-num", wt.path).write_text("7")
    cache_mod.cwd_cache("pr-muted", wt.path).write_text("muted")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {"ticket": {"tickets": [{"id": "#42", "state": "open"}]}},
    )
    assert row_capabilities(wt, "r", "linear") == frozenset({"pr", "muted", "ticket"})
    # tickets disabled for this repo → no ticket token even with a cached block
    assert row_capabilities(wt, "r", "none") == frozenset({"pr", "muted"})


def test_row_capabilities_empty_without_pr(cache_dir, monkeypatch):
    wt = _wt(branch="khivi/bare")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: None,
    )
    assert row_capabilities(wt, "r", "linear") == frozenset()


def test_row_capabilities_workspace_and_primary(cache_dir, monkeypatch):
    # `workspace` reflects live state passed in by the app; `primary` marks the
    # repo's primary checkout (a `use_worktree: false` `master`), read off the Worktree.
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: None,
    )
    wt = _wt(branch="khivi/live")
    assert row_capabilities(wt, "r", "none") == frozenset()
    assert row_capabilities(wt, "r", "none", has_workspace=True) == frozenset(
        {"workspace"}
    )
    primary = _wt(branch="master", is_primary=True)
    assert row_capabilities(primary, "r", "none") == frozenset({"primary"})
    assert row_capabilities(primary, "r", "none", has_workspace=True) == frozenset(
        {"primary", "workspace"}
    )


def test_row_capabilities_primary_on_feature_branch_not_primary(cache_dir, monkeypatch):
    # A `use_worktree: false` primary checkout parked on a *feature* branch is a
    # branch teardown, not a workspace-only close, so it does NOT get the
    # "primary" cap — that keeps `c`/`C` advertised even with no workspace.
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: None,
    )
    feature_primary = _wt(branch="khivi/feat-x", is_primary=True)
    assert row_capabilities(feature_primary, "r", "none") == frozenset()


def test_muted_pr_prefixes_workspace_glyph(cache_dir):
    wt = _wt(branch="khivi/silence", branch_prefix="khivi/")
    cache_mod.cwd_cache("pr-muted", wt.path).write_text("muted")
    cell = worktree_cells(wt, "r", None, "none", show_tickets=False)[0]
    assert cell.plain == _ws("silence", glyph=ICON_PR_MUTED)


def test_unmuted_pr_has_no_glyph(cache_dir):
    wt = _wt(branch="khivi/loud", branch_prefix="khivi/")
    cell = worktree_cells(wt, "r", None, "none", show_tickets=False)[0]
    assert cell.plain == _ws("loud")


def test_nudge_pr_prefixes_bell_glyph(cache_dir):
    """An actionable, unmuted PR (the `pr-nudge` cell holds its issue category)
    prefixes the workspace name with 🔔."""
    wt = _wt(branch="khivi/ringing", branch_prefix="khivi/")
    cache_mod.cwd_cache("pr-nudge", wt.path).write_text("ci")
    cell = worktree_cells(wt, "r", None, "none", show_tickets=False)[0]
    assert cell.plain == _ws("ringing", glyph=ICON_PR_NUDGE)


def test_mute_wins_over_nudge_glyph(cache_dir):
    """A muted PR fires no nudge, so the mute glyph wins even when the daemon
    still wrote a `pr-nudge` value (mute is orthogonal to the issue state)."""
    wt = _wt(branch="khivi/quiet", branch_prefix="khivi/")
    cache_mod.cwd_cache("pr-muted", wt.path).write_text("muted")
    cache_mod.cwd_cache("pr-nudge", wt.path).write_text("comments")
    cell = worktree_cells(wt, "r", None, "none", show_tickets=False)[0]
    assert cell.plain == _ws("quiet", glyph=ICON_PR_MUTED)


def test_empty_nudge_cell_has_no_glyph(cache_dir):
    """A blank `pr-nudge` cell (no actionable issue) shows no bell."""
    wt = _wt(branch="khivi/calm", branch_prefix="khivi/")
    cache_mod.cwd_cache("pr-nudge", wt.path).write_text("")
    cell = worktree_cells(wt, "r", None, "none", show_tickets=False)[0]
    assert cell.plain == _ws("calm")


def test_dirty_column_renders_counts(cache_dir):
    wt = _wt(path="dirtywt", branch="khivi/dirty")
    cache_mod.cwd_cache("git-status", wt.path).write_text("1 2 3")
    dirty = worktree_cells(wt, "r", None, "none", show_tickets=False)[_col(_DIRTY_ICON)]
    # ●1 ✎2 ✚3 with the footer's glyphs
    assert dirty.plain == "●1 ✎2 ✚3"
    assert "green" in str(dirty.spans[0].style)  # staged
    assert "yellow" in str(dirty.spans[1].style)  # unstaged


def test_dirty_column_omits_zero_segments(cache_dir):
    wt = _wt(path="partialdirty", branch="khivi/partial")
    cache_mod.cwd_cache("git-status", wt.path).write_text("0 0 4")
    dirty = worktree_cells(wt, "r", None, "none", show_tickets=False)[_col(_DIRTY_ICON)]
    assert dirty.plain == "✚4"  # only untracked shown


def test_dirty_column_blank_when_clean(cache_dir):
    wt = _wt(path="cleanwt", branch="khivi/clean")
    cache_mod.cwd_cache("git-status", wt.path).write_text("0 0 0")
    dirty = worktree_cells(wt, "r", None, "none", show_tickets=False)[_col(_DIRTY_ICON)]
    assert dirty.plain == ""


def test_dirty_column_blank_when_cell_missing(cache_dir):
    # Cold start: daemon hasn't written the git-status cell yet.
    wt = _wt(path="coldwt", branch="khivi/cold")
    dirty = worktree_cells(wt, "r", None, "none", show_tickets=False)[_col(_DIRTY_ICON)]
    assert dirty.plain == ""


def test_label_stays_bare_across_repos(cache_dir):
    # Same-named worktrees in different repos render bare — the group-header row
    # disambiguates them, not a `repo/` prefix.
    wt = _wt(branch="master")
    assert worktree_cells(wt, "Cockpit", None, "none", show_tickets=False)[
        0
    ].plain == _ws("master")
    assert worktree_cells(wt, "dotfiles", None, "none", show_tickets=False)[
        0
    ].plain == _ws("master")


# ── column caps (one long value must not starve the trailing Title column) ──


def test_long_workspace_label_is_ellipsized(cache_dir):
    # `branch_label` already caps at 30, so the untruncated label is `wt.label`,
    # not the raw branch — the cap here trims that further to keep the column
    # deterministic (and guards the uncapped `wt.short` dir-basename fallback).
    wt = _wt(branch="khivi/" + "x" * 40, branch_prefix="khivi/")
    assert len(wt.label) > _LABEL_MAX + 1
    assert _plain(wt)[0] == _ws(wt.label[:_LABEL_MAX] + "…")


def test_label_one_over_the_cap_passes_through_whole(cache_dir):
    # Swapping a single character for an ellipsis saves no width and loses
    # information, so the cap is deliberately soft by one.
    exact = "x" * (_LABEL_MAX + 1)
    wt = _wt(branch=f"khivi/{exact}", branch_prefix="khivi/")
    assert wt.label == exact  # under `branch_label`'s own 30-char cap
    assert _plain(wt)[0] == _ws(exact)


def test_truncated_label_stays_readable_on_the_tooltip(cache_dir):
    wt = _wt(branch="khivi/" + "y" * 40, branch_prefix="khivi/")
    tips = row_tooltips(wt, "r", "none", show_tickets=False)
    assert tips[_col("Workspace")] == wt.label


def test_truncated_label_tooltip_keeps_the_glyph_decode(cache_dir):
    # The mute/nudge decode and the full name share one cell, so the tooltip has
    # to carry both rather than one silently replacing the other.
    wt = _wt(branch="khivi/" + "z" * 40, branch_prefix="khivi/")
    cache_mod.cwd_cache("pr-muted", wt.path).write_text("muted")
    tip = row_tooltips(wt, "r", "none", show_tickets=False)[_col("Workspace")]
    assert tip == f"{wt.label} — Nudges muted"


def test_short_label_leaves_the_tooltip_to_the_column_meaning(cache_dir):
    # Untruncated and unglyphed → None, so hovering falls back to the column
    # meaning instead of echoing the text already on screen.
    wt = _wt(branch="khivi/short", branch_prefix="khivi/")
    assert row_tooltips(wt, "r", "none", show_tickets=False)[_col("Workspace")] is None


def test_long_ticket_is_ellipsized_with_the_full_text_on_hover(cache_dir, monkeypatch):
    # A Trello card whose number didn't resolve falls back to its *name*, which
    # runs long.
    title = "Fix the analytics errors on the checkout page"
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {
            "ticket": {"tickets": [{"id": "abc123", "title": title, "state": "Doing"}]}
        },
    )
    wt = _wt(branch="khivi/card")
    cells = worktree_cells(wt, "r", None, "trello", show_tickets=True)
    assert cells[_col("Ticket", show_tickets=True)].plain == (title[:_TICKET_MAX] + "…")
    tips = row_tooltips(wt, "r", "trello", show_tickets=True)
    assert tips[_col("Ticket", show_tickets=True)] == title


def test_a_typical_widest_row_fits_the_header_rule(cache_dir):
    """`_RULE_WIDTH` covers the typical widest row — a max-length label in an
    unstacked row — so the rule reaches the column edge instead of dangling
    short. The status slot is paid whether or not the row carries a glyph, so a
    belled row is exactly as wide as a quiet one. Holds the arithmetic behind the
    constants: nudge one and this fails."""
    assert cell_len(_header_cells("R", None, 8)[0].plain) == _RULE_WIDTH
    belled = _wt(branch="khivi/" + "w" * 40, branch_prefix="khivi/")
    cache_mod.cwd_cache("pr-nudge", belled.path).write_text("ci")
    belled_cell = worktree_cells(belled, "r", None, "none", show_tickets=False)[0]
    quiet = _wt(path="q", branch="khivi/" + "q" * 40, branch_prefix="khivi/")
    quiet_cell = worktree_cells(quiet, "r", None, "none", show_tickets=False)[0]
    assert cell_len(belled_cell.plain) == cell_len(quiet_cell.plain) == _RULE_WIDTH


def test_a_stacked_row_overhangs_the_rule_by_design(cache_dir):
    """The one case `_RULE_WIDTH` deliberately doesn't cover: a stacked row
    overhangs by exactly its `└` spine. Pinning the column four columns wider on
    every render, for the rows that aren't stacked, isn't worth it when Workspace
    is competing with Title for the terminal's width."""
    wt = _wt(branch="khivi/" + "w" * 40, branch_prefix="khivi/")
    cache_mod.cwd_cache("pr-nudge", wt.path).write_text("ci")
    row = worktree_cells(wt, "r", None, "none", show_tickets=False, depth=1)[0]
    assert cell_len(row.plain) == _RULE_WIDTH + 4


def test_header_cells_repo_name_and_blank_tail():
    ncols = len(column_labels(show_tickets=False))
    cells = _header_cells("Cockpit", None, ncols)
    assert len(cells) == ncols
    # Repo name, then a dim rule filling the Workspace column so the header
    # reads as a break above its indented rows rather than as another row.
    assert cells[0].plain.startswith("Cockpit ─")
    assert set(cells[0].plain[8:]) == {"─"}
    assert any("bold" in str(sp.style) for sp in cells[0].spans)
    assert any("dim" in str(sp.style) for sp in cells[0].spans)
    assert all(c.plain == "" for c in cells[1:])


def test_header_cells_tinted_by_repo_color():
    ncols = len(column_labels(show_tickets=False))
    tinted = _header_cells("Cockpit", "Blue", ncols)[0]
    plain = _header_cells("Cockpit", None, ncols)[0]
    assert tinted.plain == plain.plain
    # The tint lands on the name; the rule stays dim either way, so it reads as
    # structure rather than as more of the repo's colour.
    # The tinted spans carry a parsed Style with a colour; the untinted ones are
    # bare style *strings* ("bold" / "dim"), which have no `.color`.
    assert any(getattr(sp.style, "color", None) for sp in tinted.spans)
    assert not any(getattr(sp.style, "color", None) for sp in plain.spans)


def test_header_key_prefix_is_nul_led():
    # The sentinel must never collide with a real worktree path key.
    assert HEADER_KEY_PREFIX.startswith("\x00")


def test_header_tooltips_cover_every_column():
    # Every column label (with tickets on, the superset) has a header hover hint,
    # so hovering any header icon explains what the column is.
    for label in column_labels(show_tickets=True):
        assert label in _HEADER_TOOLTIPS


def test_row_tooltips_aligned_and_decode(cache_dir, monkeypatch):
    wt = _wt(path="tips", branch="khivi/tips")
    cache_mod.cwd_cache("pr-state", wt.path).write_text("CHANGES_REQUESTED")
    cache_mod.cwd_cache("pr-checks", wt.path).write_text("✗")
    cache_mod.cwd_cache("pr-comments", wt.path).write_text("2")
    cache_mod.cwd_cache("pr-comments-total", wt.path).write_text("5")
    cache_mod.cwd_cache("pr-muted", wt.path).write_text("muted")
    cache_mod.cwd_cache("git-status", wt.path).write_text("1 2 0")
    cache_mod.cwd_cache("diff-comments", wt.path).write_text("2")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {
            "ticket": {"tickets": [{"id": "PE-1", "state": "In Review"}]}
        },
    )
    tips = row_tooltips(wt, "r", "linear", show_tickets=True)

    def tip(label):
        return tips[_col(label, show_tickets=True)]

    # Aligned to column_labels order (11 with tickets on).
    assert len(tips) == len(column_labels(show_tickets=True)) == 11
    assert tip("Workspace") == "Nudges muted"  # workspace glyph
    assert tip("PR") is None and tip("Author") is None  # self-evident
    assert tip(_APPROVAL_ICON) == "Changes requested"  # 🔀 decoded
    assert tip("CI") == "CI failing"
    assert tip("💬") == "2 of 5 review threads unaddressed"
    assert tip("Ticket") == "PE-1"  # full id, in case `_TICKET_MAX` clipped it
    assert tip(_STATUS_ICON) == "PE-1: In Review"  # 📍 decoded
    assert tip(_DIRTY_ICON) == "1 staged, 2 modified"  # ✎, zero segment dropped
    assert tip(_DIFF_COMMENT_ICON) == "2 pending diff comments — press a to send"
    assert tip("Title") is None


def test_row_tooltips_trello_status_uses_number_not_short_link(cache_dir, monkeypatch):
    # The 📍 hover must match the Ticket cell: Trello's opaque short link is
    # garbage to a human, so show the card number (id fallback), not the id.
    wt = _wt(path="trtip", branch="khivi/trtip")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {
            "ticket": {
                "tickets": [
                    {"id": "EVskYnXV", "state": "Code Complete", "title": "#122"}
                ]
            }
        },
    )
    tips = row_tooltips(wt, "r", "trello", show_tickets=True)
    assert tips[_col(_STATUS_ICON, show_tickets=True)] == "#122: Code Complete"


def test_row_tooltips_blank_when_no_data(cache_dir, monkeypatch):
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload", lambda branch, repo: None
    )
    tips = row_tooltips(_wt(), "r", "none", show_tickets=False)
    assert len(tips) == 9 and all(t is None for t in tips)


@pytest.mark.asyncio
async def test_hover_sets_header_and_value_tooltips(cache_dir, monkeypatch):
    wt = _wt(path="hovertips", branch="khivi/hover")
    cache_mod.cwd_cache("pr-state", wt.path).write_text("APPROVED")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload", lambda branch, repo: {}
    )
    from textual.coordinate import Coordinate

    app = _Host()
    async with app.run_test() as pilot:
        table = app.query_one(WorktreeTable)
        table.update_inventory([("R", "R", None, "none", [wt])])
        await pilot.pause()
        # Header row (-1) of the 🔀 column → the column meaning.
        col = _col(_APPROVAL_ICON)
        assert (
            table._tooltip_for(Coordinate(-1, col)) == _HEADER_TOOLTIPS[_APPROVAL_ICON]
        )
        # Worktree row (index 1; 0 is the group header) → the decoded value.
        assert table._tooltip_for(Coordinate(1, col)) == "Approved"
        # A stale coordinate past the last row degrades to the column meaning.
        assert (
            table._tooltip_for(Coordinate(99, col)) == _HEADER_TOOLTIPS[_APPROVAL_ICON]
        )


class _Host(App[None]):
    def compose(self) -> ComposeResult:
        yield WorktreeTable(id="table")


@pytest.mark.asyncio
async def test_cursor_skips_past_consecutive_header_rows(cache_dir):
    # Regression: with rows [header(A), header(B), wt] the cursor auto-skip
    # only advanced one row off a header, landing on header(B) instead of the
    # worktree row below it — a header row hides every row-targeted footer key.
    wt = _wt(path="consecutive-headers-wt", branch="khivi/feat-x")
    app = _Host()
    async with app.run_test() as pilot:
        table = app.query_one(WorktreeTable)
        table.update_inventory(
            [
                ("A", "A", None, "none", []),  # empty repo -> header row only
                ("B", "B", None, "none", [wt]),  # header row + one worktree
            ]
        )
        await pilot.pause()
        assert table.current_path() == str(wt.path)


@pytest.mark.asyncio
async def test_update_inventory_keys_cache_by_nwo_not_label(cache_dir, monkeypatch):
    # Regression: the Ticket cell (and row caps) key the PR cache by the git nwo
    # — the daemon's cache key — NOT the config display label. When they differ
    # (label "Envesya" vs repo "beta") keying by the label missed every cache
    # file and blanked the column. The inventory's 2nd field is the nwo; feed a
    # `find_pr_payload` that only answers to "beta" and assert the ticket renders.
    seen: list[str | None] = []

    def fake_find(branch, repo=None):
        seen.append(repo)
        return (
            {"ticket": {"tickets": [{"id": "PE-7", "state": "Done"}]}}
            if repo == "beta"
            else {}
        )

    monkeypatch.setattr("cockpit.tui.widgets.worktree_table.find_pr_payload", fake_find)
    wt = _wt(path="envesya-wt", branch="khivi/feat-y")

    class _TicketHost(App[None]):
        def compose(self) -> ComposeResult:
            yield WorktreeTable(show_tickets=True, id="table")

    app = _TicketHost()
    async with app.run_test() as pilot:
        table = app.query_one(WorktreeTable)
        table.update_inventory([("Envesya", "beta", None, "linear", [wt])])
        await pilot.pause()
        row = table.get_row_at(1)  # 0 = group header, 1 = the worktree row
        ticket = row[_col("Ticket", show_tickets=True)]
        assert ticket.plain == "PE-7"  # Ticket cell (ticket cluster), keyed by nwo
    assert "beta" in seen and "Envesya" not in seen


@pytest.mark.asyncio
async def test_links_survive_all_the_way_into_terminal_output(cache_dir, monkeypatch):
    """The one assertion that isn't about cockpit: a `link` span has to come out
    of a rendered DataTable line as an OSC 8 escape.

    Everything else here checks the *mapping* — which cell points where — and
    would keep passing if Textual ever stopped emitting the sequence, leaving
    every link silently dead. Textual has no public promise about this
    (`Strip.render_style` is where it happens), so it is pinned against the real
    render rather than assumed."""
    import io
    import re

    from rich.console import Console
    from rich.segment import Segments

    wt = _wt(path="osc8", branch="khivi/osc8")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: {"url": _PR_URL},
    )
    cache_mod.cwd_cache("pr-num", wt.path).write_text("435")

    app = _Host()
    async with app.run_test(size=(160, 20)) as pilot:
        table = app.query_one(WorktreeTable)
        table.update_inventory([("R", "R", None, "none", [wt])])
        await pilot.pause()
        buf = io.StringIO()
        Console(file=buf, force_terminal=True, width=200).print(
            Segments(list(table.render_line(2))), end=""
        )  # line 0 = column headers, 1 = repo group header, 2 = the worktree row
    # Only `pr-num` was seeded, so the PR cell is the row's one non-blank
    # linkable cell — and the sequence wraps exactly it.
    assert re.findall(r"\x1b]8;id=\d+;([^\x1b]+)", buf.getvalue()) == [_PR_URL]


# ── stacked-PR indentation (rows derived off the `pr-base` cells) ───────────


def test_stacked_row_is_indented_under_the_chain_tip(cache_dir):
    root = _wt(path="root", branch="khivi/a")
    child = _wt(path="child", branch="khivi/b")
    cache_mod.cwd_cache("pr-base", child.path).write_text(root.branch)
    rows = _stack_rows([root, child])
    assert [(wt.path, depth) for wt, depth in rows] == [(child.path, 0), (root.path, 1)]
    assert worktree_cells(root, "r", None, "none", show_tickets=False, depth=1)[
        0
    ].plain == _ws("khivi-a", depth=1)


def test_a_deep_stack_indents_every_member_the_same_single_step(cache_dir):
    # Three PRs deep must not step right three times — one level, flat under
    # the tip, matching the cmux sidebar's single fold.
    a = _wt(path="a", branch="khivi/a")
    b = _wt(path="b", branch="khivi/b")
    c = _wt(path="c", branch="khivi/c")
    cache_mod.cwd_cache("pr-base", b.path).write_text(a.branch)
    cache_mod.cwd_cache("pr-base", c.path).write_text(b.branch)
    assert [(wt.path, depth) for wt, depth in _stack_rows([a, b, c])] == [
        (c.path, 0),
        (a.path, 1),
        (b.path, 1),
    ]


def test_unstacked_row_has_no_indent(cache_dir):
    wt = _wt(path="solo", branch="khivi/solo")
    assert _stack_rows([wt]) == [(wt, 0)]
    assert _plain(wt)[0] == _ws("khivi-solo")


# ── row bands: my queue, then reviews, then snoozed ─────────────────────────


def _snooze(wt):
    cache_mod.cwd_cache("pr-snoozed", wt.path).write_text("snoozed")


def _coworker(wt, login="someone"):
    cache_mod.cwd_cache("pr-author", wt.path).write_text(login)


def test_reviews_and_snoozed_rows_sink_below_my_queue(cache_dir):
    # The pile the sidebar already parks at the bottom must not bury the row
    # that actually wants me — git's order interleaves them.
    dozing = _wt(path="dozing", branch="khivi/dozing")
    review = _wt(path="review", branch="khivi/review")
    mine = _wt(path="mine", branch="khivi/mine")
    _snooze(dozing)
    _coworker(review)
    rows = _stack_rows([dozing, review, mine])
    assert [wt.path for wt, _ in rows] == [mine.path, review.path, dozing.path]


def test_a_snoozed_coworker_pr_sinks_past_the_reviews_band(cache_dir):
    # Snooze outranks review: a coworker PR I've already read belongs below the
    # ones I haven't, matching the sidebar's reviews → snoozed → sunk-stack order.
    read = _wt(path="read", branch="khivi/read")
    unread = _wt(path="unread", branch="khivi/unread")
    _coworker(read)
    _coworker(unread)
    _snooze(read)
    rows = _stack_rows([read, unread])
    assert [wt.path for wt, _ in rows] == [unread.path, read.path]


def test_banding_keeps_git_order_within_a_band(cache_dir):
    # Stable sort: rows that share a band must not be reshuffled.
    a, b, c = (_wt(path=f"{n}", branch=f"khivi/{n}") for n in "abc")
    assert [wt.path for wt, _ in _stack_rows([a, b, c])] == [a.path, b.path, c.path]


def test_a_stack_sinks_whole_and_bands_by_its_tip(cache_dir):
    # A chain with one snoozed member must not split: contiguity under the tip
    # is what keeps the table and the sidebar reading as the same stack.
    root = _wt(path="root", branch="khivi/root")
    tip = _wt(path="tip", branch="khivi/tip")
    mine = _wt(path="mine", branch="khivi/mine")
    cache_mod.cwd_cache("pr-base", tip.path).write_text(root.branch)
    _snooze(tip)  # the tip heads the chain, so the whole chain sinks
    assert [(wt.path, depth) for wt, depth in _stack_rows([root, tip, mine])] == [
        (mine.path, 0),
        (tip.path, 0),
        (root.path, 1),
    ]


def test_a_snooze_below_the_tip_does_not_sink_the_chain(cache_dir):
    # The other half of banding-by-tip: one snoozed dependency must not bury the
    # active stack sitting on top of it, so a snooze on a non-tip member moves
    # nothing. (The sidebar applies the same tip rule by moving the group.)
    root = _wt(path="root", branch="khivi/root")
    tip = _wt(path="tip", branch="khivi/tip")
    mine = _wt(path="mine", branch="khivi/mine")
    cache_mod.cwd_cache("pr-base", tip.path).write_text(root.branch)
    _snooze(root)  # a member below the tip, so the chain keeps its band
    assert [(wt.path, depth) for wt, depth in _stack_rows([root, tip, mine])] == [
        (tip.path, 0),
        (root.path, 1),
        (mine.path, 0),
    ]


def test_a_muted_row_stays_in_my_queue(cache_dir):
    # Mute is "stop nudging me about a PR I'm working on", not "not my turn" —
    # only a snooze sinks.
    muted = _wt(path="muted", branch="khivi/muted")
    plain = _wt(path="plain", branch="khivi/plain")
    cache_mod.cwd_cache("pr-muted", muted.path).write_text("muted")
    assert [wt.path for wt, _ in _stack_rows([muted, plain])] == [
        muted.path,
        plain.path,
    ]


# ── the per-repo snoozed fold ───────────────────────────────────────────────


def test_split_snoozed_separates_the_last_band(cache_dir):
    dozing = _wt(path="dozing", branch="khivi/dozing")
    review = _wt(path="review", branch="khivi/review")
    mine = _wt(path="mine", branch="khivi/mine")
    _snooze(dozing)
    _coworker(review)
    live, snoozed = _split_snoozed([dozing, review, mine])
    assert [wt.path for wt, _ in live] == [mine.path, review.path]
    assert [wt.path for wt, _ in snoozed] == [dozing.path]


def test_a_folding_stack_goes_whole(cache_dir):
    # The fold takes or leaves a whole chain: a tip that folds away without its
    # members would tear the stack in half.
    root = _wt(path="root", branch="khivi/root")
    tip = _wt(path="tip", branch="khivi/tip")
    cache_mod.cwd_cache("pr-base", tip.path).write_text(root.branch)
    _snooze(tip)
    live, snoozed = _split_snoozed([root, tip])
    assert live == []
    assert [(wt.path, d) for wt, d in snoozed] == [(tip.path, 0), (root.path, 1)]


def test_a_snooze_below_the_tip_folds_nothing(cache_dir):
    # The other half: one snoozed dependency must not fold the active stack
    # sitting on top of it.
    root = _wt(path="root", branch="khivi/root")
    tip = _wt(path="tip", branch="khivi/tip")
    cache_mod.cwd_cache("pr-base", tip.path).write_text(root.branch)
    _snooze(root)
    live, snoozed = _split_snoozed([root, tip])
    assert [wt.path for wt, _ in live] == [tip.path, root.path]
    assert snoozed == []


def test_stack_rows_still_returns_every_row_in_band_order(cache_dir):
    # `_stack_rows` is the un-folded view (the band order the fold builds on) —
    # splitting it must not drop the snoozed half.
    dozing = _wt(path="dozing", branch="khivi/dozing")
    mine = _wt(path="mine", branch="khivi/mine")
    _snooze(dozing)
    assert [wt.path for wt, _ in _stack_rows([dozing, mine])] == [
        mine.path,
        dozing.path,
    ]


@pytest.mark.asyncio
async def test_snoozed_rows_collapse_behind_a_disclosure_row(cache_dir):
    dozing = _wt(path="dozing", branch="khivi/dozing")
    mine = _wt(path="mine", branch="khivi/mine")
    _snooze(dozing)
    app = _Host()
    async with app.run_test() as pilot:
        table = app.query_one(WorktreeTable)
        table.update_inventory([("R", "R", None, "none", [dozing, mine])])
        await pilot.pause()
        # header, my live row, then the fold standing in for the snoozed one.
        assert table.row_count == 3
        assert table.get_row_at(1)[0].plain == _ws("khivi-mine")
        fold = table.get_row_at(2)
        assert fold[0].plain == f"{ROW_INDENT}▸ 1 snoozed"
        # Collapsed, the trailing column names what's folded away.
        assert "khivi-dozing" in fold[-1].plain
        # FOLD_CAP rides alongside: the row stands for a fold, which is what
        # advertises `A` (ask the pile) here and on the repo header.
        assert table._row_caps[snoozed_row_key("R")] == frozenset(
            {SNOOZED_CAP, FOLD_CAP}
        )


@pytest.mark.asyncio
async def test_expanding_the_fold_renders_the_snoozed_rows(cache_dir):
    dozing = _wt(path="dozing", branch="khivi/dozing")
    mine = _wt(path="mine", branch="khivi/mine")
    _snooze(dozing)
    app = _Host()
    async with app.run_test() as pilot:
        table = app.query_one(WorktreeTable)
        table.update_inventory(
            [("R", "R", None, "none", [dozing, mine])], expanded_snoozed={"R"}
        )
        await pilot.pause()
        assert table.row_count == 4
        assert table.get_row_at(2)[0].plain == f"{ROW_INDENT}▾ 1 snoozed"
        assert table.get_row_at(3)[0].plain == _ws("khivi-dozing")
        assert "expanded" in table._row_caps[snoozed_row_key("R")]


@pytest.mark.asyncio
async def test_no_fold_row_when_nothing_is_snoozed(cache_dir):
    mine = _wt(path="mine", branch="khivi/mine")
    app = _Host()
    async with app.run_test() as pilot:
        table = app.query_one(WorktreeTable)
        table.update_inventory([("R", "R", None, "none", [mine])])
        await pilot.pause()
        assert table.row_count == 2
        assert snoozed_row_key("R") not in table._row_caps


@pytest.mark.asyncio
async def test_the_cursor_rests_on_a_fold_row(cache_dir):
    # A repo whose rows are ALL snoozed collapses to header + fold. The
    # cursor-skip loop hops off group headers; it must stop at the fold, which is
    # the row `z` acts on (and the row a snooze lands the cursor on).
    dozing = _wt(path="dozing", branch="khivi/dozing")
    _snooze(dozing)
    app = _Host()
    async with app.run_test() as pilot:
        table = app.query_one(WorktreeTable)
        table.update_inventory([("R", "R", None, "none", [dozing])])
        await pilot.pause()
        assert table._current_row_key() == snoozed_row_key("R")
        # No workspace behind it, so every row action no-ops there.
        assert table.current_path() is None
        assert table.current_repo_name() == "R"


@pytest.mark.asyncio
async def test_the_cursor_row_reports_its_repo_colour(cache_dir):
    # The header-bar readout tints itself from this, so it must come off the
    # inventory the table already rendered — resolving it from `load_config()`
    # would put a disk read on every arrow key.
    mine = _wt(path="mine", branch="khivi/mine")
    app = _Host()
    async with app.run_test() as pilot:
        table = app.query_one(WorktreeTable)
        table.update_inventory([("R", "R", "Magenta", "none", [mine])])
        await pilot.pause()
        assert table.current_repo_name() == "R"
        assert table.current_repo_color() == "Magenta"


@pytest.mark.asyncio
async def test_a_repo_with_no_colour_reports_none_not_a_default(cache_dir):
    # Blank is not a colour: `repo_text` has to be able to tell "no tint set"
    # from a tint, or an uncoloured repo would render as whatever it defaulted to.
    mine = _wt(path="mine", branch="khivi/mine")
    app = _Host()
    async with app.run_test() as pilot:
        table = app.query_one(WorktreeTable)
        table.update_inventory([("R", "R", None, "none", [mine])])
        await pilot.pause()
        assert table.current_repo_color() is None


@pytest.mark.asyncio
async def test_the_repo_colour_follows_the_cursor_across_repos(cache_dir):
    # Two repos, two tints: the readout must track the cursor rather than latch
    # onto whichever repo rendered first.
    a = _wt(path="alpha", branch="khivi/alpha")
    b = _wt(path="beta", branch="khivi/beta")
    app = _Host()
    async with app.run_test() as pilot:
        table = app.query_one(WorktreeTable)
        table.update_inventory(
            [("A", "A", "Magenta", "none", [a]), ("B", "B", "Teal", "none", [b])]
        )
        await pilot.pause()
        seen = {}
        for _ in range(table.row_count):
            name = table.current_repo_name()
            if name is not None:
                seen[name] = table.current_repo_color()
            table.action_cursor_down()
            await pilot.pause()
        assert seen == {"A": "Magenta", "B": "Teal"}


@pytest.mark.parametrize("show_cost", [False, True])
def test_a_disclosure_tail_lands_in_title_not_the_cost_column(cache_dir, show_cost):
    # `Title` is not the last column — `show_cost` appends `$` after it — so a
    # `ncols - 1` tail would blank Title and (DataTable auto-sizes to content)
    # widen the numeric column for every row in the table.
    cols = column_labels(show_tickets=False, show_cost=show_cost)
    for cells, name in (
        (_snoozed_cells(["alpha", "beta"], cols, expanded=False), "alpha"),
        (_hidden_cells(["gamma"], cols, expanded=False), "gamma"),
    ):
        by_col = dict(zip(cols, (c.plain for c in cells), strict=False))
        assert name in by_col["Title"]
        assert by_col.get("$", "") == ""


@pytest.mark.asyncio
async def test_move_cursor_to_key_reports_a_miss(cache_dir):
    mine = _wt(path="mine", branch="khivi/mine")
    app = _Host()
    async with app.run_test() as pilot:
        table = app.query_one(WorktreeTable)
        table.update_inventory([("R", "R", None, "none", [mine])])
        await pilot.pause()
        assert table.move_cursor_to_key(str(mine.path))
        assert not table.move_cursor_to_key(snoozed_row_key("R"))
        assert table._current_row_key() == str(mine.path)  # unmoved


def test_indent_precedes_the_nudge_glyph(cache_dir):
    # The tree spine has to stay leftmost or the indent column ragged-edges on
    # whichever rows happen to carry a bell.
    wt = _wt(path="bell", branch="khivi/b")
    cache_mod.cwd_cache("pr-nudge", wt.path).write_text("ci")
    cell = worktree_cells(wt, "r", None, "none", show_tickets=False, depth=1)[0].plain
    assert cell == _ws("khivi-b", glyph=ICON_PR_NUDGE, depth=1)


@pytest.mark.asyncio
async def test_update_inventory_renders_a_stack_tip_first(cache_dir):
    # git lists worktrees alphabetically-ish; the chain must still render as
    # tip-then-members rather than in git's order.
    root = _wt(path="stack-root", branch="khivi/root")
    child = _wt(path="stack-child", branch="khivi/child")
    cache_mod.cwd_cache("pr-base", child.path).write_text(root.branch)
    app = _Host()
    async with app.run_test() as pilot:
        table = app.query_one(WorktreeTable)
        table.update_inventory([("R", "R", None, "none", [root, child])])
        await pilot.pause()
        # Row 0 is the repo group header.
        assert table.get_row_at(1)[0].plain == _ws("khivi-child")
        assert table.get_row_at(2)[0].plain == _ws("khivi-root", depth=1)


def test_a_snoozed_row_carries_no_glyph(cache_dir):
    """Snooze is expressed by the `▾ N snoozed` fold the row sits in, not by a
    per-row glyph — that's what makes the slot free for the 🔇 below."""
    wt = _wt(branch="khivi/dozing", branch_prefix="khivi/")
    cache_mod.cwd_cache("pr-snoozed", wt.path).write_text("snoozed")
    cell = worktree_cells(wt, "r", None, "none", show_tickets=False)[0]
    assert cell.plain == _ws("dozing")


def test_a_snoozed_row_shows_no_bell(cache_dir):
    """Dropping the 💤 glyph must not drop snooze's *suppression* of the bell.
    `pr-nudge` is never blanked for a snoozed PR, so a snooze that later goes
    CI-red would otherwise advertise a 🔔 `should_nudge` will never ring."""
    wt = _wt(branch="khivi/resting", branch_prefix="khivi/")
    cache_mod.cwd_cache("pr-snoozed", wt.path).write_text("snoozed")
    cache_mod.cwd_cache("pr-nudge", wt.path).write_text("ci")
    cell = worktree_cells(wt, "r", None, "none", show_tickets=False)[0]
    assert cell.plain == _ws("resting")
    # ...and the hover text agrees with the (absent) glyph.
    tips = row_tooltips(wt, "r", "none", show_tickets=False)
    assert tips[0] == "Snoozed until a new comment or review"


def test_one_branch_in_three_repos_snoozes_one_row(cache_dir):
    """The cells are keyed by worktree, not branch — three repos each holding a
    `khivi/ci-gatekeeper` worktree shared one cell set, so snoozing any of them
    folded away all three (and `z` read the wrong repo's PR number off the
    shared `pr-num`)."""
    a, b, c = (
        _wt(path=p, branch="khivi/ci-gatekeeper", branch_prefix="khivi/")
        for p in ("repo-a/gate", "repo-b/gate", "repo-c/gate")
    )
    cache_mod.cwd_cache("pr-num", a.path).write_text("82")
    cache_mod.cwd_cache("pr-snoozed", a.path).write_text("snoozed")
    cache_mod.cwd_cache("pr-num", b.path).write_text("20")
    cache_mod.cwd_cache("pr-num", c.path).write_text("27")

    live, snoozed = _split_snoozed([a, b, c])
    assert [wt.path for wt, _ in snoozed] == [a.path]
    assert [wt.path for wt, _ in live] == [b.path, c.path]
    # And the row the other two render is their own PR, not the snoozed one's.
    assert _plain(b)[_col("PR")] == "#20"
    assert _plain(c)[_col("PR")] == "#27"


def test_a_snoozed_row_still_shows_its_mute(cache_dir):
    """The one thing left worth saying inside the fold: a row that is muted *and*
    snoozed keeps its 🔇, because the fold says nothing about the mute."""
    wt = _wt(branch="khivi/both", branch_prefix="khivi/")
    cache_mod.cwd_cache("pr-snoozed", wt.path).write_text("snoozed")
    cache_mod.cwd_cache("pr-muted", wt.path).write_text("muted")
    cell = worktree_cells(wt, "r", None, "none", show_tickets=False)[0]
    assert cell.plain == _ws("both", glyph=ICON_PR_MUTED)


def test_every_glyph_takes_the_same_slot_so_labels_align(cache_dir):
    """The whole point of the fixed slot: a belled row, a muted row and a quiet
    one all start their label at the same column. The glyphs differ in ink width
    per font, so this asserts *cells*, not bytes."""
    starts = set()
    for cell_name, value in (
        ("pr-muted", "muted"),
        ("pr-nudge", "ci"),
        ("", ""),  # a quiet row — no cell seeded
    ):
        wt = _wt(path=f"{cell_name or 'quiet'}", branch=f"khivi/{cell_name}")
        if cell_name:
            cache_mod.cwd_cache(cell_name, wt.path).write_text(value)
        plain = worktree_cells(wt, "r", None, "none", show_tickets=False)[0].plain
        starts.add(cell_len(plain[: plain.index("khivi")]))
    assert starts == {len(ROW_INDENT) + _STATUS_SLOT}


def test_row_capabilities_snoozed(cache_dir, monkeypatch):
    wt = _wt(branch="khivi/napping")
    cache_mod.cwd_cache("pr-num", wt.path).write_text("9")
    cache_mod.cwd_cache("pr-snoozed", wt.path).write_text("snoozed")
    monkeypatch.setattr(
        "cockpit.tui.widgets.worktree_table.find_pr_payload",
        lambda branch, repo: None,
    )
    assert row_capabilities(wt, "r", "none") == frozenset({"pr", "snoozed"})


# ── The `$` cost column ─────────────────────────────────────────────────────


@pytest.fixture
def costed(cache_dir, tmp_path, monkeypatch):
    """Give a worktree path a `wt-cost` cell, as the daemon's fast tick would."""

    def _seed(usd, path=tmp_path / "wt"):
        cache_mod.atomic_write(
            cache_mod.cwd_cache("wt-cost", path), "" if usd is None else str(usd)
        )
        return _wt(path=str(path))

    return _seed


def _cost(wt, *, show_tickets=False):
    """The row's `$` cell, located by label so a column reorder can't break it."""
    cells = worktree_cells(
        wt, "repo", None, "none", show_tickets=show_tickets, show_cost=True
    )
    return cells[column_labels(show_tickets=show_tickets, show_cost=True).index("$")]


def test_cost_column_is_absent_by_default(cache_dir):
    """No `$` unless the app opts in — a machine whose Claude Code reports
    nothing must not grow a permanently blank column."""
    assert "$" not in column_labels(show_tickets=False)
    assert "$" not in column_labels(show_tickets=True)
    assert len(_plain(_wt())) == len(column_labels(show_tickets=False))


def test_cost_column_trails_every_other_column(cache_dir):
    cols = column_labels(show_tickets=True, show_cost=True)
    assert cols[-1] == "$"
    assert cols[-2] == "Title"


def test_cost_cell_renders_whole_dollars(costed):
    assert _cost(costed(31.5146)).plain == "$32"
    assert _cost(costed(135.05)).plain == "$135"


def test_cost_cell_keeps_cents_under_a_dollar(costed):
    """Under $1 the cents are the whole signal, and `$0` would read as free."""
    assert _cost(costed(0.42)).plain == "$0.42"


def test_cost_cell_is_blank_at_zero(costed):
    """Blank, never `$0.00`: an empty cell also means "never reported", and the
    row can't tell that apart from genuinely free work."""
    assert _cost(costed(0)).plain == ""
    assert _cost(costed(None)).plain == ""


def test_cost_cell_is_blank_with_no_cell_at_all(cache_dir):
    assert _cost(_wt(path="never-ticked")).plain == ""


def test_cost_cell_count_matches_columns(costed):
    wt = costed(5.0)
    for show_tickets in (False, True):
        cells = worktree_cells(
            wt, "repo", None, "none", show_tickets=show_tickets, show_cost=True
        )
        cols = column_labels(show_tickets=show_tickets, show_cost=True)
        assert len(cells) == len(cols)


def test_cost_column_has_a_header_tooltip(cache_dir):
    assert _HEADER_TOOLTIPS["$"]


def test_cost_tooltip_carries_the_exact_figure(costed):
    """The cell rounds; the hover has to give the rounded cents back."""
    wt = costed(31.5146)
    tips = row_tooltips(wt, "repo", "none", show_tickets=False, show_cost=True)
    assert len(tips) == len(column_labels(show_tickets=False, show_cost=True))
    assert tips[-1] == "$31.51 across all sessions here"


def test_cost_tooltip_is_none_on_a_blank_cell(costed):
    """A blank cell falls back to the column meaning rather than asserting $0."""
    tips = row_tooltips(costed(0), "repo", "none", show_tickets=False, show_cost=True)
    assert tips[-1] is None


def test_cost_tooltips_align_when_the_column_is_off(cache_dir):
    tips = row_tooltips(_wt(), "repo", "none", show_tickets=False)
    assert len(tips) == len(column_labels(show_tickets=False))
