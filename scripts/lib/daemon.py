"""Daemon-side runtime: pidfile + signal-handling sleep/wake loop.

This is the *daemon-side* half. The caller-side IPC (SIGUSR1 kick, SIGTERM
stop, close-request queue) lives in `lib/daemon_signal.py`.

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
