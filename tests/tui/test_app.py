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

from cockpit.lib.config import apply_org_defaults
from cockpit.lib.git import Worktree
from cockpit.tui.app import CockpitApp
from cockpit.tui.widgets.config_screen import ConfigScreen
from cockpit.tui.widgets.header_bar import HeaderBar
from cockpit.tui.widgets.worktree_table import WorktreeTable

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    # No live config reads; watch.log under a tmp dir (not the developer's real
    # ~/.config/cockpit).
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {"repos": []},
    )
    monkeypatch.setattr("cockpit.tui.app.COCKPIT_HOME", tmp_path)
    # Pin the workspace backend so footer/key tests are deterministic regardless
    # of whether cmux/limux is on PATH (CI has neither → would resolve "none").
    # Backend-specific tests override this.
    monkeypatch.setattr("cockpit.tui.app.resolve_tool", lambda: "cmux")
    # `_cache_repo_name` shells out to `gh repo view` for the PR-cache key; stub
    # it so no test hits the network (the nwo tests re-patch with their own).
    monkeypatch.setattr("cockpit.tui.app.repo_nwo", lambda p: ("acme", Path(p).name))
    # The `cmux events` doorbell would otherwise spawn a real long-lived stream
    # against the developer's running cmux. Doorbell tests call the handler
    # directly; the watcher itself is covered in tests/lib/test_events.py.
    monkeypatch.setattr("cockpit.tui.app.watch_workspace_events", lambda *_: None)


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


async def test_cache_repo_name_uses_nwo_and_memoizes(monkeypatch, tmp_path):
    # The PR-cache key is the git nwo name (what the daemon writes files under),
    # not the arbitrary config `name` label — keying by the label misses every
    # cache file (the Envesya/beta blank-ticket bug). Memoized per path since
    # `repo_nwo` shells out to `gh`.
    app, _ = _make_app()
    repo_path = tmp_path / "beta-checkout"
    repo_path.mkdir()
    calls = {"n": 0}

    def fake_nwo(path):
        calls["n"] += 1
        return ("acme", "beta")

    monkeypatch.setattr("cockpit.tui.app.repo_nwo", fake_nwo)
    repo = {"name": "Envesya", "path": str(repo_path)}
    assert app._cache_repo_name(repo) == "beta"  # nwo, not the "Envesya" label
    assert app._cache_repo_name(repo) == "beta"
    assert calls["n"] == 1  # memoized — one gh call per repo


async def test_cache_repo_name_falls_back_without_caching(monkeypatch, tmp_path):
    # A `gh` failure (off-GitHub repo, transient error) degrades to the path
    # basename and is NOT cached, so a transient failure never pins the wrong key.
    app, _ = _make_app()
    repo_path = tmp_path / "offline-checkout"
    repo_path.mkdir()
    calls = {"n": 0}

    def boom(path):
        calls["n"] += 1
        raise RuntimeError("gh repo view failed")

    monkeypatch.setattr("cockpit.tui.app.repo_nwo", boom)
    repo = {"path": str(repo_path)}
    assert app._cache_repo_name(repo) == repo_path.name  # basename fallback
    assert app._cache_repo_name(repo) == repo_path.name
    assert calls["n"] == 2  # retried — fallback never cached


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
        },
    )
    monkeypatch.setattr(
        "cockpit.tui.app.worktrees", lambda p, prefix="", repo_name="": [wt]
    )

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
        },
    )
    monkeypatch.setattr(
        "cockpit.tui.app.worktrees", lambda p, prefix="", repo_name="": [wt]
    )

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


async def test_run_slow_starts_fast_even_if_publish_raises(monkeypatch):
    # Regression: `_run_slow`'s `finally` used to call `_publish_inventory()`
    # unprotected before `call_from_thread(self._start_fast)` — a failure on
    # the very first slow tick (e.g. a bad worktree read) would raise before
    # `_start_fast` was ever reached, silently stranding the fast-tick loop.
    order: list[str] = []

    def slow(on_repo_done=None, only_repo=None):
        order.append("slow")

    app = CockpitApp(
        slow_tick=slow,
        fast_tick=lambda: order.append("fast"),
        slow_secs=300,
        fast_secs=30,
    )

    def _boom() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(app, "_publish_inventory", _boom)
    async with app.run_test() as pilot:
        await pilot.pause(0.8)
        assert order[0] == "slow"
        assert "fast" in order  # fast still started despite the publish failure
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


async def test_scoped_kick_does_not_reset_header_countdown(monkeypatch):
    # Regression: `_kick_slow` used to reset `_next_slow` unconditionally, but
    # the real cadence is the `set_interval` timer from on_mount (always
    # only_repo=None) — a repo-scoped row-action kick must not desync the
    # header countdown from that timer.
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.6)
        monkeypatch.setattr(app, "_run_slow", lambda only_repo=None: None)

        stale = time.monotonic() + 999
        app._next_slow = stale
        app._slow_phase = "idle"
        app._kick_slow("/some/repo")
        assert app._next_slow == stale  # scoped kick leaves the countdown alone

        app._slow_phase = "idle"
        app._kick_slow(None)
        assert app._next_slow != stale  # full-cycle kick does reset it


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
    # No log pane widget exists; tick output lands in the bounded watch.log.
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
        app._render_table([("repo", "repo", None, False, wts)])
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
        app._render_table([("repo", "repo", None, False, wts)])
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
        },
    )
    monkeypatch.setattr(
        "cockpit.tui.app.worktrees", lambda p, prefix="", repo_name="": [wt]
    )
    monkeypatch.setattr("cockpit.tui.app.workspace_cwds", lambda: {"ws1": wt.path})
    monkeypatch.setattr("cockpit.tui.app.workspace_names", lambda: {"ws1": "feat-a"})
    monkeypatch.setattr("cockpit.tui.app.find_pr_payload", lambda *a, **k: None)
    return wt


async def test_focus_key_focuses_workspace(monkeypatch, tmp_path):
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.resolve_tool", lambda: "cmux")
    refs: list[str] = []
    monkeypatch.setattr(
        "cockpit.tui.app.select_workspace", lambda ref, **k: refs.append(ref)
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "none", [wt])])
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause(0.6)
    assert refs == ["ws1"]


