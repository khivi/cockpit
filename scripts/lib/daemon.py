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
import threading
import time
from datetime import datetime
from typing import Callable

from .config import PID_FILE, ensure_state_dirs

_wake = False
_stop = threading.Event()
_tick_lock = threading.Lock()


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
    fast_tick_fn: Callable[[], None] | None = None,
    fast_secs: int = 0,
) -> None:
    """Run `tick_fn()` every `watch_secs`. SIGUSR1 interrupts the sleep to tick now.

    Refuses to start if a live pidfile exists; stale pidfiles are cleaned up.

    If `fast_tick_fn` + `fast_secs > 0` are provided, a background thread
    runs `fast_tick_fn()` every `fast_secs` independently of the main tick.
    Use this for cheap, local-only updates (git-state cells) that should
    refresh more often than the expensive main loop's `gh`-driven cadence.
    Failures in the fast tick are caught and logged but never kill the
    thread — the next fast tick retries.
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

    print(f"slow-tick: every {watch_secs:>3}s", flush=True)
    if fast_tick_fn is not None and fast_secs > 0:
        print(f"fast-tick: every {fast_secs:>3}s", flush=True)
    if on_start:
        on_start()

    fast_thread: threading.Thread | None = None
    _stop.clear()
    if fast_tick_fn is not None and fast_secs > 0:
        fast_thread = threading.Thread(
            target=_fast_loop,
            args=(fast_tick_fn, fast_secs),
            daemon=True,
            name="cockpit-fast-tick",
        )
        fast_thread.start()

    global _wake
    try:
        while True:
            try:
                with _tick_lock:
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
        _stop.set()
        if fast_thread is not None:
            fast_thread.join(timeout=fast_secs + 1)
        if on_stop:
            on_stop()
        PID_FILE.unlink(missing_ok=True)


def _fast_loop(tick_fn: Callable[[], None], secs: int) -> None:
    """Background fast-tick loop. Exits when `_stop` is set.

    Shares `_tick_lock` with the slow tick — if the slow tick is mid-cycle
    when the fast tick fires, this thread blocks on the lock until the slow
    tick releases. Prevents redundant git-state writes (the slow tick already
    writes those cells in `_write_pr_caches`) and CPU collisions when the
    fast/slow cadences line up (every 10th fast tick at 30s/300s).
    """
    while not _stop.is_set():
        try:
            with _tick_lock:
                tick_fn()
        except Exception as e:
            ts = datetime.now().isoformat(timespec="seconds")
            print(
                f"[{ts}] fast-tick error: {e}",
                file=sys.stderr,
                flush=True,
            )
        _stop.wait(timeout=secs)
