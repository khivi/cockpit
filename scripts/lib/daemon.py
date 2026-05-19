"""Daemon plumbing: pidfile, SIGUSR1 kick, SIGTERM stop, sleep/wake loop.

The watcher knows nothing about cockpit's reconcile logic. The caller passes:

  - `tick_fn()`            — one cycle of work, called every `watch_secs`
  - `on_start` / `on_stop` — optional setup/teardown (e.g. status pills)
  - `on_wake`              — optional callback when SIGUSR1 interrupts the sleep

SIGTERM and SIGINT both run pidfile cleanup and exit cleanly. Errors from
`tick_fn` are caught and logged with a timestamp so a transient failure
doesn't bring the watcher down.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from datetime import datetime
from typing import Callable

from .config import PID_FILE, ensure_state_dirs

_wake = False


def _on_usr1(_signum, _frame):
    global _wake
    _wake = True


def kick_running(*, quiet: bool = False) -> bool:
    """SIGUSR1 a running watcher. True if signalled, False if no live pidfile.

    `quiet=True` suppresses the success print so callers (e.g. spawn.py) can
    keep their own stdout clean.
    """
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGUSR1)
        if not quiet:
            print(f"kicked cockpit pid={pid}")
        return True
    except (ProcessLookupError, ValueError, OSError):
        return False


def sync(once_fn: Callable[[], int]) -> int:
    """USR1-kick a running watcher; if none, run `once_fn` inline."""
    return 0 if kick_running() else once_fn()


def stop_running() -> int:
    """SIGTERM the watcher and wait up to 5s for clean shutdown. Returns exit code."""
    if not PID_FILE.exists():
        print("no cockpit running (no pidfile)")
        return 0
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError) as e:
        print(f"unreadable pidfile: {e}", file=sys.stderr)
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        PID_FILE.unlink(missing_ok=True)
        print(f"cockpit pid={pid} was not running; removed stale pidfile")
        return 0
    deadline = time.time() + 5.0
    while time.time() < deadline and PID_FILE.exists():
        time.sleep(0.1)
    if PID_FILE.exists():
        print(
            f"sent SIGTERM to pid={pid} but pidfile still present after 5s",
            file=sys.stderr,
        )
        return 1
    print(f"stopped cockpit pid={pid}")
    return 0


def run_watcher(
    tick_fn: Callable[[], None],
    watch_secs: int,
    *,
    on_start: Callable[[], None] | None = None,
    on_stop: Callable[[], None] | None = None,
    on_wake: Callable[[], None] | None = None,
) -> None:
    """Run `tick_fn()` every `watch_secs`. SIGUSR1 interrupts the sleep to tick now.

    Refuses to start if a live pidfile exists; stale pidfiles are cleaned up.
    """
    ensure_state_dirs()
    if PID_FILE.exists():
        try:
            old = int(PID_FILE.read_text().strip())
            os.kill(old, 0)
            print(f"cockpit already running pid={old}", file=sys.stderr)
            sys.exit(1)
        except (ProcessLookupError, ValueError, OSError):
            PID_FILE.unlink(missing_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    signal.signal(signal.SIGUSR1, _on_usr1)

    def cleanup(*_):
        PID_FILE.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    print(f"watch: every {watch_secs}s  pid={os.getpid()}", flush=True)
    if on_start:
        on_start()

    global _wake
    try:
        while True:
            try:
                tick_fn()
            except Exception as e:
                ts = datetime.now().isoformat(timespec="seconds")
                print(f"[{ts}] watch cycle error: {e}", file=sys.stderr, flush=True)
            slept = 0
            while slept < watch_secs and not _wake:
                time.sleep(1)
                slept += 1
            if _wake:
                _wake = False
                if on_wake:
                    on_wake()
    finally:
        if on_stop:
            on_stop()
        PID_FILE.unlink(missing_ok=True)
