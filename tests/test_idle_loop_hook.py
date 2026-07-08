"""Behavioural tests for hooks/cmux-idle-pill.sh.

The script shells out to `cmux` via `( command cmux ... & )`. We intercept by
shadowing `cmux` on PATH with a tiny shim that records its argv to a log file,
then assert the recorded calls.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.fixtures import make_shim_on_path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "cmux-idle-pill.sh"


def _plant_cmux_shim(tmp_path: Path, monkeypatch, workspaces: list[str]) -> Path:
    """Plant a cmux shim that emits a controllable workspace list for
    `list-workspaces` and logs argv for everything else. Mirrors the format
    of real `cmux list-workspaces` output (leading indent, then
    `workspace:N  <name>`)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "cmux.log"
    listing = "\n".join(f"  {w}  name" for w in workspaces) + (
        "\n" if workspaces else ""
    )
    shim = bin_dir / "cmux"
    shim.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "list-workspaces" ]; then\n'
        f"  printf %s {repr(listing)}\n"
        "  exit 0\n"
        "fi\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log


@pytest.fixture
def fake_cmux(tmp_path, monkeypatch) -> Path:
    log = _plant_cmux_shim(tmp_path, monkeypatch, ["workspace:99"])
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "workspace:99")
    # Redirect the hook's operator-debug log into tmp_path so prune tests
    # control the file and so unrelated test runs don't pollute the real
    # ~/.config/cockpit/cmux-idle-pill.err.
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    # The verified idle writes (cmux_set_verify / cmux_clear_verify) retry until
    # list-status confirms. The plain shim never reflects state, so cap retries
    # at one with no sleep — otherwise each call leaves a ~5s background subshell
    # polling a torn-down tmp_path. Multi-try behaviour is covered explicitly by
    # the stateful-shim tests below.
    monkeypatch.setenv("CMUX_VERIFY_TRIES", "1")
    monkeypatch.setenv("CMUX_VERIFY_SLEEP", "0")
    return log


def _err_log(tmp_path: Path) -> Path:
    return tmp_path / "cmux-idle-pill.err"


def _poll_lines(log: Path, expected: int, timeout: float = 1.5) -> list[str]:
    """Background subshells make cmux calls async. Poll until enough lines land."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log.exists():
            lines = [ln for ln in log.read_text().splitlines() if ln]
            if len(lines) >= expected:
                return lines
        time.sleep(0.02)
    return [ln for ln in log.read_text().splitlines() if ln] if log.exists() else []


def _wait_quiet(log: Path, timeout: float = 1.5) -> bool:
    """Wait out the full `timeout` deadline, then report whether `log` stayed
    empty/absent — used to prove a noop (nothing was ever written), where a
    poll-until-true helper doesn't apply since there's no positive event to
    wait for. Mirrors `_poll_lines`'s deadline so a slow async subshell write
    that would land within that window isn't missed by a shorter fixed sleep.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log.exists() and log.read_text():
            return False
        time.sleep(0.02)
    return not (log.exists() and log.read_text())


def _transcript_with(tmp_path: Path, tool_names: list[str]) -> Path:
    t = tmp_path / "transcript.jsonl"
    turn = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": n} for n in tool_names]},
    }
    t.write_text(json.dumps(turn) + "\n")
    return t


def test_loop_set_emits_loop_pill(fake_cmux):
    subprocess.run([str(HOOK), "loop-set"], check=True)
    calls = _poll_lines(fake_cmux, expected=1)
    assert any("set-status loop" in c and "🔄" in c for c in calls), calls


def test_loop_clear_clears_loop_pill(fake_cmux):
    subprocess.run([str(HOOK), "loop-clear"], check=True)
    calls = _poll_lines(fake_cmux, expected=1)
    assert any("clear-status loop" in c for c in calls), calls


def test_prompt_clears_idle_pill(fake_cmux):
    subprocess.run([str(HOOK), "prompt"], check=True)
    calls = _poll_lines(fake_cmux, expected=1)
    assert any("clear-status idle" in c for c in calls), calls


