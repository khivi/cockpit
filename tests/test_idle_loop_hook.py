"""Behavioural tests for hooks/cmux-idle-pill.sh.

The script shells out to `cmux` via `( command cmux ... & )`. We intercept by
shadowing `cmux` on PATH with a tiny shim that records its argv to a log file,
then assert the recorded calls.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from tests.fixtures import make_shim_on_path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "cmux-idle-pill.sh"


@pytest.fixture
def fake_cmux(tmp_path, monkeypatch) -> Path:
    log = make_shim_on_path(tmp_path, monkeypatch, "cmux")
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "workspace:99")
    return log


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


def test_stop_with_schedulewakeup_sets_loop_and_clears_idle(fake_cmux, tmp_path):
    transcript = _transcript_with(tmp_path, ["ScheduleWakeup"])
    payload = json.dumps({"transcript_path": str(transcript)})
    subprocess.run([str(HOOK), "stop"], input=payload, text=True, check=True)
    calls = _poll_lines(fake_cmux, expected=2)
    assert any("clear-status idle" in c for c in calls), calls
    assert any("set-status loop" in c and "🔄" in c for c in calls), calls
    assert not any("set-status idle" in c for c in calls), calls


def test_stop_with_croncreate_sets_loop_and_clears_idle(fake_cmux, tmp_path):
    transcript = _transcript_with(tmp_path, ["Edit", "CronCreate"])
    payload = json.dumps({"transcript_path": str(transcript)})
    subprocess.run([str(HOOK), "stop"], input=payload, text=True, check=True)
    calls = _poll_lines(fake_cmux, expected=2)
    assert any("set-status loop" in c for c in calls), calls
    assert not any("set-status idle" in c for c in calls), calls


def test_stop_without_loop_tools_clears_loop_and_sets_idle(fake_cmux, tmp_path):
    transcript = _transcript_with(tmp_path, ["Edit", "Read"])
    payload = json.dumps({"transcript_path": str(transcript)})
    subprocess.run([str(HOOK), "stop"], input=payload, text=True, check=True)
    calls = _poll_lines(fake_cmux, expected=2)
    assert any("clear-status loop" in c for c in calls), calls
    assert any("set-status idle" in c for c in calls), calls
    assert not any("set-status loop" in c for c in calls), calls


def test_stop_with_missing_transcript_falls_through_to_idle(fake_cmux, tmp_path):
    payload = json.dumps({"transcript_path": str(tmp_path / "nope.jsonl")})
    subprocess.run([str(HOOK), "stop"], input=payload, text=True, check=True)
    calls = _poll_lines(fake_cmux, expected=2)
    assert any("set-status idle" in c for c in calls), calls
    assert any("clear-status loop" in c for c in calls), calls


def test_no_workspace_id_is_noop(tmp_path, monkeypatch):
    log = make_shim_on_path(tmp_path, monkeypatch, "cmux")
    monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
    subprocess.run([str(HOOK), "loop-set"], check=True)
    time.sleep(0.1)
    assert not log.exists() or log.read_text() == ""
