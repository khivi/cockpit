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
from cockpit.tui.widgets.worktree_table import WorktreeTable

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    # No live config reads; no network update check; watch.log under a tmp dir
    # (not the developer's real ~/.config/cockpit).
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {"repos": [], "check_update": False},
    )
    monkeypatch.setattr("cockpit.lib.version.latest_version", lambda: None)
    monkeypatch.setattr("cockpit.tui.app.COCKPIT_HOME", tmp_path)


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


async def test_mounts_with_header_and_table():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(HeaderBar) is not None
        assert app.query_one(WorktreeTable) is not None


async def test_initial_ticks_fire_on_mount():
    app, calls = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.8)
        assert calls["slow"] >= 1
        assert calls["fast"] >= 1


async def test_fast_starts_only_after_first_slow():
    order: list[str] = []
    app = CockpitApp(
        slow_tick=lambda: order.append("slow"),
        fast_tick=lambda: order.append("fast"),
        slow_secs=300,
        fast_secs=30,
    )
    async with app.run_test() as pilot:
        await pilot.pause(0.8)
        assert order, "no ticks ran"
        assert order[0] == "slow"  # slow runs first on startup
        assert "fast" in order  # fast started once slow completed
        assert app._fast_started


async def test_sync_key_kicks_slow_tick():
    app, calls = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.6)
        before = calls["slow"]
        await pilot.press("s")
        await pilot.pause(0.6)
        assert calls["slow"] > before


async def test_phase_gate_blocks_overlapping_kick(monkeypatch):
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.6)
        ran = []
        monkeypatch.setattr(app, "_run_slow", lambda: ran.append(1))
        app._slow_phase = "running"
        app._kick_slow()
        assert ran == []  # blocked while a slow tick is waiting/running
        app._slow_phase = "idle"
        app._kick_slow()
        assert ran == [1]  # runs once the phase clears


async def test_waiting_on_lock_shows_waiting_not_running():
    # Hold the tick lock so the slow worker blocks acquiring it: its phase must
    # be "waiting" (header sentinel -3), not "running" (-1).
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.6)
        app._tick_lock.acquire()
        try:
            app._slow_phase = "idle"  # allow a fresh kick
            app._kick_slow()
            await pilot.pause(0.4)  # worker spins up, blocks on the held lock
            assert app._slow_phase == "waiting"
            app._update_countdown()
            assert app.query_one(HeaderBar).slow_remaining == -3
        finally:
            app._tick_lock.release()
        await pilot.pause(0.4)  # worker acquires, runs, returns to idle
        assert app._slow_phase == "idle"


async def test_tick_output_written_to_bounded_log_file():
    # No LogPane in the layout; tick output lands in the bounded watch.log.
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.5)  # mount prints "slow-tick: …" → drained to file
    assert "slow-tick" in app._log_path.read_text()


async def test_log_file_bounded_to_tail():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        for i in range(300):
            print(f"line {i}")  # captured by the stdout writer
        app._drain_log()
    lines = app._log_path.read_text().splitlines()
    assert len(lines) <= 200
    assert lines[-1] == "line 299"  # newest kept


async def test_render_table_adds_one_row_per_worktree():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        wts = [
            Worktree(path=Path("/tmp/a"), branch="khivi/feat-a"),
            Worktree(path=Path("/tmp/b"), branch="khivi/feat-b"),
        ]
        app._render_table([("repo", None, False, wts)])
        await pilot.pause()
        assert app.query_one(WorktreeTable).row_count == 2


async def test_render_table_empty_inventory_has_no_rows():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([])
        await pilot.pause()
        assert app.query_one(WorktreeTable).row_count == 0


async def test_arrow_keys_move_row_cursor():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        wts = [
            Worktree(path=Path("/tmp/a"), branch="khivi/feat-a"),
            Worktree(path=Path("/tmp/b"), branch="khivi/feat-b"),
            Worktree(path=Path("/tmp/c"), branch="khivi/feat-c"),
        ]
        app._render_table([("repo", None, False, wts)])
        await pilot.pause()
        table = app.query_one(WorktreeTable)
        table.focus()
        await pilot.pause()
        start = table.cursor_row
        await pilot.press("down")
        await pilot.pause()
        assert table.cursor_row == start + 1
