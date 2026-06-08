"""Tests for the CLI→daemon signaling channel: kick/stop signals + close-request queue."""

from __future__ import annotations

import importlib
import json
import os
import signal
import time
from pathlib import Path

import pytest


@pytest.fixture
def signal_mod(tmp_path, monkeypatch):
    """Isolate $COCKPIT_HOME and reload config + daemon_signal so each test starts fresh."""
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cfg

    importlib.reload(cfg)
    import cockpit.lib.daemon_signal as ds

    importlib.reload(ds)
    return ds


# ── close-request queue ─────────────────────────────────────────────────────


def test_enqueue_and_iter_round_trip(signal_mod):
    from cockpit.orchestrators.teardown import TeardownRequest

    req = TeardownRequest(
        ref="workspace:7",
        name="feat-x",
        worktree_path=Path("/tmp/wt"),
        branch="khivi/feat-x",
        repo_path=Path("/tmp/repo"),
        repo_name="needl-ai",
        forced=True,
    )
    path = signal_mod.enqueue(req)
    assert path.exists()

    pending = signal_mod.iter_pending()
    assert len(pending) == 1
    got_path, got_req = pending[0]
    assert got_path == path
    assert got_req.ref == "workspace:7"
    assert got_req.name == "feat-x"
    assert got_req.worktree_path == Path("/tmp/wt")
    assert got_req.branch == "khivi/feat-x"
    assert got_req.repo_name == "needl-ai"
    assert got_req.forced is True


def test_pop_removes_marker(signal_mod):
    from cockpit.orchestrators.teardown import TeardownRequest

    req = TeardownRequest(ref="workspace:1", repo_name="r")
    path = signal_mod.enqueue(req)
    signal_mod.pop(path)
    assert not path.exists()
    assert signal_mod.iter_pending() == []


def test_iter_pending_scoped_by_repo(signal_mod):
    from cockpit.orchestrators.teardown import TeardownRequest

    signal_mod.enqueue(TeardownRequest(ref="workspace:1", repo_name="repo-a"))
    signal_mod.enqueue(TeardownRequest(ref="workspace:2", repo_name="repo-b"))
    signal_mod.enqueue(TeardownRequest(ref="workspace:3", repo_name=None))

    a_only = signal_mod.iter_pending(repo_name="repo-a")
    assert [r.ref for _, r in a_only] == ["workspace:1"]

    global_only = signal_mod.iter_pending(repo_name=None)
    refs = sorted(r.ref for _, r in global_only)
    assert refs == ["workspace:1", "workspace:2", "workspace:3"]


def test_prune_stale_removes_stale_requests(signal_mod):
    from cockpit.orchestrators.teardown import TeardownRequest

    fresh = signal_mod.enqueue(TeardownRequest(ref="workspace:fresh", repo_name="r"))
    stale = signal_mod.enqueue(TeardownRequest(ref="workspace:stale", repo_name="r"))

    data = json.loads(stale.read_text())
    data["requested_at"] = time.time() - signal_mod.STALE_SECONDS - 10
    stale.write_text(json.dumps(data))

    pruned = signal_mod.prune_stale()
    assert stale in pruned
    assert fresh.exists()


def test_corrupt_marker_skipped(signal_mod, capsys):
    from cockpit.orchestrators.teardown import TeardownRequest

    signal_mod.enqueue(TeardownRequest(ref="workspace:1", repo_name="r"))
    (signal_mod.STATE_DIR / "r" / "garbage.json").write_text("not json {")
    pending = signal_mod.iter_pending()
    assert len(pending) == 1
    assert pending[0][1].ref == "workspace:1"
    err = capsys.readouterr().err
    assert "skipping corrupt close-request marker" in err
    assert "garbage.json" in err


# ── SIGUSR1 kick / SIGTERM stop ─────────────────────────────────────────────


def test_kick_running_no_pidfile_returns_false(signal_mod):
    assert signal_mod.kick_running() is False


def test_kick_running_signals_pid(signal_mod, monkeypatch):
    from cockpit.lib.config import PID_FILE

    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text("4242")

    assert signal_mod.kick_running(quiet=True) is True
    assert sent == [(4242, signal.SIGUSR1)]