def _plant_verify_shim(tmp_path: Path, monkeypatch, *, set_succeeds_on: int) -> Path:
    """Stateful cmux shim: `list-status` reports `idle=idle` only on/after the
    `set_succeeds_on`-th `set-status idle` call. Models a daemon that drops the
    first writes (Broken pipe) then accepts one, so we can assert the hook's
    set→verify→retry loop keeps going until the write lands.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "cmux.log"
    counter = tmp_path / "set_count"
    shim = bin_dir / "cmux"
    shim.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "list-workspaces" ]; then\n'
        '  printf "  workspace:99  name\\n"\n'
        "  exit 0\n"
        "fi\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        'if [ "$1" = "set-status" ] && [ "$2" = "idle" ]; then\n'
        f'  n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "{counter}"\n'
        "fi\n"
        'if [ "$1" = "list-status" ]; then\n'
        f'  n=$(cat "{counter}" 2>/dev/null || echo 0)\n'
        f'  if [ "$n" -ge {set_succeeds_on} ]; then printf "idle=idle\\n"; fi\n'
        "fi\n"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "workspace:99")
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    monkeypatch.setenv("CMUX_VERIFY_TRIES", "5")
    monkeypatch.setenv("CMUX_VERIFY_SLEEP", "0")
    return log


def _poll_until(predicate: Callable[[], bool], timeout: float = 6.0) -> bool:
    """Poll a background-effect predicate until True or timeout. The verified
    writes run in detached subshells, so assertions must wait on the effect."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_stop_idle_write_retries_until_confirmed(tmp_path, monkeypatch):
    # Daemon drops the first two set-status writes; the third lands. The hook
    # must keep retrying past the drops rather than fire-and-forget once.
    log = _plant_verify_shim(tmp_path, monkeypatch, set_succeeds_on=3)
    transcript = _transcript_with(tmp_path, ["Edit"])
    payload = json.dumps({"transcript_path": str(transcript)})
    subprocess.run([str(HOOK), "stop"], input=payload, text=True, check=True)

    def _three_idle_sets() -> bool:
        if not log.exists():
            return False
        return (
            sum("set-status idle idle" in c for c in log.read_text().splitlines()) >= 3
        )

    assert _poll_until(_three_idle_sets), log.read_text() if log.exists() else "no log"
    # Once confirmed, no WARN is logged to the err file.
    err = _err_log(tmp_path)
    err_text = err.read_text() if err.exists() else ""
    assert "not confirmed" not in err_text, err_text


def test_stop_idle_write_warns_when_never_confirmed(tmp_path, monkeypatch):
    # set_succeeds_on far above the retry budget: every verify fails, so after
    # exhausting tries the hook logs a WARN instead of silently giving up.
    _plant_verify_shim(tmp_path, monkeypatch, set_succeeds_on=99)
    transcript = _transcript_with(tmp_path, ["Edit"])
    payload = json.dumps({"transcript_path": str(transcript)})
    subprocess.run([str(HOOK), "stop"], input=payload, text=True, check=True)
    err = _err_log(tmp_path)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if err.exists() and "not confirmed" in err.read_text():
            break
        time.sleep(0.02)
    assert err.exists() and "idle= not confirmed" in err.read_text()


def test_stop_with_schedulewakeup_sets_loop_and_clears_idle(fake_cmux, tmp_path):
    transcript = _transcript_with(tmp_path, ["ScheduleWakeup"])
    payload = json.dumps({"transcript_path": str(transcript)})
    subprocess.run([str(HOOK), "stop"], input=payload, text=True, check=True)
    # Three async lines on this path: the verified idle clear emits both
    # `clear-status idle` and its `list-status` readback, then `set-status loop`
    # lands separately. Poll for all three so the loop pill isn't raced out.
    calls = _poll_lines(fake_cmux, expected=3)
    assert any("clear-status idle" in c for c in calls), calls
    assert any("set-status loop" in c and "🔄" in c for c in calls), calls
    assert not any("set-status idle" in c for c in calls), calls


def test_stop_with_croncreate_sets_loop_and_clears_idle(fake_cmux, tmp_path):
    transcript = _transcript_with(tmp_path, ["Edit", "CronCreate"])
    payload = json.dumps({"transcript_path": str(transcript)})
    subprocess.run([str(HOOK), "stop"], input=payload, text=True, check=True)
    # clear-status idle + its list-status readback + set-status loop = 3 lines.
    calls = _poll_lines(fake_cmux, expected=3)
    assert any("set-status loop" in c for c in calls), calls
    assert not any("set-status idle" in c for c in calls), calls


def test_stop_without_loop_tools_clears_loop_and_sets_idle(fake_cmux, tmp_path):
    transcript = _transcript_with(tmp_path, ["Edit", "Read"])
    payload = json.dumps({"transcript_path": str(transcript)})
    subprocess.run([str(HOOK), "stop"], input=payload, text=True, check=True)
    # clear-status loop + the verified idle set (set-status idle + its
    # list-status readback) = 3 lines.
    calls = _poll_lines(fake_cmux, expected=3)
    assert any("clear-status loop" in c for c in calls), calls
    # Value must be the literal `idle`: cmux >=0.64.10 rejects empty values,
    # so any change away from a non-empty marker silently breaks nudge_if_idle.
    assert any("set-status idle idle" in c for c in calls), calls
    assert not any("set-status loop" in c for c in calls), calls


