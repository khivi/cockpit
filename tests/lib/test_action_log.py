"""`lib/action_log.py` — the append-only record of what cockpit did."""

from __future__ import annotations

import os
import threading

import pytest

from cockpit.lib import action_log, config
from tests.asserts import read_actions


@pytest.mark.covers("actions.log~1")
def test_record_appends_one_json_line_per_call():
    action_log.record("send", actor="ask", ref="workspace:1", cwd="/w", detail="hi")
    action_log.record("skip", actor="ask", ref="workspace:2", detail="mid-turn")

    rows = read_actions()
    assert [r["action"] for r in rows] == ["send", "skip"]
    assert set(rows[0]) == {
        "ts", "pid", "actor", "action", "ref", "cwd", "session", "detail",
    }  # fmt: skip
    assert rows[0]["pid"] == os.getpid()
    assert rows[1]["detail"] == "mid-turn"
    assert rows[1]["session"] is None


def test_log_lives_in_the_runtime_dir():
    action_log.record("send", actor="x")
    assert (config.COCKPIT_RUNTIME_DIR / "actions.jsonl").is_file()


def test_concurrent_writers_never_interleave_a_line():
    def worker():
        for _ in range(50):
            action_log.record("send", actor="t", detail="x" * 500)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(read_actions()) == 400  # every line parses


def test_rotates_once_past_the_size_cap(monkeypatch):
    monkeypatch.setattr(action_log, "MAX_BYTES", 200)
    for i in range(10):
        action_log.record("send", actor="t", detail=f"{i}" * 50)

    base = config.COCKPIT_RUNTIME_DIR
    assert (base / "actions.jsonl.1").is_file()
    assert not (base / "actions.jsonl.2").exists()
    assert read_actions()  # fresh file keeps receiving


def test_oserror_is_swallowed(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(action_log.os, "open", boom)
    action_log.record("send", actor="t")  # must not raise


def test_a_repeated_skip_logs_once_until_the_reason_or_a_send_changes_it():
    for _ in range(3):
        action_log.record("skip", actor="ask", ref="workspace:1", detail="mid-turn")
    action_log.record("skip", actor="ask", ref="workspace:1", detail="parked")
    action_log.record("skip", actor="seed-retry", ref="workspace:1", detail="parked")
    action_log.record("send", actor="ask", ref="workspace:1", detail="hi")
    action_log.record("skip", actor="ask", ref="workspace:1", detail="parked")

    rows = read_actions()
    assert [(r["actor"], r["action"], r["detail"]) for r in rows] == [
        ("ask", "skip", "mid-turn"),
        ("ask", "skip", "parked"),
        ("seed-retry", "skip", "parked"),
        ("ask", "send", "hi"),
        ("ask", "skip", "parked"),
    ]
