"""Append-only record of what cockpit did to which session.

A log, like `$COCKPIT_HOME/spawn.log`, not cache: nothing reads it back, so no
cell, pill, decision or TUI row depends on it. It exists so "why didn't it
nudge me" and "who closed this" are answerable after the fact.

Lives in `$COCKPIT_RUNTIME_DIR`, never `$COCKPIT_HOME`: a `ref` is a local cmux
workspace id, and a synced home would mix machines' histories.

Three choke points call `record` (`cmux.nudge_if_idle`, `teardown.teardown`,
the spawn path); a new call site per caller is not wanted.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from . import config

LOG_NAME = "actions.jsonl"
MAX_BYTES = 5 * 1024 * 1024


def record(
    action: str,
    *,
    actor: str,
    ref: str | None = None,
    cwd: str | None = None,
    session: str | None = None,
    detail: Any = None,
) -> None:
    """Append one JSON line. Never raises: a log failure must not break the action."""
    path = config.COCKPIT_RUNTIME_DIR / LOG_NAME
    line = json.dumps(
        {
            "ts": time.time(),
            "pid": os.getpid(),
            "actor": actor,
            "action": action,
            "ref": ref,
            "cwd": cwd,
            "session": session,
            "detail": detail,
        },
        default=str,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size > MAX_BYTES:
                path.replace(path.with_name(LOG_NAME + ".1"))
        except FileNotFoundError:
            pass
        # One write on an O_APPEND fd: concurrent processes cannot interleave a line.
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (line + "\n").encode())
        finally:
            os.close(fd)
    except OSError:
        pass
