"""Tests for the `cockpit` console dispatcher (cockpit/cli.py).

Mocks each leaf module's `main` at the dispatch boundary and asserts routing +
argv reshaping. The leaf behaviour is covered by each module's own tests.
"""

from __future__ import annotations

import sys

import pytest

import cockpit.cli as cli


def test_no_args_defaults_to_watch(monkeypatch):
    seen = {}

    def fake(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("cockpit.cockpit.main", fake)
    assert cli.main([]) == 0
    assert seen["argv"] == ["--watch"]


def test_help_flag_prints_usage(capsys):
    assert cli.main(["--help"]) == 0
    assert "usage: cockpit" in capsys.readouterr().out


def test_unknown_subcommand(capsys):
    assert cli.main(["bogus"]) == 2
    assert "unknown subcommand" in capsys.readouterr().err


@pytest.mark.parametrize(
    "sub,flag",
    [("watch", "--watch"), ("setup", "--setup")],
)
def test_daemon_subcommands_translate_to_flags(monkeypatch, sub, flag):
    seen = {}

    def fake(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("cockpit.cockpit.main", fake)
    assert cli.main([sub]) == 0
    assert seen["argv"] == [flag]


def test_statusline_routes_to_statusline_module(monkeypatch):
    called = []

    def fake():
        called.append(True)
        return 0

    monkeypatch.setattr("cockpit.statusline.main", fake)
    assert cli.main(["statusline"]) == 0
    assert called == [True]


def test_starship_passes_field_with_prog(monkeypatch):
    seen = {}

    def fake(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("cockpit.starship.main", fake)
    assert cli.main(["starship", "model"]) == 0
    assert seen["argv"] == ["cockpit-starship", "model"]


def test_nudge_passes_rest(monkeypatch):
    seen = {}

    def fake(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("cockpit.lib.nudge_cli.main", fake)
    assert cli.main(["nudge", "mute", "ci"]) == 0
    assert seen["argv"] == ["mute", "ci"]


@pytest.mark.parametrize(
    "sub,mod,prog",
    [
        ("new", "spawn", "cockpit-new"),
    ],
)
def test_argv_subcommands_reshape_and_restore(monkeypatch, sub, mod, prog):
    seen = {}

    def fake():
        seen["argv"] = list(sys.argv)
        return 0

    monkeypatch.setattr(f"cockpit.{mod}.main", fake)
    before = list(sys.argv)
    assert cli.main([sub, "x", "--force"]) == 0
    assert seen["argv"] == [prog, "x", "--force"]
    # argv is restored after dispatch — no leak into the caller / next test.
    assert sys.argv == before


# --- update routing --------------------------------------------------------


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["update"], {"skip_install": False, "check_only": False}),
        (["update", "--check"], {"skip_install": False, "check_only": True}),
        (["update", "--skip-install"], {"skip_install": True, "check_only": False}),
    ],
)
def test_update_routes_with_flags(monkeypatch, argv, expected):
    seen: dict[str, bool] = {}

    def fake(skip_install=False, check_only=False):
        seen.update(skip_install=skip_install, check_only=check_only)
        return 0

    monkeypatch.setattr("cockpit.lib.updater.run_update", fake)
    assert cli.main(argv) == 0
    assert seen == expected


# --- `u` self-update: watch exits 42 → update + re-exec --------------------


def test_watch_restart_triggers_update_and_reexec(monkeypatch):
    monkeypatch.setattr("cockpit.cockpit.main", lambda argv: 42)
    monkeypatch.setattr(cli, "_running_as_installed_cockpit", lambda: True)
    updated = []

    def fake_update(*a, **k):
        updated.append(True)
        return 0

    monkeypatch.setattr("cockpit.lib.updater.run_update", fake_update)
    execs = []
    monkeypatch.setattr(cli.os, "execvp", lambda file, args: execs.append((file, args)))

    rc = cli.main(["watch", "--once"])
    assert updated == [True]
    assert execs == [("cockpit", ["cockpit", "watch", "--once"])]
    assert rc == 0  # execvp mocked → falls through to the unreachable return


def test_watch_restart_declines_when_not_installed(monkeypatch, capsys):
    monkeypatch.setattr("cockpit.cockpit.main", lambda argv: 42)
    monkeypatch.setattr(cli, "_running_as_installed_cockpit", lambda: False)
    called = []

    def fake(*a, **k):
        called.append(True)
        return 0

    monkeypatch.setattr("cockpit.lib.updater.run_update", fake)

    assert cli.main(["watch"]) == 0  # decline cleanly, leave the dev a shell
    assert called == []  # updater never invoked
    assert "cockpit update" in capsys.readouterr().err


def test_watch_restart_no_reexec_when_update_fails(monkeypatch):
    monkeypatch.setattr("cockpit.cockpit.main", lambda argv: 42)
    monkeypatch.setattr(cli, "_running_as_installed_cockpit", lambda: True)
    monkeypatch.setattr("cockpit.lib.updater.run_update", lambda *a, **k: 1)
    execs = []
    monkeypatch.setattr(cli.os, "execvp", lambda file, args: execs.append((file, args)))

    assert cli.main(["watch"]) == 1  # surface the failure
    assert execs == []  # do NOT relaunch on a failed update


def test_watch_restart_reports_exec_failure(monkeypatch, capsys):
    monkeypatch.setattr("cockpit.cockpit.main", lambda argv: 42)
    monkeypatch.setattr(cli, "_running_as_installed_cockpit", lambda: True)
    monkeypatch.setattr("cockpit.lib.updater.run_update", lambda *a, **k: 0)

    def boom(file, args):
        raise OSError("cockpit: not found")

    monkeypatch.setattr(cli.os, "execvp", boom)

    rc = cli.main(["watch"])
    assert rc == 1  # exec failed → non-zero, no uncaught traceback
    assert "relaunch failed" in capsys.readouterr().err


def test_watch_clean_exit_does_not_self_update(monkeypatch):
    monkeypatch.setattr("cockpit.cockpit.main", lambda argv: 0)
    called = []

    def fake(*a, **k):
        called.append(True)
        return 0

    monkeypatch.setattr("cockpit.lib.updater.run_update", fake)
    assert cli.main(["watch"]) == 0
    assert called == []
