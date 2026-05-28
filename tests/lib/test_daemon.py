"""Integration tests for `scripts.lib.daemon.run_watcher`.

Drives a real Python subprocess that imports the module and calls
`run_watcher`, then exercises pidfile collisions, SIGUSR1 wake,
SIGTERM cleanup, and tick exception recovery via file-marker
signaling. Avoids mocking `signal`/`os.kill` so the actual signal
handlers are validated end-to-end.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _wait_for(path: Path, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(0.02)
    return False


def _wait_for_process(proc: subprocess.Popen, timeout: float = 5.0) -> int | None:
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def _write_driver(
    tmp_path: Path,
    *,
    watch_secs: int = 1,
    tick_raises_every: int | None = None,
    fast_secs: int = 0,
) -> Path:
    tick_marker = tmp_path / "ticks.log"
    wake_marker = tmp_path / "wake.log"
    stop_marker = tmp_path / "stop.log"
    fast_marker = tmp_path / "fast.log"
    raise_line = (
        f"if count % {tick_raises_every} == 0: raise RuntimeError('boom-' + str(count))"
        if tick_raises_every
        else "pass"
    )
    fast_kwarg = f", fast_tick_fn=fast, fast_secs={fast_secs}" if fast_secs > 0 else ""
    body = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(REPO_ROOT)!r})
        from scripts.lib.daemon import run_watcher

        _count = {{'n': 0}}
        _fast = {{'n': 0}}

        def tick():
            _count['n'] += 1
            count = _count['n']
            with open({str(tick_marker)!r}, 'a') as fh:
                fh.write(f't{{count}}\\n')
            {raise_line}

        def fast():
            _fast['n'] += 1
            with open({str(fast_marker)!r}, 'a') as fh:
                fh.write(f'f{{_fast["n"]}}\\n')

        def wake():
            with open({str(wake_marker)!r}, 'w') as fh:
                fh.write('woke\\n')

        def stop():
            with open({str(stop_marker)!r}, 'w') as fh:
                fh.write('stopped\\n')

        run_watcher(tick, {watch_secs}, on_wake=wake, on_stop=stop{fast_kwarg})
        """
    )
    driver = tmp_path / "driver.py"
    driver.write_text(body)
    return driver


def _launch(
    driver: Path, cockpit_home: Path, *, capture_stderr: bool = False
) -> subprocess.Popen:
    env = os.environ.copy()
    env["COCKPIT_HOME"] = str(cockpit_home)
    return subprocess.Popen(
        [sys.executable, str(driver)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE if capture_stderr else subprocess.DEVNULL,
        text=True,
    )


@pytest.fixture
def cockpit_home(tmp_path) -> Path:
    home = tmp_path / "cockpit-home"
    home.mkdir()
    return home


def test_pidfile_race_refuses_to_start(tmp_path, cockpit_home):
    pidfile = cockpit_home / "cockpit.pid"
    pidfile.write_text(str(os.getpid()))

    driver = _write_driver(tmp_path)
    proc = _launch(driver, cockpit_home, capture_stderr=True)
    rc = _wait_for_process(proc, timeout=5.0)
    stderr = proc.stderr.read() if proc.stderr else ""

    assert rc == 1, f"expected exit 1, got {rc}; stderr={stderr}"
    assert "already running" in stderr
    assert not (tmp_path / "ticks.log").exists()
    assert pidfile.read_text().strip() == str(os.getpid())


def test_stale_pidfile_replaced(tmp_path, cockpit_home):
    pidfile = cockpit_home / "cockpit.pid"
    pidfile.write_text("999999")

    driver = _write_driver(tmp_path)
    proc = _launch(driver, cockpit_home)
    try:
        assert _wait_for(tmp_path / "ticks.log"), "no tick after stale-pid cleanup"
        live_pid = int(pidfile.read_text().strip())
        assert live_pid == proc.pid
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5.0)


