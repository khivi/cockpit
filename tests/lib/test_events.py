"""`cmux events` doorbell — driven against a fake `cmux` on PATH.

Leaf-layer per AGENTS.md, but the real dependency is a *running cmux app*, not
a binary a test can install, so the stand-in is a shell script emitting the
same NDJSON frame shapes `cmux events` does.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

import cockpit.lib.events as events_mod

_CAPS_OK = '{"capabilities":["events.v1","workspace.groups.v1"]}'
_EVENT = '{"type":"event","name":"workspace.created","seq":1}'
_ACK = '{"type":"ack","protocol":"cmux-events"}'


def _fake_cmux(tmp_path: Path, monkeypatch, *, caps: str, events_body: str) -> Path:
    """Plant a `cmux` that answers `capabilities` and streams `events`, logging
    each `events` invocation's argv so the test can assert the flags + count."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "events.log"
    script = bin_dir / "cmux"
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  capabilities) printf '%s\\n' '{caps}' ;;\n"
        f'  events) printf "%s\\n" "$*" >> "{log}"\n{events_body}\n ;;\n'
        "esac\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(events_mod, "CURSOR_FILE", tmp_path / "cache" / "seq")
    return log


def _collect(
    stop_after: int | None = None,
) -> tuple[list[int], threading.Event, Callable[[], None]]:
    """A callback recording hits, optionally setting `stop` after N of them."""
    hits: list[int] = []
    stop = threading.Event()

    def on_change() -> None:
        hits.append(1)
        if stop_after is not None and len(hits) >= stop_after:
            stop.set()

    return hits, stop, on_change


def test_events_supported_requires_the_capability(tmp_path, monkeypatch):
    _fake_cmux(
        tmp_path, monkeypatch, caps='{"capabilities":["dogfood.v1"]}', events_body=""
    )
    assert events_mod.events_supported() is False


def test_events_supported_with_cmux_and_capability(tmp_path, monkeypatch):
    _fake_cmux(tmp_path, monkeypatch, caps=_CAPS_OK, events_body="")
    assert events_mod.events_supported() is True


def test_no_backend_is_a_silent_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(events_mod, "is_cmux", lambda: False)
    hits, stop, on_change = _collect()
    events_mod.watch_workspace_events(on_change, stop)
    assert hits == []


def test_fires_once_per_event_frame_and_ignores_other_frames(tmp_path, monkeypatch):
    # Ack frame + two events; the stream then ends and the callback's third hit
    # (from the restart) is what sets `stop`, so the loop can't spin forever.
    body = f"    printf '%s\\n' '{_ACK}' '{_EVENT}' '{_EVENT}'"
    log = _fake_cmux(tmp_path, monkeypatch, caps=_CAPS_OK, events_body=body)
    monkeypatch.setattr(events_mod, "_BACKOFF_STEP_SECONDS", 0.0)
    hits, stop, on_change = _collect(stop_after=3)

    events_mod.watch_workspace_events(on_change, stop)

    assert len(hits) == 3  # the ack frame never counted
    argv = log.read_text().splitlines()[0]
    assert "--reconnect" in argv and "--cursor-file" in argv
    assert "--name workspace.created" in argv
    assert "--name workspace.closed" in argv


def test_stop_kills_a_live_stream(tmp_path, monkeypatch):
    _fake_cmux(tmp_path, monkeypatch, caps=_CAPS_OK, events_body="    sleep 60")
    hits, stop, on_change = _collect()
    t = threading.Thread(
        target=events_mod.watch_workspace_events, args=(on_change, stop), daemon=True
    )
    t.start()
    time.sleep(0.5)  # let the child start before asking it to stop
    stop.set()
    t.join(timeout=10)
    assert not t.is_alive()


def test_a_stream_that_dies_instantly_gives_up(tmp_path, monkeypatch):
    """Otherwise a cmux that rejects `events` would respawn forever."""
    log = _fake_cmux(tmp_path, monkeypatch, caps=_CAPS_OK, events_body="    exit 1")
    monkeypatch.setattr(events_mod, "_BACKOFF_STEP_SECONDS", 0.0)
    hits, stop, on_change = _collect()

    events_mod.watch_workspace_events(on_change, stop)

    assert hits == []
    assert len(log.read_text().splitlines()) == events_mod._MAX_FAST_EXITS


@pytest.mark.parametrize("line", ["", "not json", _ACK])
def test_non_event_lines_never_ring(line):
    assert events_mod._is_event(line) is False