async def test_focus_via_enter_key(monkeypatch, tmp_path):
    # Enter on the focused row selects it → focuses (single click does not).
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.resolve_tool", lambda: "cmux")
    refs: list[str] = []
    monkeypatch.setattr(
        "cockpit.tui.app.select_workspace", lambda ref, **k: refs.append(ref)
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "none", [wt])])
        await pilot.pause()
        app.query_one(WorktreeTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause(0.6)
    assert refs == ["ws1"]


async def test_focus_via_double_click(monkeypatch, tmp_path):
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.resolve_tool", lambda: "cmux")
    refs: list[str] = []
    monkeypatch.setattr(
        "cockpit.tui.app.select_workspace", lambda ref, **k: refs.append(ref)
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "none", [wt])])
        await pilot.pause()
        # Row 1 (y=2 incl. the column header) is the worktree; row 1 is the repo
        # group header.
        await pilot.click(WorktreeTable, offset=(2, 2), times=2)
        await pilot.pause(0.6)
    assert refs == ["ws1"]


async def test_single_click_does_not_focus(monkeypatch, tmp_path):
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.resolve_tool", lambda: "cmux")
    refs: list[str] = []
    monkeypatch.setattr(
        "cockpit.tui.app.select_workspace", lambda ref, **k: refs.append(ref)
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "none", [wt])])
        await pilot.pause()
        await pilot.click(WorktreeTable, offset=(2, 1))
        await pilot.pause(0.4)
    assert refs == []  # single click only moves the cursor


async def test_focus_existing_does_not_select_on_limux(monkeypatch, tmp_path):
    # limux has no select verb: `f` on a row that already has a workspace just
    # reports it's open — it never spawns a duplicate and never selects.
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.resolve_tool", lambda: "limux")
    refs: list[str] = []
    monkeypatch.setattr(
        "cockpit.tui.app.select_workspace", lambda ref, **k: refs.append(ref)
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "none", [wt])])
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause(0.6)
    assert refs == []


def _patch_focus(monkeypatch, *, backend, has_ws):
    """Wire `f`'s leaves: `resolve_tool` → backend, `workspace_cwds`/`names` so
    the row's worktree either already has a workspace (`has_ws`) or not, and
    capturing stubs for both spawn helpers + `select_workspace`. `f` is the one
    "focus, spawning if missing" verb, so these cover the whole open path."""
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


async def _press_focus(app, wt):
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "none", [wt])])
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause(0.6)


async def test_focus_spawns_orphan_when_missing(monkeypatch, tmp_path):
    # No workspace + no cached PR → `f` spawns an orphan workspace, then focuses.
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    cap, _ = _patch_focus(monkeypatch, backend="cmux", has_ws=False)
    app, _ = _make_app()
    await _press_focus(app, wt)
    assert cap["orphan"] == [wt.branch]
    assert cap["pr"] == []
    assert cap["select"] == ["ws2"]


async def test_focus_spawns_pr_when_payload(monkeypatch, tmp_path):
    # No workspace but a cached PR → reconstruct it and spawn a PR workspace.
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    cap, _ = _patch_focus(monkeypatch, backend="cmux", has_ws=False)
    monkeypatch.setattr(
        "cockpit.tui.app.find_pr_payload",
        lambda *a, **k: {"number": 42, "title": "t", "branch": wt.branch},
    )
    monkeypatch.setattr("cockpit.tui.app.load_pref", lambda n: None)
    app, _ = _make_app()
    await _press_focus(app, wt)
    assert cap["pr"] == [42]
    assert cap["orphan"] == []
    assert cap["select"] == ["ws2"]


async def test_focus_spawns_without_select_on_limux(monkeypatch, tmp_path):
    # limux can spawn but not select — `f` creates the workspace, never focuses.
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    cap, _ = _patch_focus(monkeypatch, backend="limux", has_ws=False)
    app, _ = _make_app()
    await _press_focus(app, wt)
    assert cap["orphan"] == [wt.branch]
    assert cap["select"] == []


async def test_focus_noop_when_tool_none(monkeypatch, tmp_path):
    # tool=none → no backend, so `f` neither spawns nor focuses.
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    cap, _ = _patch_focus(monkeypatch, backend="none", has_ws=False)
    app, _ = _make_app()
    await _press_focus(app, wt)
    assert cap["orphan"] == [] and cap["pr"] == [] and cap["select"] == []


async def test_focus_no_worktree_repo_switches_by_repo_name(monkeypatch, tmp_path):
    # A `use_worktree: false` repo's checkout can host several sessions rooted at
    # the same cwd, so `f` there resolves the session by REPO NAME, not cwd —
    # switching to a workspace named after the repo even when the cwd match would
    # miss.
    wt = Worktree(path=tmp_path, branch="master")
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {
            "repos": [{"name": "myrepo", "path": str(tmp_path), "use_worktree": False}],
        },
    )
    monkeypatch.setattr(
        "cockpit.tui.app.worktrees", lambda p, prefix="", repo_name="": [wt]
    )
    monkeypatch.setattr("cockpit.tui.app.find_pr_payload", lambda *a, **k: None)
    monkeypatch.setattr("cockpit.tui.app.resolve_tool", lambda: "cmux")
    # The repo-named workspace lives at a DIFFERENT cwd, so a cwd match misses;
    # only the name match ("myrepo") can find it.
    monkeypatch.setattr(
        "cockpit.tui.app.workspace_cwds", lambda: {"wsX": Path("/elsewhere")}
    )
    monkeypatch.setattr("cockpit.tui.app.workspace_names", lambda: {"wsX": "myrepo"})
    refs: list[str] = []
    monkeypatch.setattr(
        "cockpit.tui.app.select_workspace", lambda ref, **k: refs.append(ref)
    )
    spawned: list = []
    monkeypatch.setattr(
        "cockpit.tui.app.spawn_orphan_workspace", lambda wt, **k: spawned.append(wt)
    )
    app, _ = _make_app()
    await _press_focus(app, wt)
    assert refs == ["wsX"]
    assert spawned == []  # switched to the existing named session, no spawn


async def test_close_key_enqueues_when_clean(monkeypatch, tmp_path):
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.resolve_pr_state", lambda *a, **k: ("", None))
    enq: list = []
    monkeypatch.setattr("cockpit.tui.app.enqueue", lambda req: enq.append(req))
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "none", [wt])])
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
        app._render_table([("repo", "repo", None, "none", [wt])])
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
        app._render_table([("repo", "repo", None, "none", [wt])])
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
        app._render_table([("repo", "repo", None, "none", [wt])])
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

    def _spy_blockers(
        path, *, branch=None, is_mine=True, pr_merged=False, is_primary=False
    ):
        seen.append(pr_merged)
        # Mirror the real gate: a merged PR skips the unpushed check.
        return [] if pr_merged else ["3 unpushed commit(s)"]

    monkeypatch.setattr("cockpit.tui.app.worktree_state_blockers", _spy_blockers)
    enq: list = []
    monkeypatch.setattr("cockpit.tui.app.enqueue", lambda req: enq.append(req))
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "none", [wt])])
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause(0.6)
    assert seen == [True]  # MERGED flowed into the hard gate
    assert len(enq) == 1
    assert enq[0].delete_branch is True  # merged → local ref is reaped


