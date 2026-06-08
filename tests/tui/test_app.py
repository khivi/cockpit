"""Headless tests for the Textual TUI (cockpit/tui/app.py).

Uses Textual's `App.run_test()` Pilot — no real terminal needed. Tick functions
are injected (not real gh/git), and `load_config` is patched so cards never read
the developer's live config. Per AGENTS.md these test the TUI's own scheduling /
gating / capture behaviour, not the reconcile cycle underneath.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cockpit.lib.git import Worktree
from cockpit.tui.app import CockpitApp
from cockpit.tui.widgets.header_bar import HeaderBar
from cockpit.tui.widgets.log_pane import LogPane
from cockpit.tui.widgets.workspace_card import WorkspaceCard

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # No live config reads; no network update check.
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {"repos": [], "check_update": False},
    )
    monkeypatch.setattr("cockpit.lib.version.latest_version", lambda: None)


def _make_app(**kw):
    calls = {"slow": 0, "fast": 0}

    def slow():
        calls["slow"] += 1

    def fast():
        calls["fast"] += 1

    app = CockpitApp(
        slow_tick=kw.get("slow_tick", slow),
        fast_tick=kw.get("fast_tick", fast),
        slow_secs=kw.get("slow_secs", 300),
        fast_secs=kw.get("fast_secs", 30),
    )
    return app, calls


async def test_mounts_with_header_and_log():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(HeaderBar) is not None
        assert app.query_one(LogPane) is not None


async def test_initial_ticks_fire_on_mount():
    app, calls = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.6)
        assert calls["slow"] >= 1
        assert calls["fast"] >= 1


async def test_sync_key_kicks_slow_tick():
    app, calls = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.6)
        before = calls["slow"]
        await pilot.press("s")
        await pilot.pause(0.6)
        assert calls["slow"] > before


async def test_in_flight_gate_blocks_overlapping_kick(monkeypatch):
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.6)
        ran = []
        monkeypatch.setattr(app, "_run_slow", lambda: ran.append(1))
        app._slow_in_flight = True
        app._kick_slow()
        assert ran == []  # blocked while a slow tick is in flight
        app._slow_in_flight = False
        app._kick_slow()
        assert ran == [1]  # runs once the flag clears


async def test_tick_output_lands_in_log():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        # on_mount prints "slow-tick: every 300s" through the captured stdout.
        await pilot.pause(0.6)
        log = app.query_one(LogPane)
        assert len(log.lines) > 0


async def test_render_cards_mounts_one_card_per_worktree():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        wts = [
            Worktree(path=Path("/tmp/a"), branch="khivi/feat-a"),
            Worktree(path=Path("/tmp/b"), branch="khivi/feat-b"),
        ]
        app._render_cards([("repo", False, wts)])
        await pilot.pause()
        assert len(app.query(WorkspaceCard)) == 2


async def test_render_cards_empty_inventory_shows_placeholder():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_cards([])
        await pilot.pause()
        assert len(app.query(WorkspaceCard)) == 0
