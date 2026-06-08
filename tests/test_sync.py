"""Tests for cockpit/sync.py — kick a running daemon, else error.

`/cockpit:sync` no longer runs a cycle inline (the `once` path was removed); it
requires a running daemon and reports cleanly when there isn't one.
"""

from __future__ import annotations

import cockpit.sync as sync


def test_sync_returns_0_when_daemon_kicked(monkeypatch):
    monkeypatch.setattr(sync, "kick_running", lambda: True)
    assert sync.main() == 0


def test_sync_errors_when_no_daemon(monkeypatch, capsys):
    monkeypatch.setattr(sync, "kick_running", lambda: False)
    assert sync.main() == 1
    err = capsys.readouterr().err
    assert "no daemon running" in err
    assert "cockpit watch" in err
