"""Daemon-side runtime: pidfile management.

The TUI (`cockpit/tui/app.py`) is the daemon now and owns its own asyncio
signal handlers and tick timers. This module only holds the pidfile
primitives shared by the TUI and `cockpit.py`'s startup path.
"""

from __future__ import annotations

import os
import sys

from .config import PID_FILE, ensure_state_dirs


def claim_pidfile() -> None:
    """Write our PID to PID_FILE, refusing to start if a live daemon holds it.

    A stale pidfile (the recorded PID is dead, unreadable, or malformed) is
    cleaned up and reclaimed. Exits 1 when another live daemon is already
    running.
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


def release_pidfile() -> None:
    """Remove the pidfile if present (idempotent)."""
    PID_FILE.unlink(missing_ok=True)