def test_kick_running_dead_pid_unlinks_pidfile(signal_mod, monkeypatch, capsys):
    from cockpit.lib.config import PID_FILE

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text("4242")

    def boom(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", boom)
    assert signal_mod.kick_running(quiet=True) is False
    assert not PID_FILE.exists()
    assert capsys.readouterr().err == ""


def test_kick_running_corrupt_pidfile_warns(signal_mod, capsys):
    from cockpit.lib.config import PID_FILE

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text("not-an-int")
    assert signal_mod.kick_running(quiet=True) is False
    err = capsys.readouterr().err
    assert "corrupt pidfile" in err
    assert "not-an-int" in err


def test_kick_running_permission_error_surfaces(signal_mod, monkeypatch, capsys):
    from cockpit.lib.config import PID_FILE

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text("4242")

    def boom(_pid, _sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "kill", boom)
    assert signal_mod.kick_running(quiet=True) is False
    err = capsys.readouterr().err
    assert "cannot signal daemon pid=4242" in err
    assert "not permitted" in err


def test_stop_running_no_pidfile_returns_zero(signal_mod, capsys):
    assert signal_mod.stop_running() == 0
    assert "no cockpit running" in capsys.readouterr().out


def test_stop_running_stale_pidfile_cleans_up(signal_mod, monkeypatch, capsys):
    from cockpit.lib.config import PID_FILE

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text("4242")

    def boom(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", boom)
    assert signal_mod.stop_running() == 0
    assert not PID_FILE.exists()
    assert "stale pidfile" in capsys.readouterr().out


def test_stop_running_signals_and_waits_for_pidfile_removal(
    signal_mod, monkeypatch, capsys
):
    from cockpit.lib.config import PID_FILE

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text("4242")
    sent: list[tuple[int, int]] = []

    def fake_kill(pid, sig):
        sent.append((pid, sig))
        PID_FILE.unlink()

    monkeypatch.setattr(os, "kill", fake_kill)
    assert signal_mod.stop_running() == 0
    assert sent == [(4242, signal.SIGTERM)]
    assert "stopped cockpit pid=4242" in capsys.readouterr().out


def test_sync_kicks_when_running(signal_mod, monkeypatch):
    monkeypatch.setattr(signal_mod, "kick_running", lambda: True)
    fallback_calls: list[int] = []

    def fallback() -> int:
        fallback_calls.append(1)
        return 7

    assert signal_mod.sync(fallback) == 0
    assert fallback_calls == []


def test_sync_falls_back_when_not_running(signal_mod, monkeypatch):
    monkeypatch.setattr(signal_mod, "kick_running", lambda: False)
    assert signal_mod.sync(lambda: 7) == 7


# ── real-subprocess signal validation (no os.kill mocks) ────────────────────

import subprocess  # noqa: E402
import sys  # noqa: E402
import textwrap  # noqa: E402
import threading  # noqa: E402


def _wait_for(path, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(0.02)
    return False


def _spawn_trap(tmp_path):
    """Launch a subprocess that records SIGUSR1 and exits on SIGTERM."""
    usr1_marker = tmp_path / "trap_usr1.log"
    term_marker = tmp_path / "trap_term.log"
    script = textwrap.dedent(
        f"""
        import signal, sys, time
        def on_usr1(*_):
            with open({str(usr1_marker)!r}, 'w') as fh:
                fh.write('usr1\\n')
        def on_term(*_):
            with open({str(term_marker)!r}, 'w') as fh:
                fh.write('term\\n')
            sys.exit(0)
        signal.signal(signal.SIGUSR1, on_usr1)
        signal.signal(signal.SIGTERM, on_term)
        sys.stdout.write('ready\\n')
        sys.stdout.flush()
        while True:
            time.sleep(0.05)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None
    ready = proc.stdout.readline().strip()
    assert ready == "ready", f"trap subprocess did not signal ready: {ready!r}"
    return proc, usr1_marker, term_marker


def test_kick_running_signals_real_process(signal_mod, tmp_path):
    from cockpit.lib.config import PID_FILE

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    proc, usr1_marker, _ = _spawn_trap(tmp_path)
    try:
        PID_FILE.write_text(str(proc.pid))
        assert signal_mod.kick_running(quiet=True) is True
        assert _wait_for(usr1_marker), "subprocess never received SIGUSR1"
    finally:
        proc.terminate()
        proc.wait(timeout=5.0)


def test_stop_running_terminates_real_process(signal_mod, tmp_path):
    from cockpit.lib.config import PID_FILE

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    proc, _, term_marker = _spawn_trap(tmp_path)
    try:
        PID_FILE.write_text(str(proc.pid))

        # The trap subprocess doesn't unlink the pidfile itself (that's
        # run_watcher's `finally:` cleanup). Help stop_running observe a
        # "clean shutdown" by clearing the pidfile shortly after SIGTERM.
        def _cleanup_pidfile():
            time.sleep(0.05)
            PID_FILE.unlink(missing_ok=True)

        threading.Thread(target=_cleanup_pidfile, daemon=True).start()

        rc = signal_mod.stop_running()
        assert rc == 0
        assert _wait_for(term_marker), "subprocess never received SIGTERM"
        assert proc.wait(timeout=5.0) == 0
        assert not PID_FILE.exists()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5.0)
