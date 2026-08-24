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
from cockpit.lib.capabilities import probe

_CAPS_OK = '{"capabilities":["events.v1","workspace.groups.v1"]}'
_EVENT = '{"type":"event","name":"workspace.created","seq":1}'
_CLOSED = (
    '{"type":"event","name":"workspace.closed","seq":2,'
    '"payload":{"workspace_id":"WS-1","cwd":"/tmp/repo/feat"}}'
)
_ACK = '{"type":"ack","protocol":"cmux-events"}'

# The shared probe reads verbs off `cmux --help` before it will trust
# `capabilities` (an absent verb is its too-old signal), so the fake answers both.
_HELP = "Commands:\\n  capabilities\\n  events\\n  list-workspaces\\n"


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """`capabilities.probe` is process-cached; clear it so one case's fake cmux
    can't decide the next one's gate."""
    probe.cache_clear()
    yield
    probe.cache_clear()


def _fake_cmux(tmp_path: Path, monkeypatch, *, caps: str, events_body: str) -> Path:
    """Plant a `cmux` that answers `--help` + `capabilities` and streams
    `events`, logging each `events` argv so the test can assert flags + count."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "events.log"
    script = bin_dir / "cmux"
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  --help) printf '{_HELP}' ;;\n"
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


@pytest.mark.parametrize("line", ["", "not json", _ACK, "[1,2]", '"str"'])
def test_non_event_lines_never_ring(line):
    assert events_mod._parse_event(line) is None


# --- the sidebar-X gesture -------------------------------------------------
#
# `workspace.closed` is the one frame whose payload is read, so its extraction
# is pinned here: the caller tears a worktree down off it, and a wrong cwd would
# tear down the wrong one.


def test_closed_workspace_extracts_id_and_cwd():
    frame = events_mod._parse_event(_CLOSED)
    assert events_mod._closed_workspace(frame) == ("WS-1", Path("/tmp/repo/feat"))


def test_created_frames_are_not_a_close():
    assert events_mod._closed_workspace(events_mod._parse_event(_EVENT)) is None


@pytest.mark.parametrize(
    "payload",
    [
        '{"workspace_id":"WS-1"}',  # no cwd: nothing to resolve to a worktree
        '{"cwd":"/tmp/repo/feat"}',  # no id: the self-close filter can't run
        '{"workspace_id":"","cwd":""}',
        "null",
        '"not-an-object"',
    ],
)
def test_a_close_frame_missing_either_field_is_skipped(payload):
    line = f'{{"type":"event","name":"workspace.closed","payload":{payload}}}'
    assert events_mod._closed_workspace(events_mod._parse_event(line)) is None


def test_on_closed_fires_alongside_the_doorbell(tmp_path, monkeypatch):
    body = f"    printf '%s\\n' '{_EVENT}' '{_CLOSED}'"
    _fake_cmux(tmp_path, monkeypatch, caps=_CAPS_OK, events_body=body)
    monkeypatch.setattr(events_mod, "_BACKOFF_STEP_SECONDS", 0.0)
    hits, stop, on_change = _collect(stop_after=2)
    closed: list[tuple[str, Path]] = []

    events_mod.watch_workspace_events(
        on_change, stop, lambda wsid, cwd: closed.append((wsid, cwd))
    )

    # Both frames ring the doorbell; only the close one reaches `on_closed`.
    assert len(hits) == 2
    assert closed == [("WS-1", Path("/tmp/repo/feat"))]


def test_omitting_on_closed_is_exactly_the_old_doorbell(tmp_path, monkeypatch):
    body = f"    printf '%s\\n' '{_CLOSED}'"
    _fake_cmux(tmp_path, monkeypatch, caps=_CAPS_OK, events_body=body)
    monkeypatch.setattr(events_mod, "_BACKOFF_STEP_SECONDS", 0.0)
    hits, stop, on_change = _collect(stop_after=1)

    events_mod.watch_workspace_events(on_change, stop)  # no third arg

    assert len(hits) == 1


def test_a_raising_close_handler_never_kills_the_stream(tmp_path, monkeypatch):
    """The doorbell already fired, so the tick lands either way — a broken
    handler must not cost the whole event stream."""
    body = f"    printf '%s\\n' '{_CLOSED}' '{_CLOSED}'"
    _fake_cmux(tmp_path, monkeypatch, caps=_CAPS_OK, events_body=body)
    monkeypatch.setattr(events_mod, "_BACKOFF_STEP_SECONDS", 0.0)
    hits, stop, on_change = _collect(stop_after=2)

    def boom(wsid: str, cwd: Path) -> None:
        raise RuntimeError("handler exploded")

    events_mod.watch_workspace_events(on_change, stop, boom)

    assert len(hits) == 2  # second frame still read after the first one raised
