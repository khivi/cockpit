"""Tests for cockpit.lib.supervisor — locating the cached bin/cockpit.sh and
re-execing `watch` through it.

The cache layout + manifest names are stubbed (`marketplace_name`/`plugin_name`
have their own version tests); `os.execvpe` and the interactive/installed
predicates are stubbed so the re-exec is observed, not performed.
"""

from __future__ import annotations

import os

import pytest

from cockpit.lib import supervisor, version


def _make_cache(tmp_path, versions):
    """Build a fake plugin cache (a cockpit.sh under each version dir) and
    return (claude_config_dir, cache_base)."""
    claude = tmp_path / ".claude"
    base = claude / "plugins" / "cache" / "mk" / "pl"
    for v in versions:
        bin_dir = base / v / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "cockpit.sh").write_text("#!/usr/bin/env bash\n")
    return claude, base


@pytest.fixture
def _names(monkeypatch):
    monkeypatch.setattr(version, "marketplace_name", lambda: "mk")
    monkeypatch.setattr(version, "plugin_name", lambda: "pl")


# --- is_supervised -----------------------------------------------------------


def test_is_supervised_only_on_exact_one(monkeypatch):
    # "1" is the contract; "0"/"false"/empty must NOT count as supervised —
    # a false positive makes `u` exit 42 with nothing catching it.
    monkeypatch.setenv("COCKPIT_SUPERVISED", "1")
    assert supervisor.is_supervised()
    for value in ("0", "false", ""):
        monkeypatch.setenv("COCKPIT_SUPERVISED", value)
        assert not supervisor.is_supervised()
    monkeypatch.delenv("COCKPIT_SUPERVISED")
    assert not supervisor.is_supervised()


# --- supervisor_script -------------------------------------------------------


def test_supervisor_script_picks_newest_by_version(monkeypatch, tmp_path, _names):
    # Numeric sort, not lexical: 0.27.100 must win over 0.27.83.
    claude, base = _make_cache(tmp_path, ["0.27.83", "0.27.100", "0.9.0"])
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    assert supervisor.supervisor_script() == base / "0.27.100" / "bin" / "cockpit.sh"


def test_supervisor_script_none_when_cache_absent(monkeypatch, tmp_path, _names):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    assert supervisor.supervisor_script() is None


def test_supervisor_script_none_when_names_missing(monkeypatch, tmp_path):
    # Unresolvable manifest names ⇒ no cache path to probe.
    monkeypatch.setattr(version, "marketplace_name", lambda: "")
    monkeypatch.setattr(version, "plugin_name", lambda: "pl")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    assert supervisor.supervisor_script() is None


def test_empty_claude_config_dir_falls_back_to_home(monkeypatch, tmp_path, _names):
    # CLAUDE_CONFIG_DIR set-but-empty must fall back to ~/.claude, not probe
    # the cwd (get()'s default only applies when the var is unset).
    claude, base = _make_cache(tmp_path, ["1.0.0"])
    monkeypatch.setenv("HOME", str(tmp_path))  # ~ expands here
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "")
    assert supervisor.supervisor_script() == base / "1.0.0" / "bin" / "cockpit.sh"


# --- reexec_through_supervisor ----------------------------------------------


class _Execed(Exception):
    pass


def _record_execvpe(monkeypatch):
    calls = []

    def fake(path, argv, env):
        calls.append((path, argv, env))
        raise _Execed  # execvpe would replace the process; stop here instead.

    monkeypatch.setattr(supervisor.os, "execvpe", fake)
    return calls


@pytest.fixture
def _reexec_ready(monkeypatch, tmp_path):
    """Arm every guard for the re-exec path; tests then break one at a time."""
    monkeypatch.delenv("COCKPIT_SUPERVISED", raising=False)
    monkeypatch.setattr(supervisor, "_is_interactive", lambda: True)
    monkeypatch.setattr(supervisor, "_is_installed_invocation", lambda: True)
    script = tmp_path / "cockpit.sh"
    monkeypatch.setattr(supervisor, "supervisor_script", lambda: script)
    return script


def test_reexec_noop_when_already_supervised(monkeypatch, _reexec_ready):
    monkeypatch.setenv("COCKPIT_SUPERVISED", "1")
    calls = _record_execvpe(monkeypatch)
    supervisor.reexec_through_supervisor([])
    assert calls == []


