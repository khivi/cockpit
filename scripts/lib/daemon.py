"""Daemon-side runtime: pidfile + signal-handling sleep/wake loop.

This is the *daemon-side* half. The caller-side IPC (SIGUSR1 kick, SIGTERM
stop, close-request queue) lives in `lib/daemon_signal.py`.

The watcher knows nothing about cockpit's reconcile logic. The caller passes:

  - `tick_fn()`            — one cycle of work, called every `watch_secs`
  - `on_start` / `on_stop` — optional setup/teardown (e.g. status pills)
  - `on_wake`              — optional callback when SIGUSR1 interrupts the sleep

SIGTERM, SIGINT, and SIGHUP all run pidfile cleanup and exit cleanly.
SIGHUP matters because a daemon launched from a terminal session receives
it when the controlling terminal closes; without the handler the process
dies before `finally` runs and leaves a stale pidfile. Errors from
`tick_fn` are caught and logged with a timestamp so a transient failure
doesn't bring the watcher down.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime

from .config import PID_FILE, ensure_state_dirs

_wake = False
_stop = threading.Event()


def _on_usr1(_signum, _frame):
    global _wake
    _wake = True


def _handle_wake(
    on_wake: Callable[[], None] | None,
    fast_tick_fn: Callable[[], None] | None,
) -> None:
    """Run on-wake side effects: caller's `on_wake` callback, then an
    immediate `fast_tick_fn()` so local-only cells refresh alongside the
    slow tick that the main loop is about to run.

    Errors in `fast_tick_fn` are caught and logged so a transient failure
    doesn't take down the loop.
    """
    if on_wake:
        on_wake()
    if fast_tick_fn is not None:
        try:
            fast_tick_fn()
        except Exception as e:
            ts = datetime.now().isoformat(timespec="seconds")
            print(f"[{ts}] wake fast-tick error: {e}", file=sys.stderr, flush=True)


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
    """Run `tick_fn()` every `watch_secs`. SIGUSR1 interrupts the sleep to
    tick now AND triggers an immediate `fast_tick_fn()` so local-only cells
    refresh alongside the kicked slow tick instead of waiting up to
    `fast_secs` for the next scheduled fast pass.

    Refuses to start if a live pidfile exists; stale pidfiles are cleaned up.

    If `fast_tick_fn` + `fast_secs > 0` are provided, a background thread
    runs `fast_tick_fn()` every `fast_secs` independently of the main tick.
    Use this for cheap, local-only updates (git-state cells) that should
    refresh more often than the expensive main loop's `gh`-driven cadence.
    Failures in the fast tick are caught and logged but never kill the
    thread — the next fast tick retries.

    Concurrency between slow and fast ticks is each tick fn's own concern —
    this module no longer holds a shared lock. Tick fns that share writable
    state should serialize themselves.
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
    signal.signal(signal.SIGHUP, cleanup)

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
                _handle_wake(on_wake, fast_tick_fn)
    finally:
        _stop.set()
        if fast_thread is not None:
            fast_thread.join(timeout=fast_secs + 1)
        if on_stop:
            on_stop()
        PID_FILE.unlink(missing_ok=True)


def _fast_loop(tick_fn: Callable[[], None], secs: int) -> None:
    """Background fast-tick loop. Exits when `_stop` is set.

    No cross-tick lock here — the daemon framework is concurrency-agnostic.
    If `tick_fn` shares writable state with the slow tick, it must serialize
    itself (cockpit's `_fast_tick` + `_once_with` share a module-level lock
    for exactly this).
    """
    while not _stop.is_set():
        try:
            tick_fn()
        except Exception as e:
            ts = datetime.now().isoformat(timespec="seconds")
            print(
                f"[{ts}] fast-tick error: {e}",
                file=sys.stderr,
                flush=True,
            )
        _stop.wait(timeout=secs)
