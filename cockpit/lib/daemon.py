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
        _reclaim(me)  # missing → reclaim
        return
    if raw == str(me):
        return  # already ours — no-op
    try:
        os.kill(int(raw), 0)
    except (ValueError, ProcessLookupError, OSError):
        _reclaim(me)  # dead/corrupt → reclaim


def _reclaim(me: int) -> None:
    """Write our pid, re-creating the state dir if it went with the pidfile.

    `claim_pidfile` gets this from `ensure_state_dirs()`, but that runs once at
    startup — and the whole point of the re-assert is to survive the pidfile
    disappearing mid-run. A wipe that takes the *directory* (a `$COCKPIT_HOME`
    under a swept tmpdir, exactly the case the fast tick's docstring names) made
    the read raise `FileNotFoundError`, which is an `OSError` so the caller
    caught it — and then the recovery write raised the same error uncaught,
    taking the fast tick down instead of healing it. Only the two drift paths
    pay the mkdir; the common already-ours case still returns without a syscall.

    Deliberately NOT `ensure_state_dirs()`: that also seeds a `config.json`,
    which is startup's job, not a pidfile reclaim's.
    """
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(me))


def release_pidfile() -> None:
    """Remove the pidfile if present (idempotent)."""
    PID_FILE.unlink(missing_ok=True)