def test_reexec_proceeds_when_supervised_is_zero(monkeypatch, _reexec_ready):
    # "0" means NOT supervised — the re-exec must still happen.
    monkeypatch.setenv("COCKPIT_SUPERVISED", "0")
    calls = _record_execvpe(monkeypatch)
    with pytest.raises(_Execed):
        supervisor.reexec_through_supervisor([])
    assert len(calls) == 1


def test_reexec_noop_on_reserved_update_verb(monkeypatch, _reexec_ready):
    # `update` is cockpit.sh's reserved first arg — forwarding it would exec
    # bin/update.sh (a silent full reinstall) instead of launching watch.
    # Fall through so watch's argparse rejects it, as before the supervisor.
    calls = _record_execvpe(monkeypatch)
    supervisor.reexec_through_supervisor(["update"])
    supervisor.reexec_through_supervisor(["update", "--check"])
    assert calls == []


def test_reexec_noop_when_not_interactive(monkeypatch, _reexec_ready):
    monkeypatch.setattr(supervisor, "_is_interactive", lambda: False)
    calls = _record_execvpe(monkeypatch)
    supervisor.reexec_through_supervisor([])
    assert calls == []


def test_reexec_noop_when_not_installed_invocation(monkeypatch, _reexec_ready):
    # A dev's `uv run cockpit watch` (worktree venv) must NOT be exec-swapped
    # for the installed wheel — their local code would silently never run.
    monkeypatch.setattr(supervisor, "_is_installed_invocation", lambda: False)
    calls = _record_execvpe(monkeypatch)
    supervisor.reexec_through_supervisor([])
    assert calls == []


def test_reexec_noop_when_no_script(monkeypatch, _reexec_ready):
    monkeypatch.setattr(supervisor, "supervisor_script", lambda: None)
    calls = _record_execvpe(monkeypatch)
    supervisor.reexec_through_supervisor([])
    assert calls == []


def test_reexec_runs_bash_with_script_args_and_marker(monkeypatch, _reexec_ready):
    script = _reexec_ready
    calls = _record_execvpe(monkeypatch)
    with pytest.raises(_Execed):
        supervisor.reexec_through_supervisor(["--foo"])
    (path, argv, env) = calls[0]
    assert (path, argv) == ("bash", ["bash", str(script), "--foo"])
    # The marker rides the exec'd env only (so even an older cockpit.sh that
    # doesn't export it can't loop) — the live process env is never mutated.
    assert env["COCKPIT_SUPERVISED"] == "1"
    assert "COCKPIT_SUPERVISED" not in os.environ


def test_reexec_falls_back_inline_when_bash_missing(monkeypatch, _reexec_ready):
    # execvpe raising (e.g. no bash) must not crash watch, and the live env
    # must stay clean so `_watch` reports supervised=False.
    def boom(path, argv, env):
        raise OSError("no bash")

    monkeypatch.setattr(supervisor.os, "execvpe", boom)
    supervisor.reexec_through_supervisor([])  # returns, no raise
    assert "COCKPIT_SUPERVISED" not in os.environ


# --- _is_installed_invocation -----------------------------------------------


def test_installed_invocation_matches_path_install(monkeypatch, tmp_path):
    exe = tmp_path / "cockpit"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(supervisor.shutil, "which", lambda name: str(exe))
    monkeypatch.setattr(supervisor.sys, "argv", [str(exe)])
    assert supervisor._is_installed_invocation()


def test_installed_invocation_rejects_other_script(monkeypatch, tmp_path):
    exe = tmp_path / "cockpit"
    exe.write_text("#!/bin/sh\n")
    other = tmp_path / "venv-cockpit"
    other.write_text("#!/bin/sh\n")
    monkeypatch.setattr(supervisor.shutil, "which", lambda name: str(exe))
    monkeypatch.setattr(supervisor.sys, "argv", [str(other)])
    assert not supervisor._is_installed_invocation()


def test_installed_invocation_false_when_not_on_path(monkeypatch):
    monkeypatch.setattr(supervisor.shutil, "which", lambda name: None)
    assert not supervisor._is_installed_invocation()
