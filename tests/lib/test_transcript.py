"""`lib.transcript` — real JSONL on tmp_path, per the leaf-module rule.

The records are the shapes Claude Code actually writes (verified against a live
session): an assistant message whose content carries a `tool_use` block, and a
user message whose content carries the answering `tool_result`.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from cockpit.lib import transcript


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Point `CLAUDE_PROJECTS_DIR` at tmp_path and hand back a writer.

    Patched on `lib.transcript`, which imported the name — patching `lib.cache`
    would leave this module's own reference bound to the real home directory.
    """
    monkeypatch.setattr(transcript, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")
    cwd = tmp_path / "worktree"
    cwd.mkdir()

    def write(session: str, *records: dict, age_seconds: float = 0.0) -> None:
        proj = (tmp_path / "projects") / transcript._claude_project_slug(cwd)
        proj.mkdir(parents=True, exist_ok=True)
        path = proj / f"{session}.jsonl"
        path.write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
        )
        if age_seconds:
            stamp = time.time() - age_seconds
            os.utime(path, (stamp, stamp))

    return cwd, write


def _tool_use(call_id: str, name: str = "Bash") -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": call_id, "name": name}],
        },
    }


def _tool_result(call_id: str) -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id}],
        },
    }


def _text(body: str = "done") -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": body}]},
    }


def test_an_answered_call_is_not_in_flight(project):
    cwd, write = project
    write("s1", _tool_use("c1"), _tool_result("c1"), _text())
    assert transcript.turn_in_flight(cwd) is False


def test_an_unanswered_call_is_in_flight(project):
    cwd, write = project
    write("s1", _tool_use("c1"), _tool_result("c1"), _tool_use("c2"))
    assert transcript.turn_in_flight(cwd) is True


def test_a_pending_permission_reads_as_in_flight(project):
    """A prompt awaiting the user is an unanswered `tool_use` like any other.

    Verified live: the id stayed unanswered for the whole window an
    `AskUserQuestion` was on screen. This is why the transcript must never feed
    the send gate — it cannot tell this from an ordinary running call.
    """
    cwd, write = project
    write("s1", _tool_use("c1", name="AskUserQuestion"))
    assert transcript.turn_in_flight(cwd) is True


def test_no_transcript_cannot_be_told(project):
    cwd, _ = project
    assert transcript.turn_in_flight(cwd) is None


def test_a_transcript_past_the_live_window_cannot_be_told(project):
    """Nothing has run here recently enough to have an opinion.

    Reported as None rather than False: the caller must not read the absence of
    a recent session as evidence that one is at rest.
    """
    cwd, write = project
    write(
        "s1",
        _tool_use("c1"),
        _tool_result("c1"),
        age_seconds=transcript.LIVE_WINDOW_SECONDS + 60,
    )
    assert transcript.turn_in_flight(cwd) is None


def test_a_sibling_session_in_flight_answers_for_the_whole_worktree(project):
    """The union, not the newest by mtime.

    A `use_worktree: false` repo hosts several sessions at one cwd. Picking by
    mtime reported at-rest while a sibling was mid-turn — observed on a live
    worktree, which is what this rule is for. `s_busy` is written first so it is
    the OLDER file, i.e. the one an mtime pick would discard.
    """
    cwd, write = project
    write("s_busy", _tool_use("c1"))
    time.sleep(0.01)
    write("s_quiet", _tool_use("c2"), _tool_result("c2"), _text())
    assert transcript.turn_in_flight(cwd) is True


def test_every_session_quiet_is_not_in_flight(project):
    cwd, write = project
    write("s1", _tool_use("c1"), _tool_result("c1"))
    write("s2", _tool_use("c2"), _tool_result("c2"))
    assert transcript.turn_in_flight(cwd) is False


def test_a_half_written_last_line_is_skipped_not_fatal(project):
    """The file is appended live, so the tail can be a partial record."""
    cwd, write = project
    write("s1", _tool_use("c1"), _tool_result("c1"))
    path = next((transcript.CLAUDE_PROJECTS_DIR).glob("*/s1.jsonl"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"type": "assistant", "message": {"content": [{"type": "too')
    assert transcript.turn_in_flight(cwd) is False


def test_an_id_less_block_decides_nothing(project):
    cwd, write = project
    malformed = {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "tool_use"}]},
    }
    write("s1", _tool_use("c1"), _tool_result("c1"), malformed)
    assert transcript.turn_in_flight(cwd) is False


def test_records_without_a_content_list_are_ignored(project):
    """The corpus carries `system`, `mode`, `attachment` and friends, none of
    which have a message content list at all."""
    cwd, write = project
    write(
        "s1",
        {"type": "system", "subtype": "turn_duration", "durationMs": 1200},
        {"type": "mode", "mode": "normal"},
        {"type": "summary", "message": {"content": "a plain string"}},
        _tool_use("c1"),
        _tool_result("c1"),
    )
    assert transcript.turn_in_flight(cwd) is False


def test_an_unreadable_session_cannot_be_told(project):
    cwd, write = project
    write("s1", _tool_use("c1"), _tool_result("c1"))
    path = next((transcript.CLAUDE_PROJECTS_DIR).glob("*/s1.jsonl"))
    path.chmod(0o000)
    try:
        assert transcript.turn_in_flight(cwd) is None
    finally:
        path.chmod(0o644)


def test_in_flight_outranks_an_unreadable_sibling(project):
    """A definite yes needs no second opinion — and refusing is what both
    answers lead to anyway, so the certain one is reported."""
    cwd, write = project
    write("s_busy", _tool_use("c1"))
    write("s_broken", _tool_use("c2"), _tool_result("c2"))
    path = next((transcript.CLAUDE_PROJECTS_DIR).glob("*/s_broken.jsonl"))
    path.chmod(0o000)
    try:
        assert transcript.turn_in_flight(cwd) is True
    finally:
        path.chmod(0o644)
