"""`cmux events` doorbell — wakes a tick early, never carries state.

cmux exposes a resumable NDJSON event stream (`events.v1`). cockpit subscribes
to exactly two names — `workspace.created` / `workspace.closed` — and uses them
as a *trigger*: an event calls `on_change()`, the tick then re-derives every bit
of inventory from `git worktree list` + `cmux list-workspaces` exactly as it
does on the timer. Nothing here is ever read as state, so AGENTS.md's "Inventory
is derived every cycle, never stored" still holds; the win is purely latency —
a spawned or closed workspace lands in ~0s instead of at the next fast tick.

The one exception is `on_closed`, which carries a `workspace.closed` frame's
`workspace_id` + `cwd` to the caller. Clicking the X in cmux's sidebar is a
*gesture*, not state — it exists nowhere else (derived inventory cannot tell
"the user just closed this" from "this worktree has no workspace yet", which is
the normal pre-spawn state), so the event is the only place it can be read.
It stays a gesture: the payload is never cached, never written to a cell, and
never consulted by a tick — it only routes to the same gated close path the `c`
key uses. See AGENTS.md's doorbell invariant.

Two rules:

- **Only `workspace.created` / `workspace.closed`.** Every tick writes pills,
  colours and names, which cmux reports as `sidebar.metadata.*` /
  `workspace.renamed` — subscribing to those would make the daemon ring its own
  doorbell forever. Created/closed are the two events a *user* causes, and
  neither is emitted by the tick body that they wake (spawning is slow-tick
  only; the doorbell wakes the fast tick, which never spawns).
- **The stream is best-effort.** The poll interval stays the correctness floor,
  so every failure here (no cmux, no capability, a dead app, a crashed stream)
  degrades silently to exactly the pre-event behaviour.

Debouncing lives in the *caller* (the TUI already coalesces on `_fast_phase`),
so this module stays a dumb line reader.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .capabilities import has_capability
from .config import CACHE_DIR
from .tool import is_cmux

EVENTS_CAPABILITY = "events.v1"

# cmux's own resume bookmark (a sequence number), not a cockpit cache cell — it
# holds no inventory and nothing but cmux ever reads it.
CURSOR_FILE = CACHE_DIR / "cmux-events.seq"

WATCHED_NAMES = ("workspace.created", "workspace.closed")

# A stream that dies faster than this counts as a failure for the restart cap.
_FAST_EXIT_SECONDS = 5.0
_MAX_FAST_EXITS = 5
_BACKOFF_STEP_SECONDS = 2.0
_BACKOFF_MAX_SECONDS = 30.0


def events_supported() -> bool:
    """True when the backend is cmux and it negotiated `events.v1`.

    `has_capability` is the shared probe (cached once per process), paired with
    `is_cmux()` as its docstring asks; `which` covers a configured-but-absent
    binary, which would otherwise raise on the first `Popen`.
    """
    return (
        is_cmux() and bool(shutil.which("cmux")) and has_capability(EVENTS_CAPABILITY)
    )


def watch_workspace_events(
    on_change: Callable[[], None],
    stop: threading.Event,
    on_closed: Callable[[str, Path], None] | None = None,
) -> None:
    """Block until `stop` is set, calling `on_change()` per workspace event.

    `on_closed(workspace_id, cwd)` additionally fires for `workspace.closed`
    frames that carry both fields — the sidebar-X gesture. It is optional and
    strictly additive: `on_change` fires for that frame either way, so a caller
    that passes nothing gets exactly the pre-existing doorbell.

    No-ops immediately on limux/none/no-capability. Restarts the stream with
    backoff if it exits (cmux quit, machine slept), and gives up for good after
    `_MAX_FAST_EXITS` consecutive instant deaths so a broken binary can't spin.
    """
    if not events_supported():
        return
    fast_exits = 0
    while not stop.is_set():
        started = time.monotonic()
        try:
            _stream_once(on_change, stop, on_closed)
        except Exception as e:  # a dead doorbell must never take the TUI down
            print(f"cmux-events: {e}")
        if stop.is_set():
            return
        if time.monotonic() - started < _FAST_EXIT_SECONDS:
            fast_exits += 1
            if fast_exits >= _MAX_FAST_EXITS:
                print("cmux-events: stream keeps dying — giving up (poll covers it)")
                return
        else:
            fast_exits = 0
        stop.wait(min(_BACKOFF_MAX_SECONDS, _BACKOFF_STEP_SECONDS * fast_exits))


def _stream_once(
    on_change: Callable[[], None],
    stop: threading.Event,
    on_closed: Callable[[str, Path], None] | None = None,
) -> None:
    """Run one `cmux events` subprocess to completion, firing per event line."""
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "cmux",
        "events",
        "--reconnect",
        "--no-heartbeat",
        "--no-ack",
        "--cursor-file",
        str(CURSOR_FILE),
    ]
    for name in WATCHED_NAMES:
        args += ["--name", name]
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        # Own process group so the stop path can kill the whole tree — killing
        # the leader alone leaves a grandchild holding the stdout pipe open,
        # and the reader below blocks on it forever.
        start_new_session=True,
    )

    # `for line in stdout` blocks indefinitely between events, so `stop` can only
    # be honoured by killing the child. Without this the stream outlives the TUI.
    def _reap_on_stop() -> None:
        stop.wait()
        _kill(proc)

    threading.Thread(target=_reap_on_stop, daemon=True).start()
    try:
        assert proc.stdout is not None  # noqa: S101 - mypy narrow, not a runtime check
        for line in proc.stdout:
            if stop.is_set():
                return
            frame = _parse_event(line)
            if frame is None:
                continue
            on_change()
            if on_closed is not None:
                closed = _closed_workspace(frame)
                if closed is not None:
                    # A raising callback must not kill the stream — the doorbell
                    # above already fired, so the tick still lands.
                    try:
                        on_closed(*closed)
                    except Exception as e:
                        print(f"cmux-events: close handler: {e}")
    finally:
        _kill(proc)


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _parse_event(line: str) -> dict | None:
    """The decoded event frame, or None. `--name` already filters server-side;
    this only drops the non-event frames (ack/heartbeat) a future cmux might
    still send, and anything unparsable."""
    try:
        frame = json.loads(line)
    except Exception:
        return None
    return frame if isinstance(frame, dict) and frame.get("type") == "event" else None


def _closed_workspace(frame: dict) -> tuple[str, Path] | None:
    """`(workspace_id, cwd)` for a `workspace.closed` frame carrying both.

    None for `workspace.created`, and for a close frame missing either field —
    a workspace with no cwd can't be resolved to a worktree anyway, so there is
    nothing for the caller to act on.
    """
    if frame.get("name") != "workspace.closed":
        return None
    payload = frame.get("payload")
    if not isinstance(payload, dict):
        return None
    wsid, cwd = payload.get("workspace_id"), payload.get("cwd")
    if not wsid or not cwd:
        return None
    return str(wsid), Path(str(cwd))
