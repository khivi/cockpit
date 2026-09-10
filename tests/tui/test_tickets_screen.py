r"""Headless tests for the ticket-inbox overlay (cockpit/tui/widgets/tickets_screen.py).

The screen is handed already-filtered payloads and dismisses with a spawn source
— a string `cockpit new` routes. These pin that contract plus the properties that
are load-bearing rather than cosmetic: the URL-over-id source rule (the only form
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
    HEADER_KEY_PREFIX,
    TicketsScreen,
    _age,
    ticket_source,
)


class _Host(App[None]):
    def compose(self) -> ComposeResult:
        yield Static("host", id="host")


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


async def _open(app, buckets, result=None):
    callback = result.append if result is not None else None
    await app.push_screen(TicketsScreen(buckets), callback)


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
async def test_enter_dismisses_with_the_source():
    app: _Host = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket()]}, result)
        await pilot.pause()
        await pilot.press("down")  # off the org header, onto the ticket
        await pilot.press("enter")
        await pilot.pause()
    assert result == ["https://linear.app/acme/issue/PE-412"]


@pytest.mark.asyncio
async def test_enter_on_an_org_header_does_nothing():
    """Same no-op every row action takes on the main table's group headers."""
    app: _Host = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket()]}, result)
        await pilot.pause()
        await pilot.press("enter")  # cursor starts on the header
        await pilot.pause()
        assert result == []
        assert isinstance(app.screen, TicketsScreen)


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
async def test_each_org_gets_a_header_above_its_tickets():
    app = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket("PE-1")], "widgets-co": [_ticket("WID-2")]})
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        keys = [str(k.value) for k in table.rows]
    assert keys == [
        f"{HEADER_KEY_PREFIX}acme",
        "acme\x00PE-1",
        f"{HEADER_KEY_PREFIX}widgets-co",
        "widgets-co\x00WID-2",
    ]


@pytest.mark.asyncio
async def test_one_ticket_under_two_orgs_keeps_both_rows():
    """Two orgs sharing a credential and a key both list it; an id-only row key
    would silently collapse the second into the first."""
    app = _Host()
    async with app.run_test() as pilot:
        await _open(app, {"acme": [_ticket("PE-1")], "other": [_ticket("PE-1")]})
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