async def test_focus_shows_notification(monkeypatch, tmp_path):
    # The log pane is removed, so a toast is the only on-screen feedback.
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.resolve_tool", lambda: "cmux")
    monkeypatch.setattr("cockpit.tui.app.select_workspace", lambda ref, **k: None)
    toasts: list[str] = []
    app, _ = _make_app()
    monkeypatch.setattr(app, "notify", lambda msg, **k: toasts.append(msg))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "none", [wt])])
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
        app._render_table([("repo", "repo", None, "none", [wt])])
        await pilot.pause()
        before = calls["slow"]
        await pilot.press("m")
        await pilot.pause(0.6)
    assert len(saved) == 1
    key, pref = saved[0]
    # Per-repo key, not a bare "123" — PR numbers collide across repos, and a
    # shared file would mute every repo's #123 at once.
    assert key == f"{tmp_path.name}__123"
    assert pref.muted  # muted
    assert calls["slow"] > before  # kicks the slow tick to republish pr-muted
    # The kick is scoped to the row's repo path, not a full all-repos reconcile,
    # so the line refreshes without round-tripping `gh` for every other repo.
    assert calls["only_repo"][-1] == str(Path(tmp_path))


async def test_snooze_key_clears_a_mute_and_snapshots_the_wake_state(
    monkeypatch, tmp_path
):
    # `z` on a muted row supersedes the mute: mute wins everywhere it's read, so
    # leaving it set would swallow both the 💤 and the snooze's wake. The wake
    # snapshots (review activity + the PR's current issue) come off the cached
    # payload — no `gh` from the TUI.
    from cockpit.lib.nudges import NudgePref

    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.read_text", lambda *a, **k: "123")
    monkeypatch.setattr(
        "cockpit.tui.app.load_pref", lambda pr: NudgePref(muted=True, until=1.0)
    )
    monkeypatch.setattr(
        "cockpit.tui.app.find_pr_payload",
        lambda branch, repo: {"total": 2, "review": "APPROVED", "nudge": "ci"},
    )
    saved: list = []
    monkeypatch.setattr(
        "cockpit.tui.app.save_pref", lambda pr, pref: saved.append((pr, pref))
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "none", [wt])])
        await pilot.pause()
        await pilot.press("z")
        await pilot.pause(0.6)
    assert len(saved) == 1
    key, pref = saved[0]
    assert key == f"{tmp_path.name}__123"  # per-repo, not a bare "123"
    assert pref.snoozed
    assert not pref.muted and pref.until is None
    assert pref.wake_on == "2|APPROVED"
    assert pref.wake_nudge == "ci"  # already failing → this issue won't re-wake it


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
        app._render_table([("repo", "repo", None, "none", [wt])])
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause(0.6)
    assert len(saved) == 1
    key, pref = saved[0]
    assert key == f"{tmp_path.name}__123"
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
        app._render_table([("repo", "repo", None, "none", [wt])])
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
        app._render_table([("repo", "repo", None, "none", [wt])])
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
        app._render_table([("repo", "repo", None, "none", [wt])])
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
        app._render_table([("repo", "repo", None, "none", [wt])])
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
        app._render_table([("repo", "repo", None, "none", [wt])])
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
        },
    )
    monkeypatch.setattr(
        "cockpit.tui.app.worktrees", lambda p, prefix="", repo_name="": [wt]
    )
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
        app._render_table([("a", "a", None, "none", [wt])])
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


async def test_new_box_sinks_parked_repos_and_unhides_on_spawn(monkeypatch, tmp_path):
    # A parked repo stays offered in the modal's picker, but sorts below the live
    # ones and is labelled `(hidden)`. Picking it is a deliberate un-park: the
    # repo has to be live for the kicked cycle to reconcile the new worktree at
    # all, since `cycle_all` skips parked repos.
    from textual.widgets import Input, Select

    from cockpit.lib.hidden import is_hidden, toggle_hidden
    from cockpit.tui.widgets.new_workspace_screen import NewWorkspaceScreen

    repo_a, repo_b = tmp_path / "a", tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    wt = Worktree(path=repo_a / "wt-a", branch="khivi/feat-a")
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {
            "repos": [
                {"name": "b", "path": str(repo_b)},  # parked, but listed first
                {"name": "a", "path": str(repo_a)},
            ],
        },
    )
    monkeypatch.setattr(
        "cockpit.tui.app.worktrees", lambda p, prefix="", repo_name="": [wt]
    )
    monkeypatch.setattr("cockpit.tui.app.workspace_cwds", lambda: {})
    monkeypatch.setattr("cockpit.tui.app.find_pr_payload", lambda *a, **k: None)
    launched: dict = {}
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda cmd, **kw: launched.update(cmd=cmd, cwd=kw.get("cwd")) or object(),
    )
    toggle_hidden(repo_b)

    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("a", "a", None, "none", [wt])])
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, NewWorkspaceScreen)
        screen = app.screen
        # Config order puts b first; parked sinks it below a, dimmed + labelled.
        assert [p for _n, p in screen._repos] == [str(repo_a), str(repo_b)]
        assert screen._option_label("b", str(repo_b)).endswith(
            NewWorkspaceScreen.HIDDEN_SUFFIX
        )
        screen.query_one(Select).value = str(repo_b)
        screen.query_one("#nw-input", Input).value = "fix-login"
        await pilot.press("enter")
        await pilot.pause(0.6)
    assert launched["cwd"] == str(repo_b)
    assert not is_hidden(repo_b)  # spawning there un-parks it


