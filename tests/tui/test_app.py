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
from pathlib import Path

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


def _make_app(**kw):
    calls = {"slow": 0, "fast": 0}

    def slow(on_repo_done=None):
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

    def slow(on_repo_done=None):
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
            assert table.row_count == 1  # primed without waiting for slow
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

    def slow(on_repo_done=None):
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
        assert table.row_count == 1  # published from the per-repo callback


async def test_fast_starts_only_after_first_slow():
    order: list[str] = []

    def slow(on_repo_done=None):
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
        table.move_cursor(row=1)
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
        await pilot.click(WorktreeTable, offset=(2, 1), times=2)
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


async def test_close_key_enqueues_when_clean(monkeypatch, tmp_path):
    wt = _seed_one_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr("cockpit.tui.app.probe_blockers", lambda *a, **k: [])
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
    monkeypatch.setattr(
        "cockpit.tui.app.probe_blockers", lambda *a, **k: ["PR #1 is OPEN"]
    )
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
    monkeypatch.setattr(
        "cockpit.tui.app.probe_blockers", lambda *a, **k: ["PR #1 is OPEN"]
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


async def test_update_key_exits_with_restart_code():
    # An available update + `u` exits with the sentinel so cockpit.sh updates
    # and relaunches.
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


async def test_show_repo_config_pushes_screen(monkeypatch, tmp_path):
    # The palette command resolves the cursor row's repo and shows its config.
    repo = {"name": "myrepo", "path": str(tmp_path), "branch_prefix": "khivi/"}
    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {"repos": [repo], "check_update": False},
    )
    wt = Worktree(path=tmp_path / "wt-a", branch="khivi/feat-a")
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("myrepo", None, False, [wt])])
        await pilot.pause()
        app.action_show_repo_config()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ConfigScreen)
        assert "myrepo" in screen._title
        assert "branch_prefix" in screen._body


async def test_show_repo_config_no_repo_notifies(monkeypatch):
    # Empty table → no repo to resolve → warn, don't push a screen.
    toasts: list[str] = []
    app, _ = _make_app()
    monkeypatch.setattr(app, "notify", lambda msg, **k: toasts.append(msg))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([])
        await pilot.pause()
        app.action_show_repo_config()
        await pilot.pause()
        assert not isinstance(app.screen, ConfigScreen)
        assert any("no repo" in t for t in toasts)


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


async def test_open_linear_opens_footer_url(monkeypatch, tmp_path):
    wt = Worktree(path=tmp_path / "wt-a", branch="khivi/feat-a")
    repo = {"name": "repo", "path": str(tmp_path)}
    opened: list[str] = []
    app, _ = _make_app()
    monkeypatch.setattr(app, "_resolve_worktree", lambda p: (repo, wt))
    monkeypatch.setattr(
        "cockpit.tui.app.find_pr_payload",
        lambda b, name=None: {"number": 7, "linear": {"tickets": [{"id": "PE-9"}]}},
    )
    monkeypatch.setattr(
        "cockpit.tui.app._pr_body",
        lambda cwd, num: "Linear: [PE-9](https://linear.app/x/issue/PE-9)",
    )
    monkeypatch.setattr(app, "open_url", lambda url: opened.append(url))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause(0.6)
    assert opened == ["https://linear.app/x/issue/PE-9"]


async def test_open_linear_no_ticket_warns(monkeypatch, tmp_path):
    wt = Worktree(path=tmp_path / "wt-a", branch="khivi/feat-a")
    opened: list[str] = []
    toasts: list[str] = []
    app, _ = _make_app()
    monkeypatch.setattr(app, "_resolve_worktree", lambda p: ({"name": "r"}, wt))
    monkeypatch.setattr(
        "cockpit.tui.app.find_pr_payload", lambda b, name=None: {"number": 7}
    )
    monkeypatch.setattr(app, "open_url", lambda url: opened.append(url))
    monkeypatch.setattr(app, "notify", lambda msg, **k: toasts.append(msg))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_table([("repo", None, False, [wt])])
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause(0.6)
    assert opened == []
    assert any("no Linear" in t for t in toasts)


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

    fb = FooterBar([])
    assert fb._label("open_pr", "Open PR") == "PR"
    assert fb._label("force_close_row", "Force close") == "Force"
    assert fb._label("sync", "Sync now") == "Sync"
    assert fb._label("whatever", "Multi word thing") == "Multi"


async def test_footer_hides_linear_when_not_configured():
    from cockpit.tui.widgets.footer_bar import FooterBar

    # _isolate patches load_config → repos with no linear_keys.
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Linear" not in app.query_one(FooterBar).row_text


async def test_footer_shows_linear_when_configured(monkeypatch):
    from cockpit.tui.widgets.footer_bar import FooterBar

    monkeypatch.setattr(
        "cockpit.tui.app.load_config",
        lambda: {"repos": [{"name": "r", "path": "/tmp", "linear_keys": ["PE"]}]},
    )
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Linear" in app.query_one(FooterBar).row_text
