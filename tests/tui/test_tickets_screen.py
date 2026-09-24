r"""Headless tests for the ticket-inbox overlay (cockpit/tui/widgets/tickets_screen.py).

The screen is handed already-filtered payloads and posts a `Start` carrying a
spawn source — a string `cockpit new` routes — without closing, since the inbox
is a list you work down. These pin that contract plus the properties that are
load-bearing rather than cosmetic: the URL-over-id source rule (the only form
that routes a GitHub issue or a Trello card), the org-qualified row key (one
ticket can legitimately sit under two orgs), and the `strip_control` pass over
tracker text (a title is written by whoever filed the ticket).

The app-side wiring — the `i` key, the `in_flight` filter, the unroutable refusal
— is in test_app.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from textual.app import App, ComposeResult
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Static

from cockpit.tui.widgets.tickets_screen import (
    _TICKET_MAX,
    AMBIGUOUS_MARK,
    HEADER_KEY_PREFIX,
    STARTED_STATE,
    UNROUTABLE_MARK,
    TicketsScreen,
    _age,
    ticket_source,
)


class _Host(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.started: list[str] = []

    def compose(self) -> ComposeResult:
        yield Static("host", id="host")

    def on_tickets_screen_start(self, event: TicketsScreen.Start) -> None:
        self.started.append(event.source)


def _ticket(tid="PE-412", **over) -> dict:
    ticket = {
        "id": tid,
        "team": tid.split("-")[0],
        "title": f"Work on {tid}",
        "state": "Todo",
        "url": f"https://linear.app/acme/issue/{tid}",
        "updated_at": "2026-09-09T10:00:00Z",
    }
    ticket.update(over)
    return ticket


async def _open(app, buckets, result=None, routes=None):
    callback = result.append if result is not None else None
    await app.push_screen(TicketsScreen(buckets, routes), callback)


def _ticket_cells(app) -> list[str]:
    """Every Ticket-column cell, as painted."""
    table = app.screen.query_one(DataTable)
    return [
        str(table.get_cell_at(Coordinate(row, 0))) for row in range(table.row_count)
    ]


# ── the spawn source ────────────────────────────────────────────────────────


def test_source_prefers_the_url():
    """A GitHub issue's `owner/repo#N` id doesn't classify; its URL does."""
    assert ticket_source(_ticket()) == "https://linear.app/acme/issue/PE-412"
    gh = _ticket("acme/widgets#77", url="https://github.com/acme/widgets/issues/77")
    assert ticket_source(gh) == "https://github.com/acme/widgets/issues/77"


def test_source_falls_back_to_the_id():
    assert ticket_source(_ticket(url="")) == "PE-412"
    assert ticket_source({"id": "PE-1"}) == "PE-1"


def test_source_of_an_empty_ticket_is_empty():
    assert ticket_source({}) == ""


@pytest.mark.asyncio
async def test_enter_starts_the_ticket_without_closing_the_inbox():
    """The inbox is a list you work down: popping it on the first enter made
    starting a second ticket a re-open and a re-fold."""
    app: _Host = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket()]}, result)
        await pilot.pause()
        await pilot.press("down")  # off the org header, onto the ticket
        await pilot.press("enter")
        await pilot.pause()
        assert app.started == ["https://linear.app/acme/issue/PE-412"]
        assert isinstance(app.screen, TicketsScreen)
    assert result == []


@pytest.mark.asyncio
async def test_a_started_row_says_so_and_a_second_enter_does_nothing():
    """`cockpit new` attaches to an existing worktree, but it runs detached — so
    a double-tap has both children find none and the loser cuts a `-2`."""
    app: _Host = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket()]})
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        assert str(table.get_cell_at(Coordinate(1, 2))) == STARTED_STATE
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.started) == 1


@pytest.mark.asyncio
async def test_releasing_a_ticket_gives_the_row_its_state_back():
    """The mark is optimistic — an unroutable ticket and a cancelled repo picker
    both decline after the keypress, and would otherwise read as started."""
    app: _Host = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket()]})
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, TicketsScreen)
        screen.release("https://linear.app/acme/issue/PE-412")
        await pilot.pause()
        table = screen.query_one(DataTable)
        assert str(table.get_cell_at(Coordinate(1, 2))) == "Todo"
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.started) == 2


@pytest.mark.asyncio
async def test_a_started_row_keeps_its_mark_across_a_fold():
    """`_started` is keyed by source, not by a row key a rebuild reissues."""
    app: _Host = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket()]})
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("up")
        await pilot.press("enter")  # fold the org
        await pilot.press("enter")  # and open it again
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        assert str(table.get_cell_at(Coordinate(1, 2))) == STARTED_STATE


@pytest.mark.asyncio
async def test_enter_on_an_org_header_folds_it_rather_than_dismissing():
    """A header's one gesture is its fold — as `z` and `h` on the main table."""
    app: _Host = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket()]}, result)
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        assert table.row_count == 2  # a lone org opens expanded
        await pilot.press("enter")  # cursor starts on the header
        await pilot.pause()
        assert result == []
        assert isinstance(app.screen, TicketsScreen)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_escape_dismisses_with_none():
    app: _Host = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket()]}, result)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result == [None]


