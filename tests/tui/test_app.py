"""Headless tests for the Textual TUI (cockpit/tui/app.py).

Uses Textual's `App.run_test()` Pilot — no real terminal needed. Tick functions
are injected (not real gh/git), and `load_config` is patched so cards never read
the developer's live config. Per AGENTS.md these test the TUI's own scheduling /
gating / capture behaviour, not the reconcile cycle underneath.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from cockpit.lib.git import Worktree
from cockpit.tui.app import CockpitApp
from cockpit.tui.widgets.config_screen import ConfigScreen
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
    # Pin the workspace backend so footer/key tests are deterministic regardless
    # of whether cmux/limux is on PATH (CI has neither → would resolve "none").
    # Backend-specific tests override this.
    monkeypatch.setattr("cockpit.tui.app.resolve_tool", lambda: "cmux")


def _make_app(**kw):
    calls: dict[str, Any] = {"slow": 0, "fast": 0, "only_repo": []}

    def slow(on_repo_done=None, only_repo=None):
        calls["slow"] += 1
        calls["only_repo"].append(only_repo)

    def fast():
        calls["fast"] += 1

    app = CockpitApp(
        slow_tick=kw.get("slow_tick", slow),
        fast_tick=kw.get("fast_tick", fast),
        slow_secs=kw.get("slow_secs", 300),
        fast_secs=kw.get("fast_secs", 30),
    )
    # Startup spawns worker threads (_prime_table + the slow/fast tick finallys)
    # that render the table off the git-derived inventory via call_from_thread.
    # These tests drive _render_table explicitly, so neutralize the background
    # render — otherwise a late worker render can clobber the controlled table
    # (order-dependent flake under pytest-randomly). The dedicated priming tests
    # build CockpitApp directly, not via _make_app, so they keep the real render.
    app._publish_inventory = lambda: None  # type: ignore[method-assign]
    return app, calls


async def test_mounts_with_header_and_table():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(HeaderBar) is not None
        assert app.query_one(WorktreeTable) is not None


async def test_table_cursor_preserves_repo_color():
    # DataTable's default cursor style forces its own foreground onto every
    # cell, clobbering the repo color painted into the Workspace cell
    # (WorktreeTable._workspace_cell). "renderable" priority is what keeps the
    # cell's own Rich Text color on the highlighted row.
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(WorktreeTable).cursor_foreground_priority == "renderable"


async def test_header_shows_running_version(monkeypatch):
    # The header's top-left displays the running plugin version on mount.
    monkeypatch.setattr("cockpit.tui.app.version.running_version", lambda: "9.9.9")
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(HeaderBar).version_text == "9.9.9"


async def test_initial_ticks_fire_on_mount():
    app, calls = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.8)
        assert calls["slow"] >= 1
        assert calls["fast"] >= 1


async def test_table_primes_before_slow_completes(monkeypatch, tmp_path):
    # The worktree table shows rows on startup even while the first slow tick
    # is still running — priming reads git + cache, not the network.
    wt = Worktree(path=tmp_path / "wt-a", branch="khivi/feat-a")
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {
            "repos": [{"name": "repo", "path": str(tmp_path)}],
            "check_update": False,
        },
    )
    monkeypatch.setattr("cockpit.tui.app.worktrees", lambda p, prefix="": [wt])

    release = threading.Event()

    def slow(on_repo_done=None, only_repo=None):
        release.wait(2)  # hold the slow tick open

    app = CockpitApp(
        slow_tick=slow, fast_tick=lambda: None, slow_secs=300, fast_secs=30
    )
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one(WorktreeTable)
            for _ in range(20):
                if table.row_count >= 1:
                    break
                await pilot.pause(0.1)
            assert table.row_count == 2  # repo header + 1 worktree; primed early
            assert app._slow_phase in ("waiting", "running")  # slow still open
    finally:
        release.set()


async def test_slow_tick_gets_per_repo_publish_callback(monkeypatch, tmp_path):
    # The slow tick is handed an `on_repo_done` callback; invoking it mid-tick
    # republishes the table from the cells/worktrees on disk so a finished repo
    # surfaces before the whole tick returns.
    wt = Worktree(path=tmp_path / "wt-a", branch="khivi/feat-a")
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {
            "repos": [{"name": "repo", "path": str(tmp_path)}],
            "check_update": False,
        },
    )
    monkeypatch.setattr("cockpit.tui.app.worktrees", lambda p, prefix="": [wt])

    captured: dict = {}
    published = threading.Event()

    def slow(on_repo_done=None, only_repo=None):
        captured["cb"] = on_repo_done
        on_repo_done()  # a repo finished — surface it now, not at tick end
        published.set()

    app = CockpitApp(
        slow_tick=slow, fast_tick=lambda: None, slow_secs=300, fast_secs=30
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(20):
            if published.is_set():
                break
            await pilot.pause(0.1)
        assert callable(captured.get("cb"))  # callback was threaded in
        table = app.query_one(WorktreeTable)
        for _ in range(20):
            if table.row_count >= 1:
                break
            await pilot.pause(0.1)
        assert table.row_count == 2  # repo header + 1 worktree, per-repo callback


async def test_fast_starts_only_after_first_slow():
    order: list[str] = []

    def slow(on_repo_done=None, only_repo=None):
        order.append("slow")

    app = CockpitApp(
        slow_tick=slow,
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
        monkeypatch.setattr(app, "_run_slow", lambda only_repo=None: ran.append(1))
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


async def test_update_check_re_runs_on_each_slow_tick(monkeypatch):
    # The update check rides the slow tick (no separate hourly timer), so a
    # release that lands after startup surfaces on the next slow tick — not up
    # to an hour later. Clear the indicator after the startup check, kick a
    # fresh slow tick, and assert it gets re-set.
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {"repos": [], "check_update": True},
    )
    monkeypatch.setattr("cockpit.lib.version.running_version", lambda: "0.1")
    monkeypatch.setattr("cockpit.lib.version.latest_version", lambda: "0.2")

    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.8)  # startup slow tick + its update check land
        header = app.query_one(HeaderBar)
        assert header.update_text == "0.1 → 0.2"

        header.update_text = ""  # forget it; only a re-check can restore it
        await pilot.press("s")  # kick a fresh slow tick
        for _ in range(20):
            if header.update_text:
                break
            await pilot.pause(0.1)
        assert header.update_text == "0.1 → 0.2"  # slow tick re-checked


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


async def test_render_table_adds_header_plus_one_row_per_worktree():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        wts = [
            Worktree(path=Path("/tmp/a"), branch="khivi/feat-a"),
            Worktree(path=Path("/tmp/b"), branch="khivi/feat-b"),
        ]
        app._render_table([("repo", None, False, wts)])
        await pilot.pause()
        table = app.query_one(WorktreeTable)
        assert table.row_count == 3  # one repo header + two worktrees
        # Cursor auto-skips off the header onto the first worktree row.
        assert table.current_path() == "/tmp/a"


async def test_render_table_empty_inventory_has_no_rows():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([])
        await pilot.pause()
        assert app.query_one(WorktreeTable).row_count == 0


async def test_current_path_returns_cursor_row_key():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        wts = [
            Worktree(path=Path("/tmp/a"), branch="khivi/feat-a"),
            Worktree(path=Path("/tmp/b"), branch="khivi/feat-b"),
        ]
        app._render_table([("repo", None, False, wts)])
        await pilot.pause()
        table = app.query_one(WorktreeTable)
        # Row 0 is the repo header; the worktrees follow, so /tmp/b is row 2.
        table.move_cursor(row=2)
        assert table.current_path() == "/tmp/b"


async def test_current_path_none_when_empty():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(WorktreeTable).current_path() is None


def _seed_one_worktree(monkeypatch, tmp_path, *, branch="khivi/feat-a"):
    """Patch the resolution leaves so the cursor row maps to one worktree whose
    cmux workspace is `ws1`. Returns the Worktree."""
    wt = Worktree(path=tmp_path / "wt-a", branch=branch)
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {
            "repos": [{"name": "repo", "path": str(tmp_path)}],
            "check_update": False,
        },
    )
    monkeypatch.setattr("cockpit.tui.app.worktrees", lambda p, prefix="": [wt])
    monkeypatch.setattr("cockpit.tui.app.workspace_cwds", lambda: {"ws1": wt.path})
    monkeypatch.setattr("cockpit.tui.app.workspace_names", lambda: {"ws1": "feat-a"})
    monkeypatch.setattr("cockpit.tui.app.find_pr_payload", lambda *a, **k: None)
    return wt


async def test_focus_key_focuses_workspace(monkeypatch, tmp_path):
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.is_cmux", lambda: True)
    refs: list[str] = []
    monkeypatch.setattr(
        "cockpit.tui.app.select_workspace", lambda ref, **k: refs.append(ref)
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause(0.6)
    assert refs == ["ws1"]


async def test_focus_via_enter_key(monkeypatch, tmp_path):
    # Enter on the focused row selects it → focuses (single click does not).
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.is_cmux", lambda: True)
    refs: list[str] = []
    monkeypatch.setattr(
        "cockpit.tui.app.select_workspace", lambda ref, **k: refs.append(ref)
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        app.query_one(WorktreeTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause(0.6)
    assert refs == ["ws1"]


async def test_focus_via_double_click(monkeypatch, tmp_path):
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.is_cmux", lambda: True)
    refs: list[str] = []
    monkeypatch.setattr(
        "cockpit.tui.app.select_workspace", lambda ref, **k: refs.append(ref)
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        # Row 1 (y=2 incl. the column header) is the worktree; row 1 is the repo
        # group header.
        await pilot.click(WorktreeTable, offset=(2, 2), times=2)
        await pilot.pause(0.6)
    assert refs == ["ws1"]


async def test_single_click_does_not_focus(monkeypatch, tmp_path):
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.is_cmux", lambda: True)
    refs: list[str] = []
    monkeypatch.setattr(
        "cockpit.tui.app.select_workspace", lambda ref, **k: refs.append(ref)
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.click(WorktreeTable, offset=(2, 1))
        await pilot.pause(0.4)
    assert refs == []  # single click only moves the cursor


async def test_focus_key_noop_on_limux(monkeypatch, tmp_path):
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.is_cmux", lambda: False)
    refs: list[str] = []
    monkeypatch.setattr(
        "cockpit.tui.app.select_workspace", lambda ref, **k: refs.append(ref)
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause(0.6)
    assert refs == []


def _patch_open_workspace(monkeypatch, *, backend, has_ws):
    """Wire `w`'s leaves: `resolve_tool` → backend, `workspace_cwds`/`names`
    so the row's worktree either already has a workspace (`has_ws`) or not, and
    capturing stubs for both spawn helpers + `select_workspace`. Returns the
    capture dicts."""
    monkeypatch.setattr("cockpit.tui.app.resolve_tool", lambda: backend)
    cwds = {"ws1": Path("/x")}  # placeholder; the test sets the real path below
    monkeypatch.setattr(
        "cockpit.tui.app.workspace_cwds", lambda: cwds if has_ws else {}
    )
    monkeypatch.setattr(
        "cockpit.tui.app.workspace_names", lambda: {"ws1": "feat-a"} if has_ws else {}
    )
    cap: dict[str, list] = {"select": [], "orphan": [], "pr": []}

    def _spawn_orphan(wt, **k):
        cap["orphan"].append(wt.branch)
        return "ws2"

    def _spawn_pr(pr, wt, **k):
        cap["pr"].append(pr.number)
        return "ws2"

    monkeypatch.setattr(
        "cockpit.tui.app.select_workspace", lambda ref, **k: cap["select"].append(ref)
    )
    monkeypatch.setattr("cockpit.tui.app.spawn_orphan_workspace", _spawn_orphan)
    monkeypatch.setattr("cockpit.tui.app.spawn_pr_workspace", _spawn_pr)
    return cap, cwds


async def _press_open(app, wt):
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause(0.6)


async def test_open_workspace_focuses_existing_on_cmux(monkeypatch, tmp_path):
    # Workspace already exists → `w` just focuses it (no spawn), like `f`.
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    cap, cwds = _patch_open_workspace(monkeypatch, backend="cmux", has_ws=True)
    cwds.clear()
    cwds["ws1"] = wt.path
    app, _ = _make_app()
    await _press_open(app, wt)
    assert cap["select"] == ["ws1"]
    assert cap["orphan"] == [] and cap["pr"] == []


async def test_open_workspace_spawns_orphan_when_missing(monkeypatch, tmp_path):
    # No workspace + no cached PR → spawn an orphan workspace, then focus it.
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    cap, _ = _patch_open_workspace(monkeypatch, backend="cmux", has_ws=False)
    app, _ = _make_app()
    await _press_open(app, wt)
    assert cap["orphan"] == [wt.branch]
    assert cap["pr"] == []
    assert cap["select"] == ["ws2"]


async def test_open_workspace_spawns_pr_when_payload(monkeypatch, tmp_path):
    # No workspace but a cached PR → reconstruct it and spawn a PR workspace.
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    cap, _ = _patch_open_workspace(monkeypatch, backend="cmux", has_ws=False)
    monkeypatch.setattr(
        "cockpit.tui.app.find_pr_payload",
        lambda *a, **k: {"number": 42, "title": "t", "branch": wt.branch},
    )
    monkeypatch.setattr("cockpit.tui.app.load_pref", lambda n: None)
    app, _ = _make_app()
    await _press_open(app, wt)
    assert cap["pr"] == [42]
    assert cap["orphan"] == []
    assert cap["select"] == ["ws2"]


async def test_open_workspace_spawns_without_focus_on_limux(monkeypatch, tmp_path):
    # limux can spawn but not select — create the workspace, never focus it.
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    cap, _ = _patch_open_workspace(monkeypatch, backend="limux", has_ws=False)
    app, _ = _make_app()
    await _press_open(app, wt)
    assert cap["orphan"] == [wt.branch]
    assert cap["select"] == []


async def test_open_workspace_noop_when_tool_none(monkeypatch, tmp_path):
    # tool=none → no backend, so `w` neither spawns nor focuses.
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    cap, _ = _patch_open_workspace(monkeypatch, backend="none", has_ws=False)
    app, _ = _make_app()
    await _press_open(app, wt)
    assert cap["orphan"] == [] and cap["pr"] == [] and cap["select"] == []


async def test_close_key_enqueues_when_clean(monkeypatch, tmp_path):
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.resolve_pr_state", lambda *a, **k: ("", None))
    enq: list = []
    monkeypatch.setattr("cockpit.tui.app.enqueue", lambda req: enq.append(req))
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause(0.6)
    assert len(enq) == 1
    req = enq[0]
    assert req.ref == "ws1"
    assert req.worktree_path == wt.path
    assert req.branch == "khivi/feat-a"
    assert req.forced is False


async def test_close_key_refuses_on_blockers(monkeypatch, tmp_path):
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.resolve_pr_state", lambda *a, **k: ("OPEN", 1))
    enq: list = []
    monkeypatch.setattr("cockpit.tui.app.enqueue", lambda req: enq.append(req))
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause(0.6)
    assert enq == []  # `c` (no force) refuses on the open-PR soft blocker


async def test_force_close_key_overrides_open_pr(monkeypatch, tmp_path):
    # `C` force-close: it enqueues despite the soft open-PR blocker. No hard
    # blockers (the seeded path isn't a real worktree).
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.worktree_state_blockers", lambda *a, **k: [])
    monkeypatch.setattr("cockpit.tui.app.resolve_pr_state", lambda *a, **k: ("OPEN", 1))
    enq: list = []
    monkeypatch.setattr("cockpit.tui.app.enqueue", lambda req: enq.append(req))
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("C")
        await pilot.pause(0.6)
    assert len(enq) == 1
    assert enq[0].forced is True  # force flag propagates to the teardown request


async def test_force_close_key_still_refuses_hard_blockers(monkeypatch, tmp_path):
    # Force never overrides uncommitted / unpushed work.
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "cockpit.tui.app.worktree_state_blockers",
        lambda *a, **k: ["1 uncommitted file(s)"],
    )
    enq: list = []
    monkeypatch.setattr("cockpit.tui.app.enqueue", lambda req: enq.append(req))
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("C")
        await pilot.pause(0.6)
    assert enq == []  # hard blocker stands even under force


async def test_close_key_merge_aware_clears_hard_unpushed(monkeypatch, tmp_path):
    # The squash-merge fix at the TUI layer: an out-of-band merge resolved live
    # as MERGED feeds pr_merged=True into the *hard* gate, so the false-positive
    # unpushed block is skipped and the close enqueues (with delete_branch set).
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "cockpit.tui.app.resolve_pr_state", lambda *a, **k: ("MERGED", 7)
    )
    seen: list = []

    def _spy_blockers(path, *, branch=None, is_mine=True, pr_merged=False):
        seen.append(pr_merged)
        # Mirror the real gate: a merged PR skips the unpushed check.
        return [] if pr_merged else ["3 unpushed commit(s)"]

    monkeypatch.setattr("cockpit.tui.app.worktree_state_blockers", _spy_blockers)
    enq: list = []
    monkeypatch.setattr("cockpit.tui.app.enqueue", lambda req: enq.append(req))
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause(0.6)
    assert seen == [True]  # MERGED flowed into the hard gate
    assert len(enq) == 1
    assert enq[0].delete_branch is True  # merged → local ref is reaped


async def test_focus_shows_notification(monkeypatch, tmp_path):
    # The log pane is removed, so a toast is the only on-screen feedback.
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.is_cmux", lambda: True)
    monkeypatch.setattr("cockpit.tui.app.select_workspace", lambda ref, **k: None)
    toasts: list[str] = []
    app, _ = _make_app()
    monkeypatch.setattr(app, "notify", lambda msg, **k: toasts.append(msg))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause(0.6)
    assert any("focused" in t for t in toasts)


async def test_close_key_noop_when_table_empty(monkeypatch):
    enq: list = []
    monkeypatch.setattr("cockpit.tui.app.enqueue", lambda req: enq.append(req))
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause(0.3)
    assert enq == []


async def test_mute_key_mutes_unmuted_pr(monkeypatch, tmp_path):
    from cockpit.lib.nudges import NudgePref

    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.read_text", lambda *a, **k: "123")
    monkeypatch.setattr("cockpit.tui.app.load_pref", lambda pr: NudgePref())
    saved: list = []
    monkeypatch.setattr(
        "cockpit.tui.app.save_pref", lambda pr, pref: saved.append((pr, pref))
    )
    app, calls = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        before = calls["slow"]
        await pilot.press("m")
        await pilot.pause(0.6)
    assert len(saved) == 1
    pr, pref = saved[0]
    assert pr == 123
    assert pref.muted  # muted
    assert calls["slow"] > before  # kicks the slow tick to republish pr-muted
    # The kick is scoped to the row's repo path, not a full all-repos reconcile,
    # so the line refreshes without round-tripping `gh` for every other repo.
    assert calls["only_repo"][-1] == str(Path(tmp_path))


async def test_sync_key_kicks_full_cycle_not_scoped(monkeypatch, tmp_path):
    # The global `s` sync key reconciles *every* repo — its kick passes
    # only_repo=None, unlike the per-row keys which scope to the cursor row.
    _seed_one_worktree(monkeypatch, tmp_path)
    app, calls = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.8)
        before = calls["slow"]
        await pilot.press("s")
        await pilot.pause(0.6)
    assert calls["slow"] > before
    assert calls["only_repo"][-1] is None  # full reconcile, not scoped


async def test_mute_key_unmutes_muted_pr(monkeypatch, tmp_path):
    from cockpit.lib.nudges import NudgePref

    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.read_text", lambda *a, **k: "123")
    monkeypatch.setattr(
        "cockpit.tui.app.load_pref",
        lambda pr: NudgePref(muted=True),
    )
    saved: list = []
    monkeypatch.setattr(
        "cockpit.tui.app.save_pref", lambda pr, pref: saved.append((pr, pref))
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause(0.6)
    assert len(saved) == 1
    pr, pref = saved[0]
    assert pr == 123
    assert not pref.muted  # cleared → unmuted


async def test_mute_key_noop_when_no_pr(monkeypatch, tmp_path):
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.read_text", lambda *a, **k: "")
    saved: list = []
    monkeypatch.setattr(
        "cockpit.tui.app.save_pref", lambda pr, pref: saved.append((pr, pref))
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause(0.6)
    assert saved == []  # no PR on this row → nothing written


async def test_nudge_key_sends_when_idle(monkeypatch, tmp_path):
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.is_cmux", lambda: True)
    calls: list[tuple[str, str]] = []

    def _fake_nudge(ref, msg, **k):
        calls.append((ref, msg))
        return True

    monkeypatch.setattr("cockpit.tui.app.nudge_if_idle", _fake_nudge)
    toasts: list[str] = []
    app, _ = _make_app()
    monkeypatch.setattr(app, "notify", lambda msg, **k: toasts.append(msg))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("N")
        await pilot.pause(0.6)
    assert len(calls) == 1
    ref, _msg = calls[0]
    assert ref == "ws1"  # resolved cwd→path workspace ref
    assert any("nudged" in t for t in toasts)


async def test_nudge_key_skips_when_not_idle(monkeypatch, tmp_path):
    # nudge_if_idle returns False when the session is busy / awaiting permission
    # / parked — the manual nudge must report a skip, never a forced send.
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.is_cmux", lambda: True)
    monkeypatch.setattr("cockpit.tui.app.nudge_if_idle", lambda ref, msg, **k: False)
    toasts: list[str] = []
    app, _ = _make_app()
    monkeypatch.setattr(app, "notify", lambda msg, **k: toasts.append(msg))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("N")
        await pilot.pause(0.6)
    assert any("skipped" in t for t in toasts)


async def test_nudge_key_noop_on_limux(monkeypatch, tmp_path):
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.is_cmux", lambda: False)
    calls: list = []
    monkeypatch.setattr(
        "cockpit.tui.app.nudge_if_idle", lambda ref, msg, **k: calls.append(ref)
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("N")
        await pilot.pause(0.6)
    assert calls == []  # a nudge is a cmux-only `send`


async def test_nudge_key_noop_when_table_empty(monkeypatch):
    calls: list = []
    monkeypatch.setattr("cockpit.tui.app.is_cmux", lambda: True)
    monkeypatch.setattr(
        "cockpit.tui.app.nudge_if_idle", lambda ref, msg, **k: calls.append(ref)
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("N")
        await pilot.pause(0.3)
    assert calls == []


async def test_new_key_opens_text_box(monkeypatch, tmp_path):
    # `n` pushes the new-workspace modal with an input ready for typing.
    from cockpit.tui.widgets.new_workspace_screen import NewWorkspaceScreen

    _seed_one_worktree(monkeypatch, tmp_path)
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, NewWorkspaceScreen)


async def test_new_box_submit_launches_spawn(monkeypatch, tmp_path):
    # Submitting the box fires `cockpit new` via module dispatch detached (cwd =
    # selected row's repo so a bare name routes correctly) with the typed source,
    # then kicks the slow tick so the new worktree surfaces.
    from cockpit.tui.widgets.new_workspace_screen import NewWorkspaceScreen

    wt = _seed_one_worktree(monkeypatch, tmp_path)
    launched: dict = {}

    def fake_popen(cmd, **kwargs):
        launched["cmd"] = cmd
        launched["cwd"] = kwargs.get("cwd")
        return object()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    app, calls = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        before = calls["slow"]
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, NewWorkspaceScreen)
        await pilot.press(*"fix-login")
        await pilot.press("enter")
        await pilot.pause(0.6)
    cmd = launched["cmd"]
    assert cmd[-1] == "fix-login"  # typed source forwarded as the final spawn arg
    # Module dispatch, not `spawn.py` by path (path invocation shadows the
    # `cockpit` package on sys.path[0] → ModuleNotFoundError in the child).
    assert cmd[1:4] == ["-m", "cockpit.cli", "new"]
    assert not any("spawn.py" in str(part) for part in cmd)
    assert launched["cwd"] == str(tmp_path)  # selected row's repo path
    assert calls["slow"] > before  # kicked so the new worktree surfaces


async def test_new_box_cancel_does_not_spawn(monkeypatch, tmp_path):
    # Escape (or blank submit) dismisses without launching spawn.
    _seed_one_worktree(monkeypatch, tmp_path)
    launched: list = []

    def _fake_popen(cmd, **k):
        launched.append(cmd)
        return object()

    monkeypatch.setattr("subprocess.Popen", _fake_popen)
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause(0.4)
    assert launched == []


async def test_new_box_selected_repo_becomes_spawn_cwd(monkeypatch, tmp_path):
    # With multiple repos, the modal's repo Select drives spawn.py's cwd — so a
    # bare name routes to the *chosen* repo, not the cursor row's.
    from textual.widgets import Input, Select

    from cockpit.tui.widgets.new_workspace_screen import NewWorkspaceScreen

    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    wt = Worktree(path=repo_a / "wt-a", branch="khivi/feat-a")
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {
            "repos": [
                {"name": "a", "path": str(repo_a)},
                {"name": "b", "path": str(repo_b)},
            ],
            "check_update": False,
        },
    )
    monkeypatch.setattr("cockpit.tui.app.worktrees", lambda p, prefix="": [wt])
    monkeypatch.setattr("cockpit.tui.app.workspace_cwds", lambda: {"ws1": wt.path})
    monkeypatch.setattr("cockpit.tui.app.workspace_names", lambda: {"ws1": "feat-a"})
    monkeypatch.setattr("cockpit.tui.app.find_pr_payload", lambda *a, **k: None)

    launched: dict = {}
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda cmd, **kw: launched.update(cmd=cmd, cwd=kw.get("cwd")) or object(),
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("a", None, False, [wt])])
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, NewWorkspaceScreen)
        # Pick repo b (cursor row is repo a), then submit a bare name.
        app.screen.query_one(Select).value = str(repo_b)
        app.screen.query_one("#nw-input", Input).value = "fix-login"
        await pilot.press("enter")
        await pilot.pause(0.6)
    assert launched["cmd"][-1] == "fix-login"
    assert launched["cwd"] == str(repo_b)  # chosen repo, not the cursor row's


async def test_new_box_defaults_to_cursor_header_repo(monkeypatch, tmp_path):
    # Cursor resting on a group-header row (current_path() is None there) still
    # preselects that header's repo in the modal — the Select opens on repo b.
    from textual.widgets import Select

    from cockpit.tui.widgets.new_workspace_screen import NewWorkspaceScreen
    from cockpit.tui.widgets.worktree_table import WorktreeTable

    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    wt_a = Worktree(path=repo_a / "wt-a", branch="khivi/feat-a")
    wt_b = Worktree(path=repo_b / "wt-b", branch="khivi/feat-b")
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {
            "repos": [
                {"name": "a", "path": str(repo_a)},
                {"name": "b", "path": str(repo_b)},
            ],
            "check_update": False,
        },
    )
    monkeypatch.setattr("cockpit.tui.app.find_pr_payload", lambda *a, **k: None)

    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("a", None, False, [wt_a]), ("b", None, False, [wt_b])])
        await pilot.pause()
        # Rows: header-a(0), wt-a(1), header-b(2). Park the cursor on header-b.
        table = app.query_one(WorktreeTable)
        table.move_cursor(row=2)
        assert table.current_path() is None  # header row carries no workspace
        assert table.current_repo_name() == "b"
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, NewWorkspaceScreen)
        assert app.screen.query_one(Select).value == str(repo_b)


async def test_update_key_exits_with_restart_code():
    # An available update + `u` exits with the sentinel so cli.py runs the
    # updater and re-execs.
    from cockpit.tui.app import RESTART_EXIT_CODE

    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(HeaderBar).update_text = "0.1 → 0.2"
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
    assert app.return_code == RESTART_EXIT_CODE


async def test_update_key_noop_when_no_update(monkeypatch):
    # No advertised update → `u` is a no-op toast, the daemon keeps running.
    app, _ = _make_app()
    toasts: list[str] = []
    monkeypatch.setattr(app, "notify", lambda m, **k: toasts.append(m))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause(0.3)
        assert app.return_code is None  # still running; no restart requested
    assert any("no update" in t.lower() for t in toasts)


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


async def test_show_full_config_pushes_screen(monkeypatch, tmp_path):
    cfg = {"repos": [{"name": "a", "path": str(tmp_path)}], "check_update": False}
    monkeypatch.setattr("cockpit.tui.app.load_config", lambda: cfg)
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_show_full_config()
        await pilot.pause()
        assert isinstance(app.screen, ConfigScreen)
        assert "check_update" in app.screen._body


async def test_full_config_surfaces_both_themes(monkeypatch):
    # The overlay header shows the current `theme` (dark|light, pills/footer)
    # and the live `tui_theme` (this TUI) — answering "show the current theme".
    cfg = {"repos": [], "check_update": False, "theme": "light", "tui_theme": "nord"}
    monkeypatch.setattr("cockpit.tui.app.load_config", lambda: cfg)
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_show_full_config()
        await pilot.pause()
        body = app.screen._body
        assert "theme" in body and "light" in body
        assert "tui_theme" in body and "nord" in body


async def test_applies_saved_tui_theme_on_mount(monkeypatch):
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {"repos": [], "check_update": False, "tui_theme": "nord"},
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "nord"


async def test_unknown_tui_theme_falls_back_without_crashing(monkeypatch):
    # An unregistered name must not raise (Textual validates App.theme); the app
    # stays on a valid theme.
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {"repos": [], "check_update": False, "tui_theme": "no-such-theme"},
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme in app.available_themes


async def test_theme_change_persists_to_config(monkeypatch):
    # A palette theme pick (modeled by setting app.theme) is written back via
    # save_tui_theme so it survives a restart — Textual itself never persists it.
    saved: list[str] = []
    monkeypatch.setattr("cockpit.tui.app.save_tui_theme", lambda n: saved.append(n))
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.theme = "gruvbox"
        await pilot.pause()
        assert saved == ["gruvbox"]


async def test_open_pr_opens_cached_url(monkeypatch, tmp_path):
    wt = Worktree(path=tmp_path / "wt-a", branch="khivi/feat-a")
    repo = {"name": "repo", "path": str(tmp_path)}
    opened: list[str] = []
    app, _ = _make_app()
    monkeypatch.setattr(app, "_resolve_worktree", lambda p: (repo, wt))
    monkeypatch.setattr(
        "cockpit.tui.app.find_pr_payload",
        lambda branch, name=None: {"url": "https://gh/pr/7", "number": 7},
    )
    monkeypatch.setattr(app, "open_url", lambda url: opened.append(url))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause(0.6)
    assert opened == ["https://gh/pr/7"]


async def test_open_pr_no_pr_warns(monkeypatch, tmp_path):
    wt = Worktree(path=tmp_path / "wt-a", branch="khivi/feat-a")
    opened: list[str] = []
    toasts: list[str] = []
    app, _ = _make_app()
    monkeypatch.setattr(app, "_resolve_worktree", lambda p: ({"name": "r"}, wt))
    monkeypatch.setattr("cockpit.tui.app.find_pr_payload", lambda b, name=None: None)
    monkeypatch.setattr(app, "open_url", lambda url: opened.append(url))
    monkeypatch.setattr(app, "notify", lambda msg, **k: toasts.append(msg))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause(0.6)
    assert opened == []
    assert any("no PR" in t for t in toasts)


async def test_show_output_and_escape_close(monkeypatch):
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._log_tail.append("slow-tick: every 300s")
        await pilot.press("o")
        await pilot.pause()
        assert isinstance(app.screen, ConfigScreen)
        assert "slow-tick" in app.screen._body
        await pilot.press("escape")  # esc closes the overlay
        await pilot.pause()
        assert not isinstance(app.screen, ConfigScreen)


def _patch_edit_config(monkeypatch, app, cfg_path, *, editor_writes):
    """Wire `action_edit_config` to a tmp config + a fake editor + spies.

    Returns (toasts, reset_calls). The fake editor invokes `editor_writes(path)`
    so a test can simulate writing valid / invalid JSON.
    """
    monkeypatch.setattr("cockpit.tui.app.CONFIG_PATH", cfg_path)
    monkeypatch.setattr("cockpit.tui.app.ensure_state_dirs", lambda: None)
    reset_calls = {"n": 0}
    monkeypatch.setattr(
        "cockpit.tui.app.reset_config_cache",
        lambda: reset_calls.__setitem__("n", reset_calls["n"] + 1),
    )
    monkeypatch.setattr(
        subprocess, "run", lambda argv, *a, **k: editor_writes(cfg_path)
    )
    # Suspend tears down the terminal — a no-op in the headless test harness.
    monkeypatch.setattr(app, "suspend", lambda: contextlib.nullcontext())
    toasts: list[str] = []
    monkeypatch.setattr(app, "notify", lambda m, **kw: toasts.append(m))
    return toasts, reset_calls


async def test_edit_config_valid_reloads(monkeypatch, tmp_path):
    # A valid edit drops the config cache (so live-read tick paths see it) and
    # toasts the restart-to-apply hint.
    app, _ = _make_app()
    cfg = tmp_path / "config.json"
    cfg.write_text('{"repos": []}\n')
    toasts, reset_calls = _patch_edit_config(
        monkeypatch,
        app,
        cfg,
        editor_writes=lambda p: p.write_text('{"repos": [{"name": "r"}]}\n'),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_edit_config()
        await pilot.pause()
    assert reset_calls["n"] == 1
    assert any("config saved" in t for t in toasts)


async def test_edit_config_invalid_json_does_not_reload(monkeypatch, tmp_path):
    # A broken edit must NOT drop the cache — the running daemon stays on its
    # last-good in-memory config — and must surface the parse error.
    app, _ = _make_app()
    cfg = tmp_path / "config.json"
    cfg.write_text('{"repos": []}\n')
    toasts, reset_calls = _patch_edit_config(
        monkeypatch,
        app,
        cfg,
        editor_writes=lambda p: p.write_text("{ this is not json"),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_edit_config()
        await pilot.pause()
    assert reset_calls["n"] == 0
    assert any("invalid JSON" in t for t in toasts)


async def test_escape_back_is_noop_on_base_screen():
    # Escape on the main table must not crash or pop the base screen.
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        depth = len(app.screen_stack)
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == depth


async def test_open_ticket_linear_opens_footer_url(monkeypatch, tmp_path):
    # `t` routes through the row's provider (`tickets.provider_for`). For a Linear
    # repo, the provider reads the exact `Linear: [ID](url)` footer link out of
    # the PR body (no hand-constructed URL).
    wt = Worktree(path=tmp_path / "wt-a", branch="khivi/feat-a")
    repo = {"name": "repo", "path": str(tmp_path), "tickets": {"provider": "linear"}}
    opened: list[str] = []
    app, _ = _make_app()
    monkeypatch.setattr(app, "_resolve_worktree", lambda p: (repo, wt))
    monkeypatch.setattr(
        "cockpit.tui.app.find_pr_payload",
        lambda b, name=None: {"number": 7, "linear": {"tickets": [{"id": "PE-9"}]}},
    )
    monkeypatch.setattr(
        "cockpit.lib.tickets.pr_body",
        lambda cwd, num: "Linear: [PE-9](https://linear.app/x/issue/PE-9)",
    )
    monkeypatch.setattr(app, "open_url", lambda url: opened.append(url))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, True, [wt])])
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause(0.6)
    assert opened == ["https://linear.app/x/issue/PE-9"]


async def test_open_ticket_github_opens_issue_url(monkeypatch, tmp_path):
    # For a GitHub-issue repo the provider builds the URL deterministically from
    # the delivered ref + the PR's repo nwo (parsed from the cached PR URL) — no
    # PR-body fetch.
    wt = Worktree(path=tmp_path / "wt-a", branch="khivi/feat-a")
    repo = {"name": "repo", "path": str(tmp_path), "tickets": {"provider": "github"}}
    opened: list[str] = []
    app, _ = _make_app()
    monkeypatch.setattr(app, "_resolve_worktree", lambda p: (repo, wt))
    monkeypatch.setattr(
        "cockpit.tui.app.find_pr_payload",
        lambda b, name=None: {
            "number": 7,
            "url": "https://github.com/ai-needl/repo/pull/7",
            "linear": {"tickets": [{"id": "#42"}]},
        },
    )
    monkeypatch.setattr(app, "open_url", lambda url: opened.append(url))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, True, [wt])])
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause(0.6)
    assert opened == ["https://github.com/ai-needl/repo/issues/42"]


async def test_open_ticket_no_ticket_warns(monkeypatch, tmp_path):
    wt = Worktree(path=tmp_path / "wt-a", branch="khivi/feat-a")
    repo = {"name": "r", "path": str(tmp_path), "tickets": {"provider": "github"}}
    opened: list[str] = []
    toasts: list[str] = []
    app, _ = _make_app()
    monkeypatch.setattr(app, "_resolve_worktree", lambda p: (repo, wt))
    monkeypatch.setattr(
        "cockpit.tui.app.find_pr_payload", lambda b, name=None: {"number": 7}
    )
    monkeypatch.setattr(app, "open_url", lambda url: opened.append(url))
    monkeypatch.setattr(app, "notify", lambda msg, **k: toasts.append(msg))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, True, [wt])])
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause(0.6)
    assert opened == []
    assert any("no ticket" in t for t in toasts)


async def test_footer_hides_update_until_available():
    from cockpit.tui.widgets.footer_bar import FooterBar

    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        footer = app.query_one(FooterBar)
        assert "Update" not in footer.global_text  # hidden with no update
        app._set_update("0.1 → 0.2")
        await pilot.pause()
        assert "Update" in footer.global_text  # revealed once available


async def test_footer_groups_row_keys_left_global_right():
    from cockpit.tui.widgets.footer_bar import FooterBar

    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        footer = app.query_one(FooterBar)
        assert "Focus" in footer.row_text and "Close" in footer.row_text
        assert "Sync" in footer.global_text and "Quit" in footer.global_text
        assert "Focus" not in footer.global_text and "Sync" not in footer.row_text


async def test_footer_merges_close_and_force_into_one_segment():
    # `c` (close) and `C` (force) share a single `c/C Close` slot — both letters
    # stay independently clickable, and there is no standalone "Force" label.
    from cockpit.tui.widgets.footer_bar import FooterBar

    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        rt = app.query_one(FooterBar).row_text
        assert rt.count("Close") == 1  # one combined slot, not two
        assert "app.close_row" in rt and "app.force_close_row" in rt  # both clickable
        # The two click links sit adjacent, joined by `/` → renders as `c/C Close`.
        assert "[/]/[@click=app.force_close_row]" in rt
        assert "Force" not in rt  # folded in, no separate label


async def test_footer_global_group_orders_new_sync_output_first():
    # The global group renders New, Sync, Output in that order regardless of
    # BINDINGS order (FooterBar.GLOBAL_ORDER), with Quit trailing.
    from cockpit.tui.widgets.footer_bar import FooterBar

    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        gt = app.query_one(FooterBar).global_text
        assert (
            gt.index("New") < gt.index("Sync") < gt.index("Output") < gt.index("Quit")
        )


async def test_footer_labels_are_one_word():
    # Verbose binding descriptions collapse to a single curated word; unknown
    # actions fall back to the description's first word.
    from cockpit.tui.widgets.footer_bar import FooterBar

    fb = FooterBar([], backend="cmux")
    assert fb._label("open_pr", "Open PR") == "PR"
    assert fb._label("force_close_row", "Force close") == "Force"
    assert fb._label("sync", "Sync now") == "Sync"
    assert fb._label("whatever", "Multi word thing") == "Multi"


async def test_footer_hides_ticket_when_not_configured():
    from cockpit.tui.widgets.footer_bar import FooterBar

    # _isolate patches load_config → repos with no ticket provider.
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Ticket" not in app.query_one(FooterBar).row_text


async def test_footer_shows_ticket_when_configured(monkeypatch):
    from cockpit.tui.widgets.footer_bar import FooterBar

    # A legacy `linear_keys` repo resolves to the linear provider; the ticket key
    # is enabled for any provider (linear or github) — the compose-time global
    # gate (`show_tickets`) opens, so `t` is no longer globally skipped. (Whether
    # it renders for a *given* row is the separate per-row capability gate,
    # covered by test_footer_gates_row_keys_on_capabilities — asserted here with
    # caps unset to isolate the global gate from the background tick.)
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {"repos": [{"name": "r", "path": "/tmp", "linear_keys": ["PE"]}]},
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        footer = app.query_one(FooterBar)
        assert footer._show_tickets is True
        footer._row_caps = None
        assert not footer._skip("open_ticket")


async def test_footer_gates_row_keys_on_capabilities():
    # Per-row gating: with row caps known, `p`/`m` show only with a PR and `l`
    # only with a ticket. Driven directly via set_row_state (the app pushes these
    # from the highlighted row's `current_capabilities`).
    from cockpit.tui.widgets.footer_bar import FooterBar

    fb = FooterBar(CockpitApp.BINDINGS, show_tickets=True, backend="cmux")
    fb._row_caps = frozenset()
    assert fb._skip("open_pr") and fb._skip("open_ticket") and fb._skip("mute_row")
    fb._row_caps = frozenset({"pr"})
    assert not fb._skip("open_pr") and not fb._skip("mute_row")
    assert fb._skip("open_ticket")
    fb._row_caps = frozenset({"pr", "ticket"})
    assert not fb._skip("open_ticket")


async def test_footer_hides_all_row_keys_on_group_header():
    # A repo group-header row hands the footer the HEADER_CAP sentinel; every
    # row-targeted key hides, global keys stay.
    from cockpit.tui.widgets.footer_bar import FooterBar
    from cockpit.tui.widgets.worktree_table import HEADER_CAP

    fb = FooterBar(CockpitApp.BINDINGS, show_tickets=True, backend="cmux")
    fb._row_caps = frozenset({HEADER_CAP})
    assert all(fb._skip(a) for a in FooterBar.ROW_ACTIONS)
    assert not fb._skip("sync") and not fb._skip("quit")


async def test_footer_mute_label_flips_to_unmute_when_muted():
    from cockpit.tui.widgets.footer_bar import FooterBar

    fb = FooterBar(CockpitApp.BINDINGS, show_tickets=True, backend="cmux")
    fb._row_caps = frozenset({"pr"})
    assert fb._label("mute_row", "Mute") == "Mute"
    fb._row_caps = frozenset({"pr", "muted"})
    assert fb._label("mute_row", "Mute") == "Unmute"


async def test_footer_on_cmux_shows_focus_nudge_hides_open(monkeypatch):
    # cmux backend (the _isolate default): the cmux-only verbs are advertised,
    # and `w`/Open is hidden because `f`/Focus already reaches the workspace.
    from cockpit.tui.widgets.footer_bar import FooterBar

    monkeypatch.setattr("cockpit.tui.app.resolve_tool", lambda: "cmux")
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        rt = app.query_one(FooterBar).row_text
        assert "Focus" in rt and "Nudge" in rt
        assert "Open" not in rt


async def test_footer_on_limux_hides_focus_nudge_shows_open(monkeypatch):
    # limux has no `focus`/notify verb, so Focus and Nudge would no-op — hide
    # their help text. `w`/Open is the only way to reach a workspace, so show it.
    from cockpit.tui.widgets.footer_bar import FooterBar

    monkeypatch.setattr("cockpit.tui.app.resolve_tool", lambda: "limux")
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        rt = app.query_one(FooterBar).row_text
        assert "Focus" not in rt and "Nudge" not in rt
        assert "Open" in rt


async def test_footer_on_no_backend_hides_all_backend_keys(monkeypatch):
    # tool=none: every workspace-backend verb is dead, so none of the three
    # backend-conditional hints render.
    from cockpit.tui.widgets.footer_bar import FooterBar

    monkeypatch.setattr("cockpit.tui.app.resolve_tool", lambda: "none")
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        rt = app.query_one(FooterBar).row_text
        assert "Focus" not in rt and "Nudge" not in rt and "Open" not in rt


async def test_mount_does_not_block_loop_on_update_check(monkeypatch):
    # The startup update check (`_check_update` → `version.latest_version` →
    # `gh api`) must not stall the first paint: it's dispatched off the loop via
    # `@work(thread=True)`. Guard that a *slow* update check leaves the app
    # interactive within the fast-tick horizon anyway. A bounded event stands in
    # for a slow `gh` so a regression (making the check synchronous in on_mount)
    # can't hang the suite. NOTE: this pins the non-blocking-mount invariant only
    # — it does NOT reproduce the `u` self-update freeze, which is a real-TTY /
    # execvp fd-inheritance issue outside the headless PipeDriver's reach.
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {"repos": [], "check_update": True},
    )
    monkeypatch.setattr("cockpit.tui.app.version.running_version", lambda: "0.1")
    release = threading.Event()
    entered = threading.Event()

    def blocking_latest():
        entered.set()
        release.wait(5)  # bounded: a bug can't hang the suite, only slow it
        return "9.9.9"

    monkeypatch.setattr("cockpit.lib.version.latest_version", blocking_latest)

    app, _ = _make_app()
    try:
        start = time.monotonic()
        async with app.run_test() as pilot:
            await pilot.pause()
            ready = time.monotonic() - start
            # Interactive well before the 5s block clears → the check ran on a
            # worker, not the loop. A synchronous on_mount call would push this
            # past 5s (the freeze the re-exec'd TUI shows, in miniature).
            assert ready < 2.0, f"mount blocked {ready:.1f}s on the update check"
            assert entered.is_set()  # the check did start (off-thread)
    finally:
        release.set()