async def test_h_parks_repo(monkeypatch, tmp_path):
    # `h` parks the cursor row's whole repo (persisted via lib/hidden) — it drops
    # out of the inventory entirely, even once revealed (a parked repo is dormant:
    # revealing it must not cost a `git worktree list`).
    from cockpit.lib.hidden import is_hidden

    alpha, beta = tmp_path / "alpha", tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    awt = Worktree(path=alpha / "wt", branch="khivi/feat")
    bwt = Worktree(path=beta / "wt", branch="khivi/other")
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {
            "repos": [
                {"name": "alpha", "path": str(alpha)},
                {"name": "beta", "path": str(beta)},
            ]
        },
    )
    monkeypatch.setattr(
        "cockpit.tui.app.worktrees",
        lambda p, prefix="", repo_name="": [awt if Path(p) == alpha else bwt],
    )
    # Parking closes the repo's workspaces — keep that off real cmux here.
    monkeypatch.setattr("cockpit.tui.app.workspace_cwds", lambda: {})
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table(
            [
                ("alpha", "alpha", None, "none", [awt]),
                ("beta", "beta", None, "none", [bwt]),
            ]
        )
        await pilot.pause()
        # Cursor auto-skips the header onto alpha's worktree row; `h` is
        # repo-scoped, so it parks alpha (not just that row).
        await pilot.press("h")
        await pilot.pause()
    assert is_hidden(alpha) and not is_hidden(beta)
    assert [n for n, *_ in app._gather_inventory(set())] == ["beta"]
    assert app._hidden_names() == {"alpha"}
    app._show_hidden = True
    assert [n for n, *_ in app._gather_inventory(set())] == ["beta"]


async def test_parking_closes_the_repos_workspaces(monkeypatch, tmp_path):
    # Hiding the TUI row while the repo's workspaces stay in the cmux sidebar
    # would just move the clutter, so parking closes them — but never the
    # daemon's own workspace, never a busy one, and never another repo's.
    repo, other = tmp_path / "repo", tmp_path / "other"
    repo.mkdir()
    other.mkdir()
    wt = Worktree(path=tmp_path / "repo-feat", branch="khivi/feat")
    wt.path.mkdir()
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {"repos": [{"name": "repo", "path": str(repo)}]},
    )
    monkeypatch.setattr("cockpit.tui.app.worktrees", lambda p, prefix="", **k: [wt])
    monkeypatch.setattr(
        "cockpit.tui.app.workspace_cwds",
        lambda: {
            "workspace:1": repo,  # the checkout itself
            "workspace:2": wt.path,  # a sibling worktree — matched by cwd
            "workspace:3": repo,  # busy → spared
            "workspace:4": other,  # another repo → untouched
            "workspace:9": repo,  # the daemon's own → never closed
        },
    )
    monkeypatch.setattr(
        "cockpit.tui.app.workspace_is_idle", lambda ref: ref != "workspace:3"
    )
    closed: list[str] = []

    def _close(ref: str) -> bool:
        closed.append(ref)
        return True

    monkeypatch.setattr("cockpit.tui.app.cmux_close_workspace_best_effort", _close)
    app, _ = _make_app()
    app._self_ws = "workspace:9"
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "none", [wt])])
        await pilot.pause()
        await pilot.press("h")
        await pilot.pause(0.5)
    assert sorted(closed) == ["workspace:1", "workspace:2"]


async def test_unparking_closes_nothing(monkeypatch, tmp_path):
    # `h` on an already-parked repo un-parks it — a pure display change; it must
    # not touch the sidebar (and certainly not close what it never opened).
    from cockpit.lib.hidden import toggle_hidden

    repo = tmp_path / "repo"
    repo.mkdir()
    wt = Worktree(path=repo, branch="master")
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {"repos": [{"name": "repo", "path": str(repo)}]},
    )
    monkeypatch.setattr("cockpit.tui.app.worktrees", lambda p, prefix="", **k: [wt])
    monkeypatch.setattr("cockpit.tui.app.workspace_cwds", lambda: {"workspace:1": repo})
    monkeypatch.setattr("cockpit.tui.app.workspace_is_idle", lambda ref: True)
    closed: list[str] = []

    def _close(ref: str) -> bool:
        closed.append(ref)
        return True

    monkeypatch.setattr("cockpit.tui.app.cmux_close_workspace_best_effort", _close)
    toggle_hidden(repo)  # already parked
    app, _ = _make_app()
    app._show_hidden = True
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "none", [wt])], None, {"repo"})
        await pilot.pause()
        await pilot.press("h")
        await pilot.pause(0.5)
    assert closed == []


async def test_h_on_group_header_parks_that_repo(monkeypatch, tmp_path):
    # Row keys are all suppressed on a group header — `h` is global precisely so
    # it still works there (and it resolves the header's own repo).
    from cockpit.lib.hidden import is_hidden

    repo = tmp_path / "solo"
    repo.mkdir()
    wt = Worktree(path=repo / "wt", branch="khivi/feat")
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {"repos": [{"name": "solo", "path": str(repo)}]},
    )
    monkeypatch.setattr("cockpit.tui.app.workspace_cwds", lambda: {})
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("solo", "solo", None, "none", [wt])])
        await pilot.pause()
        app.query_one(WorktreeTable).move_cursor(row=0)  # the group header
        await pilot.press("h")
        await pilot.pause()
    assert is_hidden(repo)


async def test_parked_repos_collapse_into_one_disclosure_row():
    from cockpit.tui.widgets.footer_bar import FooterBar
    from cockpit.tui.widgets.worktree_table import HIDDEN_ROW_KEY

    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        wt = Worktree(path=Path("/tmp/a"), branch="khivi/feat")
        app._render_table(
            [("alpha", "alpha", None, "none", [wt])], None, {"beta", "gamma"}
        )
        await pilot.pause()
        table = app.query_one(WorktreeTable)
        # header + worktree + ONE line for both parked repos (not one each).
        assert table.row_count == 3
        assert HIDDEN_ROW_KEY in table._row_caps
        # It's a header-ish row: no path, so every row action no-ops there.
        table.move_cursor(row=2)
        assert table.current_path() is None
        await pilot.pause()
        # `h` reads as Reveal there, not Hide — one key, labelled by the row.
        assert "Reveal" in app.query_one(FooterBar).global_text


async def test_no_disclosure_row_when_nothing_parked():
    from cockpit.tui.widgets.footer_bar import FooterBar
    from cockpit.tui.widgets.worktree_table import HIDDEN_ROW_KEY

    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        wt = Worktree(path=Path("/tmp/a"), branch="khivi/feat")
        app._render_table([("alpha", "alpha", None, "none", [wt])])
        await pilot.pause()
        table = app.query_one(WorktreeTable)
        assert table.row_count == 2
        assert HIDDEN_ROW_KEY not in table._row_caps
        table.move_cursor(row=0)  # the group header — where `h` is advertised
        await pilot.pause()
        assert "Hide" in app.query_one(FooterBar).global_text


