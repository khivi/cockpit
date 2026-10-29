"""The failed-spawns overlay and the header segment that opens it."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Static

from cockpit.tui.widgets.header_bar import HeaderBar
from cockpit.tui.widgets.spawn_failures_screen import SpawnFailuresScreen


class _Host(App[None]):
    def compose(self) -> ComposeResult:
        yield HeaderBar()


def _shown(widget: Static) -> bool:
    # A function, so mypy re-reads `display` after each repaint instead of
    # narrowing it once.
    return bool(widget.display)


@pytest.mark.asyncio
@pytest.mark.covers("header.failures~1")
async def test_segment_appears_only_above_zero_and_moves_no_countdown():
    app = _Host()
    async with app.run_test(size=(120, 24)) as pilot:
        await pilot.pause()
        bar = app.query_one(HeaderBar)
        seg = app.query_one("#header-failures", Static)
        assert not _shown(seg)
        before = app.query_one("#header-status", Static).region.x
        bar.spawn_failures = 2
        await pilot.pause()
        assert _shown(seg)
        assert "2 spawns failed" in str(seg.render())
        assert app.query_one("#header-status", Static).region.x == before
        bar.spawn_failures = 1
        await pilot.pause()
        assert "1 spawn failed" in str(seg.render())
        bar.spawn_failures = 0
        await pilot.pause()
        assert not _shown(seg)


@pytest.mark.asyncio
@pytest.mark.covers("header.failures~1")
async def test_overlay_lists_failures_newest_first_and_strips_control():
    failures = [
        {
            "pr": 1,
            "repo": "o/old",
            "title": "t",
            "failures": 1,
            "ts": 1.0,
            "error": "a",
        },
        {
            "pr": 2,
            "repo": "o/new",
            "title": "x\x1b]8;;evil\x07",
            "failures": 3,
            "ts": 2.0,
            "error": "e" * 200,
        },
    ]
    app = _Host()
    async with app.run_test(size=(160, 40)) as pilot:
        await app.push_screen(SpawnFailuresScreen(failures))
        await pilot.pause()
        table = app.screen.query_one("#sf-table", DataTable)
        assert table.row_count == 2
        assert table.get_row_at(0)[0].plain == "#2"
        assert "\x1b" not in table.get_row_at(0)[2].plain
        assert table.get_row_at(0)[5].plain.endswith("…")
        assert table.tooltip == "e" * 200
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SpawnFailuresScreen)