def test_stop_with_missing_transcript_falls_through_to_idle(fake_cmux, tmp_path):
    payload = json.dumps({"transcript_path": str(tmp_path / "nope.jsonl")})
    subprocess.run([str(HOOK), "stop"], input=payload, text=True, check=True)
    # clear-status loop + the verified idle set (set-status idle + its
    # list-status readback) = 3 lines.
    calls = _poll_lines(fake_cmux, expected=3)
    assert any("set-status idle idle" in c for c in calls), calls
    assert any("clear-status loop" in c for c in calls), calls


def test_no_workspace_id_is_noop(tmp_path, monkeypatch):
    log = make_shim_on_path(tmp_path, monkeypatch, "cmux")
    monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
    subprocess.run([str(HOOK), "loop-set"], check=True)
    assert _wait_quiet(log), log.read_text() if log.exists() else "log missing"


def test_dead_workspace_is_noop(tmp_path, monkeypatch):
    # Workspace was closed/recreated — its ID is no longer in
    # `cmux list-workspaces`. Hook must exit silently so we don't hammer a
    # dead socket and fill the err log with Broken Pipe forever.
    log = _plant_cmux_shim(tmp_path, monkeypatch, ["workspace:1", "workspace:42"])
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "workspace:99")
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    subprocess.run([str(HOOK), "loop-set"], check=True)
    assert _wait_quiet(log), log.read_text() if log.exists() else "log missing"


def test_substring_workspace_id_does_not_match(tmp_path, monkeypatch):
    # `workspace:9` must not match against `workspace:99` in the live list.
    # Space-delimited case match guards against the substring trap.
    log = _plant_cmux_shim(tmp_path, monkeypatch, ["workspace:99"])
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "workspace:9")
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    subprocess.run([str(HOOK), "loop-set"], check=True)
    assert _wait_quiet(log), log.read_text() if log.exists() else "log missing"


def test_live_workspace_passes_through(fake_cmux):
    # Sanity: fake_cmux registers workspace:99 as live, so loop-set must reach
    # the set-status call. (Companion to test_dead_workspace_is_noop.)
    subprocess.run([str(HOOK), "loop-set"], check=True)
    calls = _poll_lines(fake_cmux, expected=1)
    assert any("set-status loop" in c for c in calls), calls


def test_prune_truncates_oversized_log(fake_cmux, tmp_path):
    err = _err_log(tmp_path)
    # Distinct head + tail so we can assert which slice was kept.
    head = b"H" * 80_000
    tail = b"T" * 8_000
    original = head + tail
    err.write_bytes(original)
    assert len(original) > 65_536

    subprocess.run([str(HOOK), "loop-clear"], check=True)
    _poll_lines(fake_cmux, expected=1)  # let the hook finish its rotate

    kept = err.read_bytes()
    # Kept slice must be exactly the last 16 KB of the original — tail
    # preserved, head dropped beyond the cutoff.
    assert len(kept) == 16_384, len(kept)
    assert kept == original[-16_384:]


def test_prune_leaves_undersized_log_alone(fake_cmux, tmp_path):
    err = _err_log(tmp_path)
    body = b"x" * 10_000  # well under 64 KB threshold
    err.write_bytes(body)

    subprocess.run([str(HOOK), "loop-clear"], check=True)
    _poll_lines(fake_cmux, expected=1)

    assert err.read_bytes() == body


def test_prune_skipped_when_lock_held(fake_cmux, tmp_path):
    err = _err_log(tmp_path)
    err.write_bytes(b"H" * 80_000)
    original = err.read_bytes()
    # Simulate a sibling session mid-rotate by pre-creating a fresh lock dir.
    # The hook's stale-lock reclaim only fires for dirs older than 5 minutes,
    # so this freshly-mkdir'd one blocks the rotate.
    lockdir = tmp_path / "cmux-idle-pill.err.lock.d"
    lockdir.mkdir()

    subprocess.run([str(HOOK), "loop-clear"], check=True)
    _poll_lines(fake_cmux, expected=1)

    # Lock was held → rotate skipped → file untouched.
    assert err.read_bytes() == original
    assert lockdir.is_dir()  # hook must not remove a lock it didn't acquire
