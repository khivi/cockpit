"""`cockpit status` lists remembered state, read-only."""

from __future__ import annotations

import json
import os
import time

from cockpit import cli, status
from cockpit.lib import ask_queue, daemon_signal, hidden, seed_queue
from cockpit.lib.seed_queue import SeedRequest
from cockpit.lib.teardown_types import TeardownRequest


def _point(monkeypatch, tmp_path):
    monkeypatch.setattr(hidden, "HIDDEN_PATH", tmp_path / "hidden.json")
    monkeypatch.setattr(seed_queue, "STATE_DIR", tmp_path / "seed")
    monkeypatch.setattr(ask_queue, "STATE_DIR", tmp_path / "ask")
    monkeypatch.setattr(daemon_signal, "STATE_DIR", tmp_path / "close")


def test_empty_state(monkeypatch, tmp_path):
    _point(monkeypatch, tmp_path)
    out = "\n".join(status.render())
    assert "parked repos (0)" in out
    assert "queued asks (0)" in out


def test_lists_every_store_with_age_and_deletes_nothing(monkeypatch, tmp_path):
    _point(monkeypatch, tmp_path)
    (tmp_path / "hidden.json").write_text(json.dumps(["/r/parked"]))
    seed = seed_queue.enqueue(SeedRequest(ref="workspace:1", text="x", cwd="/w/a"))
    ask = ask_queue.enqueue("workspace:2", "hi", "/w/b")
    close = daemon_signal.enqueue(
        TeardownRequest(ref="workspace:3", name="feat", repo_name="svc")
    )
    assert seed is not None and ask is not None and close is not None
    old = time.time() - 2 * 86400
    for p in (seed, ask, close):
        os.utime(p, (old, old))

    out = "\n".join(status.render())

    assert "/r/parked" in out
    assert "workspace:1  /w/a  2d  (stale" in out
    assert "workspace:2  /w/b  2d  (stale" in out
    assert "svc/feat  close  2d  (stale" in out
    assert seed.exists() and ask.exists() and close.exists()


def test_cli_routes_status_and_rejects_args(monkeypatch, tmp_path, capsys):
    _point(monkeypatch, tmp_path)
    assert cli.main(["status"]) == 0
    assert "parked repos" in capsys.readouterr().out
    assert cli.main(["status", "--x"]) == 2