# ── rows ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_several_orgs_start_folded_to_their_headers():
    """One tracker with a hundred cards assigned to you would otherwise bury the
    org that has three."""
    app = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket("PE-1")], "widgets-co": [_ticket("WID-2")]})
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        keys = [str(k.value) for k in table.rows]
        headers = [table.get_cell_at(Coordinate(0, 0)).plain]
    assert keys == [
        f"{HEADER_KEY_PREFIX}acme",
        f"{HEADER_KEY_PREFIX}widgets-co",
    ]
    assert headers == ["\u25b8 acme (1)"]  # folded, and its count is on the row


@pytest.mark.asyncio
async def test_opening_one_org_leaves_the_others_folded():
    app = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket("PE-1")], "widgets-co": [_ticket("WID-2")]})
        await pilot.pause()
        await pilot.press("down")  # onto the second org's header
        await pilot.press("enter")
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        keys = [str(k.value) for k in table.rows]
        # The cursor stays on the row that was toggled, which has not moved.
        cursor = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
    assert keys == [
        f"{HEADER_KEY_PREFIX}acme",
        f"{HEADER_KEY_PREFIX}widgets-co",
        "widgets-co\x00WID-2",
    ]
    assert str(cursor) == f"{HEADER_KEY_PREFIX}widgets-co"


@pytest.mark.asyncio
async def test_one_ticket_under_two_orgs_keeps_both_rows():
    """Two orgs sharing a credential and a key both list it; an id-only row key
    would silently collapse the second into the first."""
    app = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket("PE-1")], "other": [_ticket("PE-1")]})
        await pilot.pause()
        await pilot.press("enter")  # open the first org
        await pilot.press("down")
        await pilot.press("down")  # onto the second org's header
        await pilot.press("enter")
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        assert table.row_count == 4  # two headers, two tickets


@pytest.mark.asyncio
async def test_a_ticket_with_no_id_is_skipped():
    app = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket("PE-1"), {"title": "orphan"}]})
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        assert table.row_count == 2  # header + the one real ticket


@pytest.mark.asyncio
async def test_a_trello_card_shows_its_number_not_its_short_link():
    """`6rm3JJPY` names nothing a human recognizes; `#122` is what's on the card.
    The id stays the key everything else joins on."""
    app = _Host()
    card = _ticket("6rm3JJPY", handle="#122", url="https://trello.com/c/6rm3JJPY")
    async with app.run_test() as pilot:
        await _open(app, {"acme": [card]})
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        assert table.get_cell_at(Coordinate(1, 0)).plain.strip() == "#122"
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert app.started == ["https://trello.com/c/6rm3JJPY"]


@pytest.mark.asyncio
async def test_a_ticket_with_no_handle_shows_its_id():
    app = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket("PE-412")]})
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        assert table.get_cell_at(Coordinate(1, 0)).plain.strip() == "PE-412"


@pytest.mark.asyncio
@pytest.mark.covers("tickets-screen.widths~1")
async def test_a_revealed_title_is_not_clipped_to_its_column_label():
    """Opening a fold must not paint the new rows at the pre-fold width.

    `DataTable` widens an auto column from its cells in `_update_dimensions`,
    which runs on idle — so a paint that beats it caches every Title clipped to
    the width of `Title` itself, and only the row under the mouse re-renders,
    hover being part of the cell cache key. Hence the explicit widths: the
    rebuild below deliberately never yields.
    """
    app = _Host()
    title = "Drop spatie/laravel-sitemap for a DB"
    async with app.run_test() as pilot:
        await _open(app, {"a": [_ticket(title=title)], "b": [_ticket("W-1")]})
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        screen = app.screen
        assert isinstance(screen, TicketsScreen)
        screen._open.add("a")
        screen._rebuild()
        widths = [c.get_render_width(table) for c in table.ordered_columns]
        cell = table.get_cell_at(Coordinate(1, 1)).plain
    assert cell == title
    assert widths[1] >= len(title)


@pytest.mark.asyncio
async def test_a_long_org_name_keeps_its_count():
    """The count is what makes a folded header worth reading, so the name is
    ellipsized around it."""
    app = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"an-extravagantly-long-org-name": [_ticket("PE-1")]})
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        header = table.get_cell_at(Coordinate(0, 0)).plain
    assert header.endswith(" (1)")
    assert header.startswith("▾ an-extravagantly")
    assert len(header) <= 27


@pytest.mark.asyncio
async def test_tracker_text_is_neutralized():
    """A title is written by whoever filed the ticket — an ESC in it would paint
    a second hyperlink over a row cockpit never named."""
    app = _Host()
    hostile = _ticket(title="\x1b]8;;https://evil\x07Fix login\x1b]8;;\x07")
    async with app.run_test() as pilot:
        await _open(app, {"acme": [hostile]})
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        title = table.get_cell_at(Coordinate(1, 1)).plain
    assert "\x1b" not in title
    assert "\x07" not in title
    assert "Fix login" in title


