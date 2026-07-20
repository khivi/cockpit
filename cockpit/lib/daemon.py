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


def reassert_pidfile() -> None:
    """Re-write the pidfile if it's missing or stale, so a live daemon that
    lost its pidfile mid-run becomes reachable again.

    `claim_pidfile` runs exactly once, at startup. If the pidfile is later
    deleted — a racing stale-cleanup or an external `rm` — nothing rewrites it,
    so `cockpit close`/spawn kicks report "no daemon" for the rest of this
    process's life. Called each fast tick to self-heal within ~30s, mirroring
    the workspace-name / colour / `idle=` re-asserts. Idempotent; only writes on
    drift, and never clobbers a pidfile a *different* live daemon holds."""
    me = os.getpid()
    try:
        raw = PID_FILE.read_text().strip()
    except OSError:
        PID_FILE.write_text(str(me))  # missing → reclaim
        return
    if raw == str(me):
        return  # already ours — no-op
    try:
        os.kill(int(raw), 0)
    except (ValueError, ProcessLookupError, OSError):
        PID_FILE.write_text(str(me))  # dead/corrupt → reclaim


def release_pidfile() -> None:
    """Remove the pidfile if present (idempotent)."""
    PID_FILE.unlink(missing_ok=True)