async def test_expanded_disclosure_row_lists_parked_repos(monkeypatch, tmp_path):
    # `h` on the disclosure row expands it in place: the parked repos render as
    # dim name rows under it (no worktrees — they're dormant), and `h` on one of
    # those un-parks it. The whole loop is one key.
    from cockpit.lib.hidden import is_hidden, toggle_hidden
    from cockpit.tui.widgets.footer_bar import FooterBar
    from cockpit.tui.widgets.worktree_table import HIDDEN_ROW_KEY

    alpha, beta = tmp_path / "alpha", tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    awt = Worktree(path=alpha / "wt", branch="khivi/feat")
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {
            "repos": [
                {"name": "alpha", "path": str(alpha)},
                {"name": "beta", "path": str(beta)},
            ]
        },
    )
    monkeypatch.setattr("cockpit.tui.app.worktrees", lambda p, prefix="", **k: [awt])
    monkeypatch.setattr("cockpit.tui.app.workspace_cwds", lambda: {})
    toggle_hidden(beta)
    app, _ = _make_app()
    # `h` re-renders through the real `_publish_inventory` (`_prime_table`), which
    # `_make_app` stubs out — restore the class method so expansion actually paints.
    del app._publish_inventory
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("alpha", "alpha", None, "none", [awt])], None, {"beta"})
        await pilot.pause()
        table = app.query_one(WorktreeTable)
        assert table.row_count == 3  # alpha header + row + collapsed disclosure
        table.move_cursor(row=2)
        await pilot.press("h")  # expand
        await pilot.pause(0.5)
        assert app._show_hidden
        # A parked repo stays out of the inventory even revealed — its row comes
        # from the name set, so revealing costs no `git worktree list`.
        assert [n for n, *_ in app._gather_inventory(set())] == ["alpha"]
        assert table.row_count == 4
        assert table._current_row_key() == HIDDEN_ROW_KEY  # cursor stays put
        assert "(hidden)" in str(table.get_row_at(3)[0])
        # Down onto the revealed repo: `h` there reads Unhide, and un-parks it.
        table.move_cursor(row=3)
        await pilot.pause()
        assert "Unhide" in app.query_one(FooterBar).global_text
        await pilot.press("h")
        await pilot.pause(0.5)
    assert not is_hidden(beta)


async def test_gather_inventory_hides_workspaceless_no_worktree_row(
    monkeypatch, tmp_path
):
    # A `use_worktree: false` repo with no live workspace shows only its group
    # header — the branch row is unactionable, and `n` starts one on demand. With
    # a workspace open the row comes back. A normal (worktree-managed) repo is
    # never filtered.
    scratch, managed = tmp_path / "scratch", tmp_path / "managed"
    scratch.mkdir()
    managed.mkdir()
    swt = Worktree(path=scratch, branch="master")
    mwt = Worktree(path=managed, branch="feature")
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {
            "repos": [
                {"name": "scratch", "path": str(scratch), "use_worktree": False},
                {"name": "managed", "path": str(managed)},
            ]
        },
    )
    monkeypatch.setattr(
        "cockpit.tui.app.worktrees",
        lambda p, prefix="", repo_name="": [swt if Path(p) == scratch else mwt],
    )
    app, _ = _make_app()
    assert [(name, wts) for name, _, _, _, wts in app._gather_inventory(set())] == [
        ("scratch", []),
        ("managed", [mwt]),
    ]
    live = {scratch.resolve()}
    assert [(name, wts) for name, _, _, _, wts in app._gather_inventory(live)] == [
        ("scratch", [swt]),
        ("managed", [mwt]),
    ]


async def test_gather_inventory_clumps_an_orgs_repos(monkeypatch, tmp_path):
    # Repos sharing an `org` render adjacent even when `cockpit new` appended
    # them interleaved, and each carries the org's merged sidebar_color — that
    # pair is what makes an org read as one block in the table.
    paths = {}
    for name in ("acme-1", "solo", "acme-2"):
        paths[name] = tmp_path / name
        paths[name].mkdir()
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: apply_org_defaults(
            {
                "repos": [
                    {"name": "acme-1", "path": str(paths["acme-1"]), "org": "acme"},
                    {"name": "solo", "path": str(paths["solo"])},
                    {"name": "acme-2", "path": str(paths["acme-2"]), "org": "acme"},
                ],
                "orgs": {"acme": {"sidebar_color": "Magenta"}},
            }
        ),
    )
    monkeypatch.setattr(
        "cockpit.tui.app.worktrees", lambda p, prefix="", repo_name="": []
    )
    app, _ = _make_app()
    assert [(name, color) for name, _, color, _, _ in app._gather_inventory(set())] == [
        ("acme-1", "Magenta"),
        ("acme-2", "Magenta"),
        ("solo", None),
    ]


async def test_new_box_no_worktree_repo_spawns_named_checkout(monkeypatch, tmp_path):
    # `n` on a `use_worktree: false` repo → one named workspace on the checkout:
    # `cockpit new --cwd <path> --name <name>`, no worktree. The name prefills to
    # the repo name and rides through to `--name`.
    from textual.widgets import Input

    from cockpit.tui.widgets.new_workspace_screen import NewWorkspaceScreen

    repo = tmp_path / "scratch"
    repo.mkdir()
    wt = Worktree(path=repo, branch="master")
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {
            "repos": [{"name": "scratch", "path": str(repo), "use_worktree": False}],
        },
    )
    monkeypatch.setattr(
        "cockpit.tui.app.worktrees", lambda p, prefix="", repo_name="": [wt]
    )
    monkeypatch.setattr("cockpit.tui.app.workspace_cwds", lambda: {})
    monkeypatch.setattr("cockpit.tui.app.workspace_names", lambda: {})
    monkeypatch.setattr("cockpit.tui.app.find_pr_payload", lambda *a, **k: None)

    launched: dict = {}
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda cmd, **kw: launched.update(cmd=cmd, cwd=kw.get("cwd")) or object(),
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("scratch", "scratch", None, "none", [wt])])
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, NewWorkspaceScreen)
        # Name prefilled to the repo name; accept it as-is.
        assert app.screen.query_one("#nw-input", Input).value == "scratch"
        await pilot.press("enter")
        await pilot.pause(0.6)
    cmd = launched["cmd"]
    assert "--cwd" in cmd and cmd[cmd.index("--cwd") + 1] == str(repo)
    assert "--name" in cmd and cmd[cmd.index("--name") + 1] == "scratch"
    assert launched["cwd"] == str(repo)


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
        },
    )
    monkeypatch.setattr("cockpit.tui.app.find_pr_payload", lambda *a, **k: None)

    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table(
            [("a", "a", None, "none", [wt_a]), ("b", "b", None, "none", [wt_b])]
        )
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