def test_sigusr1_triggers_wake(tmp_path, cockpit_home):
    driver = _write_driver(tmp_path, watch_secs=60)
    proc = _launch(driver, cockpit_home)
    try:
        assert _wait_for(tmp_path / "ticks.log"), "no initial tick"
        time.sleep(0.3)
        os.kill(proc.pid, signal.SIGUSR1)
        assert _wait_for(tmp_path / "wake.log"), "wake marker never appeared"
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5.0)


def test_sigusr1_also_triggers_fast_tick(tmp_path, cockpit_home):
    """SIGUSR1 kicks the slow tick AND runs an immediate fast tick so
    local-only cells refresh alongside the gh-driven slow pass instead of
    waiting up to `fast_secs` for the next scheduled fast pass.
    """
    fast_marker = tmp_path / "fast.log"
    driver = _write_driver(tmp_path, watch_secs=60, fast_secs=60)
    proc = _launch(driver, cockpit_home)
    try:
        assert _wait_for(tmp_path / "ticks.log"), "no initial slow tick"
        # The background fast loop fires at startup too — wait for that
        # initial line so we can count subsequent kicks separately.
        assert _wait_for(fast_marker), "no initial fast tick"
        baseline = len(fast_marker.read_text().splitlines())

        os.kill(proc.pid, signal.SIGUSR1)
        assert _wait_for(tmp_path / "wake.log"), "wake marker never appeared"

        deadline = time.time() + 3.0
        while time.time() < deadline:
            n = len(fast_marker.read_text().splitlines())
            if n > baseline:
                break
            time.sleep(0.05)
        n = len(fast_marker.read_text().splitlines())
        assert (
            n > baseline
        ), f"fast tick did not fire on SIGUSR1 (baseline={baseline}, after={n})"
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5.0)


def test_sigterm_cleanup_removes_pidfile(tmp_path, cockpit_home):
    pidfile = cockpit_home / "cockpit.pid"
    driver = _write_driver(tmp_path, watch_secs=60)
    proc = _launch(driver, cockpit_home)

    assert _wait_for(tmp_path / "ticks.log"), "no initial tick"
    assert pidfile.exists()

    proc.send_signal(signal.SIGTERM)
    rc = _wait_for_process(proc, timeout=5.0)
    assert rc == 0, f"expected clean exit, got {rc}"
    assert _wait_for(tmp_path / "stop.log"), "on_stop never ran"
    assert not pidfile.exists(), "pidfile should be removed on SIGTERM"


def test_sighup_cleanup_removes_pidfile(tmp_path, cockpit_home):
    # SIGHUP fires when the controlling terminal closes. Without a handler the
    # default disposition kills the process before `finally` runs and leaves a
    # stale pidfile that blocks the next daemon launch.
    pidfile = cockpit_home / "cockpit.pid"
    driver = _write_driver(tmp_path, watch_secs=60)
    proc = _launch(driver, cockpit_home)

    assert _wait_for(tmp_path / "ticks.log"), "no initial tick"
    assert pidfile.exists()

    proc.send_signal(signal.SIGHUP)
    rc = _wait_for_process(proc, timeout=5.0)
    assert rc == 0, f"expected clean exit, got {rc}"
    assert _wait_for(tmp_path / "stop.log"), "on_stop never ran"
    assert not pidfile.exists(), "pidfile should be removed on SIGHUP"


def test_tick_exception_does_not_kill_loop(tmp_path, cockpit_home):
    driver = _write_driver(tmp_path, watch_secs=1, tick_raises_every=1)
    proc = _launch(driver, cockpit_home, capture_stderr=True)
    try:
        deadline = time.time() + 6.0
        tick_count = 0
        while time.time() < deadline:
            log = tmp_path / "ticks.log"
            if log.exists():
                tick_count = len(log.read_text().splitlines())
                if tick_count >= 3:
                    break
            time.sleep(0.1)
        assert tick_count >= 3, f"only saw {tick_count} ticks before timeout"
        assert proc.poll() is None, "daemon died on tick exception"
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5.0)
        stderr = proc.stderr.read() if proc.stderr else ""

    assert "watch cycle error" in stderr
    assert "boom-" in stderr
