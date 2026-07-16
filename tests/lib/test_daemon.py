"""Tests for the daemon pidfile primitives — focused on `reassert_pidfile`,
the mid-run self-heal that keeps a live daemon reachable to `cockpit close`
after its pidfile is lost."""

from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture
def daemon_mod(tmp_path, monkeypatch):
    """Isolate $COCKPIT_HOME and reload config + daemon so PID_FILE points at
    a fresh tmp path each test (daemon imports PID_FILE at module load)."""
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cfg

    importlib.reload(cfg)
    import cockpit.lib.daemon as daemon

    importlib.reload(daemon)
    return daemon


def test_reassert_writes_when_missing(daemon_mod):
    daemon_mod.reassert_pidfile()
    assert daemon_mod.PID_FILE.read_text() == str(os.getpid())


def test_reassert_noop_when_already_ours(daemon_mod):
    daemon_mod.PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    daemon_mod.PID_FILE.write_text(str(os.getpid()))
    before = daemon_mod.PID_FILE.stat().st_mtime_ns
    daemon_mod.reassert_pidfile()
    # No rewrite on a pidfile that already points at us.
    assert daemon_mod.PID_FILE.stat().st_mtime_ns == before


def test_reassert_reclaims_dead_pid(daemon_mod, monkeypatch):
    daemon_mod.PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    daemon_mod.PID_FILE.write_text("4242")

    def boom(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", boom)
    daemon_mod.reassert_pidfile()
    assert daemon_mod.PID_FILE.read_text() == str(os.getpid())


def test_reassert_reclaims_corrupt_pidfile(daemon_mod):
    daemon_mod.PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    daemon_mod.PID_FILE.write_text("not-an-int")
    daemon_mod.reassert_pidfile()
    assert daemon_mod.PID_FILE.read_text() == str(os.getpid())


def test_reassert_keeps_other_live_daemon(daemon_mod, monkeypatch):
    """A pidfile owned by a *different* live daemon is left untouched — the
    re-assert heals a lost pidfile, it never steals one from a running peer."""
    daemon_mod.PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    daemon_mod.PID_FILE.write_text("4242")
    monkeypatch.setattr(os, "kill", lambda _pid, _sig: None)  # 4242 "alive"
    daemon_mod.reassert_pidfile()
    assert daemon_mod.PID_FILE.read_text() == "4242"