async def test_double_click_header_opens_new_modal(monkeypatch, tmp_path):
    # Double-clicking a repo group-header row opens the new-workspace modal for
    # that repo (a header has no workspace to focus, so its action is `n`).
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
        },
    )
    monkeypatch.setattr("cockpit.tui.app.find_pr_payload", lambda *a, **k: None)

    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table(
            [("a", "a", None, "none", [wt_a]), ("b", "b", None, "none", [wt_b])]
        )
        await pilot.pause()
        table = app.query_one(WorktreeTable)
        table.move_cursor(row=2)  # header-b
        assert table.current_path() is None  # header carries no workspace
        table.on_click(type("Ev", (), {"chain": 2})())  # simulate double-click
        await pilot.pause()
        assert isinstance(app.screen, NewWorkspaceScreen)
        assert app.screen.query_one(Select).value == str(repo_b)


async def test_single_click_on_hidden_row_expands():
    # A disclosure triangle that needs a double-click doesn't read as one, so the
    # hidden row is the one row a *single* click acts on — from wherever the
    # cursor happens to be. Drive a REAL click through the Pilot with the cursor
    # parked on another row: `on_click` is dispatched before DataTable moves the
    # row cursor, so a cursor-only read would need a second click to see it.
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        wt = Worktree(path=Path("/tmp/a"), branch="khivi/feat")
        app._render_table([("alpha", "alpha", None, "none", [wt])], None, {"beta"})
        await pilot.pause()
        table = app.query_one(WorktreeTable)
        table.move_cursor(row=0)  # cursor elsewhere — do NOT pre-select the row
        # y: header at 0, group header at 1, worktree at 2, `▸ 1 hidden` at 3.
        await pilot.click(WorktreeTable, offset=(2, 3))
        await pilot.pause()
        assert app._show_hidden


async def test_enter_on_hidden_row_expands():
    # Enter is Focus on every other row; on the disclosure row (no path to
    # focus) it opens the section instead.
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        wt = Worktree(path=Path("/tmp/a"), branch="khivi/feat")
        app._render_table([("alpha", "alpha", None, "none", [wt])], None, {"beta"})
        await pilot.pause()
        table = app.query_one(WorktreeTable)
        table.focus()
        table.move_cursor(row=2)  # the `▸ 1 hidden` row
        await pilot.press("enter")
        await pilot.pause()
        assert app._show_hidden


async def test_arrow_keys_move_row_cursor():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        wts = [
            Worktree(path=Path("/tmp/a"), branch="khivi/feat-a"),
            Worktree(path=Path("/tmp/b"), branch="khivi/feat-b"),
            Worktree(path=Path("/tmp/c"), branch="khivi/feat-c"),
        ]
        app._render_table([("repo", "repo", None, False, wts)])
        await pilot.pause()
        table = app.query_one(WorktreeTable)
        table.focus()
        await pilot.pause()
        start = table.cursor_row
        await pilot.press("down")
        await pilot.pause()
        assert table.cursor_row == start + 1


async def test_show_full_config_pushes_screen(monkeypatch, tmp_path):
    cfg = {"repos": [{"name": "a", "path": str(tmp_path)}], "use_slack": False}
    monkeypatch.setattr("cockpit.tui.app.load_config", lambda: cfg)
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_show_full_config()
        await pilot.pause()
        assert isinstance(app.screen, ConfigScreen)
        assert "use_slack" in app.screen._body


async def test_full_config_surfaces_both_themes(monkeypatch):
    # The overlay header shows the current `theme` (dark|light, pills/footer)
    # and the live `tui_theme` (this TUI) — answering "show the current theme".
    cfg = {"repos": [], "theme": "light", "tui_theme": "nord"}
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
        lambda: {"repos": [], "tui_theme": "nord"},
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
        lambda: {"repos": [], "tui_theme": "no-such-theme"},
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
        app._render_table([("repo", "repo", None, "none", [wt])])
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause(0.6)
    assert opened == ["https://gh/pr/7"]


async def test_open_pr_no_pr_warns(monkeypatch, tmp_path):
    wt = Worktree(path=tmp_path / "wt-a", branch="khivi/feat-a")
    opened: list[str] = []
    toasts: list[str] = []
    app, _ = _make_app()
    monkeypatch.setattr(
        app, "_resolve_worktree", lambda p: ({"name": "r", "path": str(tmp_path)}, wt)
    )
    monkeypatch.setattr("cockpit.tui.app.find_pr_payload", lambda b, name=None: None)
    monkeypatch.setattr(app, "open_url", lambda url: opened.append(url))
    monkeypatch.setattr(app, "notify", lambda msg, **k: toasts.append(msg))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "none", [wt])])
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
        lambda b, name=None: {"number": 7, "ticket": {"tickets": [{"id": "PE-9"}]}},
    )
    monkeypatch.setattr(
        "cockpit.lib.tickets.pr_body",
        lambda cwd, num: "Linear: [PE-9](https://linear.app/x/issue/PE-9)",
    )
    monkeypatch.setattr(app, "open_url", lambda url: opened.append(url))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "linear", [wt])])
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
            "ticket": {"tickets": [{"id": "#42"}]},
        },
    )
    monkeypatch.setattr(app, "open_url", lambda url: opened.append(url))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", "repo", None, "linear", [wt])])
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
        app._render_table([("repo", "repo", None, "linear", [wt])])
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause(0.6)
    assert opened == []
    assert any("no ticket" in t for t in toasts)


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