@pytest.mark.asyncio
async def test_an_empty_inbox_says_so_rather_than_showing_nothing():
    app = _Host()
    async with app.run_test() as pilot:
        await _open(app, {})
        await pilot.pause()
        subtitle = app.screen.query_one("#tk-subtitle", Static)
        assert "Nothing assigned" in str(subtitle.render())


@pytest.mark.asyncio
async def test_the_subtitle_counts_across_orgs():
    app = _Host()
    async with app.run_test() as pilot:
        await _open(
            app, {"a": [_ticket("PE-1"), _ticket("PE-2")], "b": [_ticket("W-1")]}
        )
        await pilot.pause()
        subtitle = app.screen.query_one("#tk-subtitle", Static)
        assert str(subtitle.render()).startswith("3 assigned")


# ── age ─────────────────────────────────────────────────────────────────────


def test_age_reads_every_provider_stamp():
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    assert _age("2026-09-10T11:46:00Z", now=now) == "14m"
    assert _age("2026-09-10T06:00:00.000+0000", now=now) == "6h"  # Jira's offset
    assert _age("2026-09-08T12:00:00.000Z", now=now) == "2d"  # Linear / Trello


def test_age_of_an_unparsable_or_absent_stamp_is_blank():
    assert _age("") == ""
    assert _age("last tuesday") == ""


def test_age_of_a_future_stamp_is_blank():
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    assert _age((now + timedelta(hours=2)).isoformat(), now=now) == ""


def test_age_assumes_utc_for_a_naive_stamp():
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    assert _age("2026-09-10T11:00:00", now=now) == "1h"


# ── routing markers ─────────────────────────────────────────────────────────
#
# The screen is *handed* the candidate names — it reads no config, so a marker
# costs nothing per repaint. Stage one only, which is why a marked row is a
# prediction the app re-checks before it acts.


@pytest.mark.asyncio
async def test_a_cleanly_routed_ticket_carries_no_marker():
    """The common row. A marker on every row would be a Repo column by another
    name, which is the thing this deliberately is not."""
    app: _Host = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket("PE-1")]}, routes={"PE-1": ["widgets"]})
        await pilot.pause()
        assert AMBIGUOUS_MARK not in _ticket_cells(app)[1]
        assert UNROUTABLE_MARK not in _ticket_cells(app)[1]


@pytest.mark.asyncio
async def test_several_candidates_mark_the_row_ambiguous():
    app: _Host = _Host()
    async with app.run_test() as pilot:
        await _open(
            app,
            {"platform": [_ticket("PLAT-77")]},
            routes={"PLAT-77": ["infra", "cluster"]},
        )
        await pilot.pause()
        assert AMBIGUOUS_MARK in _ticket_cells(app)[1]


@pytest.mark.asyncio
async def test_no_candidate_marks_the_row_unroutable():
    """`publish` unions scopes across a credential group, so a bucket can hold a
    ticket no repo in it claims. Those rows spawn nowhere, and used to say so
    only after enter."""
    app: _Host = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket("OTHER-9")]}, routes={"OTHER-9": []})
        await pilot.pause()
        assert UNROUTABLE_MARK in _ticket_cells(app)[1]


@pytest.mark.asyncio
async def test_a_ticket_outside_stage_one_is_marked_with_nothing():
    """Absent from the map is not the same as matching nothing: a Trello card
    carries no key to match on, so `!` there would be a lie."""
    app: _Host = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket("6rm3JJPY")]}, routes={})
        await pilot.pause()
        cell = _ticket_cells(app)[1]
        assert AMBIGUOUS_MARK not in cell and UNROUTABLE_MARK not in cell


@pytest.mark.asyncio
@pytest.mark.covers("tickets-screen.widths~1")
async def test_a_marker_never_widens_the_ticket_column():
    """The widths are explicit for a paid-for reason — auto-sizing caches the
    wrong width on a fold — so the marker comes out of the handle's budget."""
    app: _Host = _Host()
    long_id = "PLAT-" + "9" * 40
    async with app.run_test() as pilot:
        await _open(
            app,
            {"platform": [_ticket(long_id)]},
            routes={long_id: ["infra", "cluster"]},
        )
        await pilot.pause()
        assert len(_ticket_cells(app)[1]) <= _TICKET_MAX + 1


@pytest.mark.asyncio
async def test_the_legend_names_only_the_markers_on_screen():
    """Spelling out glyphs nothing uses is how a hint line stops being read."""
    app: _Host = _Host()
    async with app.run_test() as pilot:
        await _open(
            app,
            {"platform": [_ticket("PLAT-77")]},
            routes={"PLAT-77": ["infra", "cluster"]},
        )
        await pilot.pause()
        legend = str(app.screen.query_one("#tk-legend", Static).render())
        assert AMBIGUOUS_MARK in legend
        assert "no configured repo" not in legend


@pytest.mark.asyncio
async def test_a_cleanly_routed_inbox_shows_no_legend_at_all():
    app: _Host = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket("PE-1")]}, routes={"PE-1": ["widgets"]})
        await pilot.pause()
        assert not app.screen.query("#tk-legend")
