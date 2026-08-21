"""Tests for the HeaderBar widget (cockpit/tui/widgets/header_bar.py).

`_fmt` and `build_tooltip` are pure functions — tested directly with no app
needed. One headless `App.run_test()` test proves the reactive watcher wiring
actually updates `self.tooltip` (not just at mount), per AGENTS.md's TUI test
style: drive the widget's own scheduling/state, not the reconcile cycle.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from cockpit.tui.widgets.header_bar import (
    OFF,
    RUNNING,
    WAITING,
    HeaderBar,
    _fmt,
    build_tooltip,
)

# --- _fmt: sentinel decoding + normal countdowns -----------------------------


def test_fmt_waiting():
    assert _fmt(WAITING) == "waiting"


def test_fmt_off():
    assert _fmt(OFF) == "off"


def test_fmt_running():
    assert _fmt(RUNNING) == "running…"


def test_fmt_zero():
    assert _fmt(0) == "0:00"


def test_fmt_normal_countdown():
    assert _fmt(65) == "1:05"


def test_fmt_large_countdown():
    assert _fmt(300) == "5:00"


# --- build_tooltip: pure function --------------------------------------------


def test_tooltip_explains_both_ticks_in_ordinary_countdown_case():
    text = build_tooltip(120, 20)
    assert "Slow tick" in text
    assert "Fast tick" in text
    assert "reconcile" in text.lower()
    assert "github" in text.lower() or "GitHub" in text
    assert "network-free" in text.lower()
    # No sentinel decode lines when both are plain countdowns.
    assert "waiting" not in text.lower()
    assert "disabled" not in text.lower()
    assert "running right now" not in text.lower()


def test_tooltip_decodes_slow_waiting():
    text = build_tooltip(WAITING, 20)
    assert "slow tick is waiting" in text.lower()
    assert "tick lock" in text.lower()


def test_tooltip_decodes_fast_waiting():
    text = build_tooltip(120, WAITING)
    assert "fast tick is waiting" in text.lower()


def test_tooltip_decodes_fast_off():
    text = build_tooltip(120, OFF)
    assert "fast tick is disabled" in text.lower()


def test_tooltip_decodes_slow_running():
    text = build_tooltip(RUNNING, 20)
    assert "slow tick is running right now" in text.lower()


def test_tooltip_decodes_fast_running():
    text = build_tooltip(120, RUNNING)
    assert "fast tick is running right now" in text.lower()


def test_tooltip_always_includes_base_descriptions_even_when_decoding():
    # The decode is additive, never a replacement for the "what is this" prose.
    text = build_tooltip(WAITING, OFF)
    assert "Slow tick" in text
    assert "Fast tick" in text
    assert "waiting" in text.lower()
    assert "disabled" in text.lower()


# --- watcher wiring: proves the tooltip updates on reactive change, not just
# --- at mount -----------------------------------------------------------------


class _HeaderBarHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield HeaderBar()


@pytest.mark.asyncio
async def test_tooltip_set_on_mount():
    app = _HeaderBarHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(HeaderBar)
        assert bar.tooltip == build_tooltip(bar.slow_remaining, bar.fast_remaining)


@pytest.mark.asyncio
async def test_tooltip_updates_when_reactive_changes_after_mount():
    app = _HeaderBarHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(HeaderBar)
        initial_tooltip = bar.tooltip

        bar.slow_remaining = WAITING
        await pilot.pause()

        assert bar.tooltip != initial_tooltip
        assert "slow tick is waiting" in (bar.tooltip or "").lower()


@pytest.mark.asyncio
async def test_tooltip_updates_for_fast_remaining_change():
    app = _HeaderBarHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(HeaderBar)

        bar.fast_remaining = OFF
        await pilot.pause()

        assert "fast tick is disabled" in (bar.tooltip or "").lower()
