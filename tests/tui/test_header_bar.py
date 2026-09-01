"""Tests for the HeaderBar widget (cockpit/tui/widgets/header_bar.py).

`_fmt` and `build_tooltip` are pure functions — tested directly with no app
needed. One headless `App.run_test()` test proves the reactive watcher wiring
actually updates `self.tooltip` (not just at mount), per AGENTS.md's TUI test
style: drive the widget's own scheduling/state, not the reconcile cycle.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from cockpit.tui.widgets.header_bar import (
    OFF,
    RUNNING,
    WAITING,
    HeaderBar,
    _fmt,
    build_tooltip,
    repo_text,
    status_text,
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


# --- status_text: pure function ----------------------------------------------


def test_status_text_shows_the_slow_countdown_alone_by_default():
    assert status_text("", 65, OFF) == "slow ⏱ 1:05"


def test_status_text_leads_with_the_version_when_set():
    text = status_text("9.9.9", 65, OFF)
    assert text.startswith("[bold cyan]cockpit 9.9.9[/]")
    assert "slow ⏱ 1:05" in text


def test_status_text_adds_the_fast_countdown_when_the_fast_tick_is_on():
    assert "fast ⏱ 0:20" in status_text("", 65, 20)


# --- repo_text: pure function ------------------------------------------------


def test_repo_text_is_empty_for_no_row():
    assert repo_text("", None).plain == ""


def test_repo_text_names_the_repo():
    assert "myrepo" in repo_text("myrepo", None).plain


def test_repo_text_survives_an_unknown_colour():
    # A colour not in CMUX_COLOR_ANSI must degrade to the untinted name, never
    # raise — preflight rejects one at startup, but a renderer is not the place
    # to discover that.
    assert "myrepo" in repo_text("myrepo", "Chartreuse").plain


def test_repo_text_tints_with_the_repos_sidebar_colour():
    # The ANSI colorizer must actually reach the Text as a style, so the readout
    # matches the group header's tint rather than reading as plain text.
    tinted = repo_text("myrepo", "Magenta")
    plain = repo_text("myrepo", None)
    assert tinted.plain == plain.plain
    assert tinted.spans != plain.spans


# --- the two halves ----------------------------------------------------------


class _HeaderBarHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield HeaderBar()


def _painted_spans(app: App[None]) -> list:
    """The repo half's style spans.

    Read reflectively: `Static.render()` is typed as a renderable union, and
    Textual re-wraps the `Text` it was handed in its own `Content`, so narrowing
    to a concrete class pins an internal Textual type this test has no stake in.
    """
    return list(getattr(app.query_one("#header-repo", Static).render(), "spans", []))


@pytest.mark.asyncio
async def test_menu_half_carries_its_own_tooltip():
    # The menu explains the menu: its own tooltip wins the ancestor walk over
    # the bar's tick explanation.
    app = _HeaderBarHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#header-menu", Static).tooltip == HeaderBar.MENU_TOOLTIP


@pytest.mark.asyncio
async def test_status_half_defers_to_the_bar_for_its_tooltip():
    # It carries none of its own, so hovering the countdowns walks up to the
    # bar and finds the slow/fast explanation.
    app = _HeaderBarHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(HeaderBar)
        assert app.query_one("#header-status", Static).tooltip is None
        assert bar.tooltip == build_tooltip(bar.slow_remaining, bar.fast_remaining)


@pytest.mark.asyncio
async def test_status_half_repaints_when_a_countdown_changes():
    app = _HeaderBarHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(HeaderBar)
        bar.version_text = "9.9.9"
        bar.slow_remaining = 65
        await pilot.pause()
        painted = str(app.query_one("#header-status", Static).render())
        assert "cockpit 9.9.9" in painted
        assert "slow ⏱ 1:05" in painted


# --- watcher wiring: proves the tooltip updates on reactive change, not just
# --- at mount -----------------------------------------------------------------


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
        # `Widget.tooltip` is typed as a renderable union, so coerce before
        # matching — `_sync_tooltip` only ever assigns a str.
        assert "slow tick is waiting" in str(bar.tooltip).lower()


@pytest.mark.asyncio
async def test_repo_half_carries_its_own_tooltip():
    app = _HeaderBarHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#header-repo", Static).tooltip == HeaderBar.REPO_TOOLTIP


@pytest.mark.asyncio
async def test_repo_half_is_blank_until_a_row_is_highlighted():
    app = _HeaderBarHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert str(app.query_one("#header-repo", Static).render()).strip() == ""


@pytest.mark.asyncio
async def test_repo_half_repaints_when_the_cursor_row_changes_repo():
    app = _HeaderBarHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(HeaderBar)
        bar.repo_name = "myrepo"
        await pilot.pause()
        assert "myrepo" in str(app.query_one("#header-repo", Static).render())

        bar.repo_name = "otherrepo"
        await pilot.pause()
        painted = str(app.query_one("#header-repo", Static).render())
        assert "otherrepo" in painted
        assert "myrepo" not in painted


@pytest.mark.asyncio
async def test_repo_half_repaints_when_only_the_colour_changes():
    # Both reactives own the readout: a repo renamed to the same string with a
    # new tint still has to repaint, so the watcher can't hang off the name.
    app = _HeaderBarHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(HeaderBar)
        bar.repo_name = "myrepo"
        await pilot.pause()
        before = _painted_spans(app)

        bar.repo_color = "Magenta"
        await pilot.pause()
        assert _painted_spans(app) != before


@pytest.mark.asyncio
async def test_tooltip_updates_for_fast_remaining_change():
    app = _HeaderBarHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(HeaderBar)

        bar.fast_remaining = OFF
        await pilot.pause()

        assert "fast tick is disabled" in str(bar.tooltip).lower()