async def test_footer_global_group_orders_hide_new_sync_output_first():
    # The global group renders Hide, New, Sync, Output in that order regardless
    # of BINDINGS order (FooterBar.GLOBAL_ORDER), with Quit trailing. `h` leads
    # so it sits against the row-key group — it's the repo-targeted key, the most
    # row-adjacent of the globals.
    from cockpit.tui.widgets.footer_bar import FooterBar

    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        gt = app.query_one(FooterBar).global_text
        assert (
            gt.index("Hide")
            < gt.index("New")
            < gt.index("Sync")
            < gt.index("Output")
            < gt.index("Quit")
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

    # The ticket key is enabled for any provider (linear or github) — the
    # compose-time global gate (`show_tickets`) opens, so `t` is no longer
    # globally skipped. (Whether it renders for a *given* row is the separate
    # per-row capability gate, covered by
    # test_footer_gates_row_keys_on_capabilities — asserted here with caps unset
    # to isolate the global gate from the background tick.)
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {
            "repos": [
                {
                    "name": "r",
                    "path": "/tmp",
                    "tickets": {"provider": "linear", "keys": ["PE"]},
                }
            ]
        },
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


async def test_footer_hide_key_shows_only_on_repo_rows():
    # `h` parks a whole repo, so its hint rides the rows that read as a repo —
    # the group header, the `▸ N hidden` disclosure row, a revealed parked repo
    # (all HEADER_CAP) — and hides on a worktree row, where "Hide" would read as
    # "hide this row". Caps unknown (empty table) → full legend, so it shows.
    from cockpit.tui.widgets.footer_bar import FooterBar
    from cockpit.tui.widgets.worktree_table import (
        HEADER_CAP,
        HIDDEN_CAP,
        PARKED_CAP,
    )

    fb = FooterBar(CockpitApp.BINDINGS, show_tickets=True, backend="cmux")
    for caps in (
        frozenset({HEADER_CAP}),
        frozenset({HEADER_CAP, HIDDEN_CAP}),
        frozenset({HEADER_CAP, PARKED_CAP}),
    ):
        fb._row_caps = caps
        assert not fb._skip("hide_repo")
    fb._row_caps = frozenset({"pr", "workspace"})
    assert fb._skip("hide_repo")
    fb._row_caps = None
    assert not fb._skip("hide_repo")


async def test_footer_mute_label_flips_to_unmute_when_muted():
    from cockpit.tui.widgets.footer_bar import FooterBar

    fb = FooterBar(CockpitApp.BINDINGS, show_tickets=True, backend="cmux")
    fb._row_caps = frozenset({"pr"})
    assert fb._label("mute_row", "Mute") == "Mute"
    fb._row_caps = frozenset({"pr", "muted"})
    assert fb._label("mute_row", "Mute") == "Unmute"


async def test_footer_cmux_shows_focus_gates_nudge_on_workspace():
    # cmux: `f`/Focus is the single "focus, spawning if missing" verb, so it
    # shows on any row regardless of workspace presence. `N`/Nudge reaches an
    # *existing* workspace, so it's gated on the `workspace` cap.
    from cockpit.tui.widgets.footer_bar import FooterBar

    fb = FooterBar(CockpitApp.BINDINGS, show_tickets=True, backend="cmux")
    fb._row_caps = frozenset({"workspace"})
    assert not fb._skip("focus_row") and not fb._skip("nudge_row")
    fb._row_caps = frozenset()
    assert not fb._skip("focus_row")  # `f` still shown — it spawns
    assert fb._skip("nudge_row")  # nothing to nudge


async def test_footer_limux_shows_focus_hides_nudge():
    # limux can spawn (so `f` shows — it spawns then the user switches via limux)
    # but has no nudge verb, so `N`/Nudge always hides.
    from cockpit.tui.widgets.footer_bar import FooterBar

    fb = FooterBar(CockpitApp.BINDINGS, show_tickets=True, backend="limux")
    for caps in (frozenset(), frozenset({"workspace"})):
        fb._row_caps = caps
        assert not fb._skip("focus_row")
        assert fb._skip("nudge_row")


async def test_footer_on_no_backend_hides_all_backend_keys():
    # tool=none: every workspace-backend verb is dead (no backend to spawn into
    # or reach), so neither renders regardless of workspace presence.
    from cockpit.tui.widgets.footer_bar import FooterBar

    fb = FooterBar(CockpitApp.BINDINGS, show_tickets=True, backend="none")
    for caps in (frozenset(), frozenset({"workspace"})):
        fb._row_caps = caps
        assert fb._skip("focus_row")
        assert fb._skip("nudge_row")


async def test_footer_hides_close_on_workspaceless_primary_checkout():
    # A primary checkout (a `use_worktree: false` `master`) can only be closed workspace-only;
    # with no workspace there's nothing to close, so `c`/`C` hide. A feature row
    # (no `primary` cap) keeps `c` regardless — it also removes the worktree.
    from cockpit.tui.widgets.footer_bar import FooterBar

    fb = FooterBar(CockpitApp.BINDINGS, show_tickets=True, backend="cmux")
    fb._row_caps = frozenset({"primary"})
    assert fb._skip("close_row") and fb._skip("force_close_row")
    fb._row_caps = frozenset({"primary", "workspace"})
    assert not fb._skip("close_row") and not fb._skip("force_close_row")
    fb._row_caps = frozenset()  # feature row, no workspace
    assert not fb._skip("close_row") and not fb._skip("force_close_row")


async def test_footer_snooze_label_flips_to_wake_when_snoozed():
    from cockpit.tui.widgets.footer_bar import FooterBar

    fb = FooterBar(CockpitApp.BINDINGS, show_tickets=True, backend="cmux")
    fb._row_caps = frozenset({"pr"})
    assert fb._label("snooze_row", "Snooze") == "Snooze"
    fb._row_caps = frozenset({"pr", "snoozed"})
    assert fb._label("snooze_row", "Snooze") == "Wake"


async def test_footer_hides_snooze_on_a_row_without_a_pr():
    from cockpit.tui.widgets.footer_bar import FooterBar

    fb = FooterBar(CockpitApp.BINDINGS, show_tickets=True, backend="cmux")
    fb._row_caps = frozenset({"workspace"})
    assert fb._skip("snooze_row")
    fb._row_caps = frozenset({"workspace", "pr"})
    assert not fb._skip("snooze_row")


async def test_snooze_reads_the_wake_payload_under_the_nwo_key(monkeypatch, tmp_path):
    # `z` builds `wake_on` from the cached PR payload, which the daemon writes
    # under the git *nwo* name — not the config `name` label. Keying by the label
    # ("Envesya" vs repo "beta") found no file, so the snooze was written with the
    # empty-payload signature "0|" and the very next slow tick woke it again.
    from cockpit.lib.nudges import NudgePref

    app, _ = _make_app()
    repo_path = tmp_path / "beta"
    repo_path.mkdir()
    repo = {"name": "Envesya", "path": str(repo_path)}
    wt = Worktree(path=repo_path / "fnox", branch="khivi/fnox")
    seen: list[str] = []
    saved: dict[str, NudgePref] = {}

    def fake_find(branch, repo_name=None):
        seen.append(repo_name)
        # Only the nwo-keyed lookup resolves, exactly like the real cache glob.
        return {"total": 3, "review": "APPROVED"} if repo_name == "beta" else None

    monkeypatch.setattr("cockpit.tui.app.find_pr_payload", fake_find)
    monkeypatch.setattr(
        "cockpit.tui.app.save_pref", lambda key, pref: saved.__setitem__(key, pref)
    )
    monkeypatch.setattr(
        app,
        "_resolve_row_pref",
        lambda p, verb: (repo, wt, 269, "beta__269", NudgePref()),
    )
    monkeypatch.setattr(app, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(app, "call_from_thread", lambda fn, *a, **k: None)

    # `@work` wraps the method; call the undecorated body so the assertions run
    # synchronously instead of racing a worker thread.
    CockpitApp._toggle_snooze.__wrapped__(app, str(wt.path))  # type: ignore[attr-defined]

    assert seen == ["beta"]  # nwo, not the "Envesya" label
    assert saved["beta__269"].snoozed
    assert saved["beta__269"].wake_on == "3|APPROVED"  # not the empty-payload "0|"


async def test_workspace_event_kicks_the_fast_tick():
    # The `cmux events` doorbell: a workspace created/closed out from under us
    # republishes now instead of at the next 30s fast tick.
    app, calls = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.8)
        before = calls["fast"]
        app._on_workspace_event()
        await pilot.pause(0.5)
        assert calls["fast"] == before + 1


async def test_event_during_a_running_fast_tick_is_not_lost():
    # The running tick may have read workspace state *before* the event, so the
    # doorbell owes one more kick once it lands — otherwise a close that races a
    # tick sits stale until the interval.
    started, release = threading.Event(), threading.Event()
    runs: list[int] = []

    def fast() -> None:
        runs.append(1)
        started.set()
        release.wait(5)

    app = CockpitApp(
        slow_tick=lambda on_repo_done=None, only_repo=None: None,
        fast_tick=fast,
        slow_secs=300,
        fast_secs=30,
    )
    app._publish_inventory = lambda: None  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.pause(0.8)
        assert started.wait(5), "first fast tick never started"
        app._on_workspace_event()
        assert app._events_pending  # coalesced, not dropped
        release.set()
        for _ in range(20):
            await pilot.pause(0.1)
            if len(runs) >= 2:
                break
        assert len(runs) >= 2
        assert not app._events_pending


# ── the sidebar X → close ───────────────────────────────────────────────────


async def test_sidebar_x_closes_the_worktree(monkeypatch):
    """Clicking cmux's X is the one close gesture available outside the TUI, so
    it routes to exactly the `c` path — same resolution, same blockers gate."""
    closed: list[tuple[str, dict]] = []
    monkeypatch.setattr("cockpit.tui.app.was_self_closed", lambda _wsid: False)
    app, _ = _make_app()
    app._close_worktree = lambda p, **kw: closed.append((p, kw))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._on_workspace_closed("UUID-9", "/tmp/repo/feat")
        await pilot.pause()

    assert closed == [("/tmp/repo/feat", {"quiet": True})]


async def test_sidebar_x_never_forces(monkeypatch):
    """`C`'s open-PR override is a deliberate second keystroke. The X is one
    click with no modifier, so it must land on the refusing gate, not force."""
    closed: list[tuple[str, dict]] = []
    monkeypatch.setattr("cockpit.tui.app.was_self_closed", lambda _wsid: False)
    app, _ = _make_app()
    app._close_worktree = lambda p, **kw: closed.append((p, kw))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._on_workspace_closed("UUID-9", "/tmp/repo/feat")
        await pilot.pause()

    assert closed[0][1].get("force", False) is False


async def test_cockpit_own_close_never_tears_anything_down(monkeypatch):
    """The load-bearing filter. `h`/park closes workspaces and is documented as
    workspace-only — without this, parking a repo tears down every worktree in
    it. Same for a fold-anchor dissolve, the dead-cwd sweep, and teardown's own
    trailing close."""
    closed: list[str] = []
    monkeypatch.setattr("cockpit.tui.app.was_self_closed", lambda _wsid: True)
    app, _ = _make_app()
    app._close_worktree = lambda p, **kw: closed.append(p)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._on_workspace_closed("UUID-9", "/tmp/repo/feat")
        await pilot.pause()

    assert closed == []


async def test_an_unregistered_workspace_close_is_silent(monkeypatch):
    """A hand-made session or a trailing-fold anchor rooted at $HOME resolves to
    no worktree. That is the common case for this handler, so it must be a
    no-op, not an error toast on every close."""
    notes: list[tuple] = []
    app, _ = _make_app()
    app._notify = lambda msg, severity="information": notes.append((msg, severity))
    async with app.run_test() as pilot:
        await pilot.pause()
        # `quiet=True` is the sidebar-X contract; `_resolve_worktree` finds
        # nothing because the injected config has no repos.
        app._close_worktree("/nowhere/at/all", quiet=True)
        await pilot.pause(0.5)

    assert notes == []


async def test_the_c_key_still_reports_an_unresolvable_path():
    """`quiet` must not leak into the keypress path — pressing `c` on a row
    whose worktree vanished has to say so."""
    notes: list[tuple] = []
    app, _ = _make_app()
    app._notify = lambda msg, severity="information": notes.append((msg, severity))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._close_worktree("/nowhere/at/all")
        await pilot.pause(0.5)

    assert notes and notes[0][1] == "error"


async def test_table_hides_cost_column_when_nothing_reports_cost(monkeypatch):
    """The `$` column is gated on the data, not on config: a machine whose
    Claude Code writes no spend must not grow a permanently blank column."""
    from cockpit.tui.widgets.worktree_table import WorktreeTable

    monkeypatch.setattr("cockpit.tui.app.cost_reporting_available", lambda: False)
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(WorktreeTable)
        assert table._show_cost is False
        assert "$" not in [str(c.label) for c in table.columns.values()]


async def test_table_shows_cost_column_when_spend_is_reported(monkeypatch):
    from cockpit.tui.widgets.worktree_table import WorktreeTable

    monkeypatch.setattr("cockpit.tui.app.cost_reporting_available", lambda: True)
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(WorktreeTable)
        assert table._show_cost is True
        assert [str(c.label) for c in table.columns.values()][-1] == "$"
