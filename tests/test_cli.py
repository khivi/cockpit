"""Tests for the `cockpit` console dispatcher (cockpit/cli.py).

Mocks each leaf module's `main` at the dispatch boundary and asserts routing +
argv passing. The leaf behaviour is covered by each module's own tests.
"""

from __future__ import annotations

import pytest

import cockpit.cli as cli


@pytest.fixture(autouse=True)
def _no_real_self_update(monkeypatch):
    # `_self_update_and_reexec` shells out to `cockpit update`. The
    # `_running_as_installed_cockpit` gate already declines under pytest (argv[0]
    # isn't `cockpit`), but as defense-in-depth make a real subprocess fail
    # loudly — no cli test should ever reinstall cockpit on the dev's machine.
    # Update-path tests override subprocess.run with their own recorder.
    def _boom(cmd, *a, **k):
        raise AssertionError(f"cli test shelled out for real: {cmd!r}")

    monkeypatch.setattr("subprocess.run", _boom)


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


def test_close_passes_rest(monkeypatch):
    seen = {}

    def fake(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("cockpit.close.main", fake)
    assert cli.main(["close", "khivi/foo", "--force"]) == 0
    assert seen["argv"] == ["khivi/foo", "--force"]


def test_new_passes_rest(monkeypatch):
    seen = {}

    def fake(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("cockpit.spawn.main", fake)
    assert cli.main(["new", "x", "--force"]) == 0
    assert seen["argv"] == ["x", "--force"]


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


def test_update_sync_routes_to_run_sync_when_installed(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr("cockpit.cli._running_as_installed_cockpit", lambda: True)

    def fake() -> int:
        called["n"] += 1
        return 0

    monkeypatch.setattr("cockpit.lib.updater.run_sync", fake)
    assert cli.main(["update", "--sync"]) == 0
    assert called["n"] == 1


def test_update_sync_declines_when_not_installed(monkeypatch):
    # A dev `uv run` / pytest session (argv[0] isn't the installed console
    # script) must never auto-swap the binary — decline without calling run_sync.
    monkeypatch.setattr("cockpit.cli._running_as_installed_cockpit", lambda: False)

    def _boom() -> int:
        raise AssertionError("run_sync must not run when not installed")

    monkeypatch.setattr("cockpit.lib.updater.run_sync", _boom)
    assert cli.main(["update", "--sync"]) == 0


# --- `u` self-update: run `cockpit update` in a subprocess, then re-exec ----


class _Completed:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def test_watch_restart_updates_in_subprocess_then_reexecs(monkeypatch):
    order: list = []
    monkeypatch.setattr("cockpit.cockpit.main", lambda argv: 42)
    monkeypatch.setattr(cli, "_running_as_installed_cockpit", lambda: True)
    monkeypatch.setattr(
        cli, "_restore_terminal_foreground", lambda: order.append("restore")
    )

    def _run(cmd, *a, **k):
        order.append(("run", cmd))
        return _Completed(0)

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setattr(cli.os, "execvp", lambda f, a: order.append(("exec", f, a)))

    rc = cli.main(["watch", "--once"])
    # Update ran as a fresh `cockpit update` subprocess (not in this process),
    # then the tty foreground is reclaimed, then re-exec onto the new version
    # preserving the watch args — in that order.
    assert order == [
        ("run", ["cockpit", "update"]),
        "restore",
        ("exec", "cockpit", ["cockpit", "watch", "--once"]),
    ]
    assert rc == 0  # execvp mocked → falls through to the unreachable return


def test_watch_restart_reexecs_with_hint_when_update_skipped_noop(monkeypatch, capsys):
    # `cockpit update` found nothing newer in the local plugin cache (it can lag
    # GitHub's default-branch plugin.json). Not the same as "up to date" — still
    # relaunch (quitting the TUI shouldn't strand the user at a shell) but print
    # a hint explaining the header may still say an update is available.
    from cockpit.lib.updater import UPDATE_SKIPPED_NOOP_EXIT

    order: list = []
    monkeypatch.setattr("cockpit.cockpit.main", lambda argv: 42)
    monkeypatch.setattr(cli, "_running_as_installed_cockpit", lambda: True)
    monkeypatch.setattr(
        cli, "_restore_terminal_foreground", lambda: order.append("restore")
    )
    monkeypatch.setattr("time.sleep", lambda s: order.append(("sleep", s)))

    def _run(cmd, *a, **k):
        order.append(("run", cmd))
        return _Completed(UPDATE_SKIPPED_NOOP_EXIT)

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setattr(cli.os, "execvp", lambda f, a: order.append(("exec", f, a)))

    rc = cli.main(["watch", "--once"])
    assert order == [
        ("run", ["cockpit", "update"]),
        ("sleep", 2),
        "restore",
        ("exec", "cockpit", ["cockpit", "watch", "--once"]),
    ]
    assert rc == 0
    assert "retry" in capsys.readouterr().err.lower()


def test_watch_restart_declines_when_not_installed(monkeypatch, capsys):
    monkeypatch.setattr("cockpit.cockpit.main", lambda argv: 42)
    monkeypatch.setattr(cli, "_running_as_installed_cockpit", lambda: False)
    execs = []
    monkeypatch.setattr(cli.os, "execvp", lambda f, a: execs.append((f, a)))

    assert cli.main(["watch"]) == 0  # decline cleanly, leave the dev a shell
    assert execs == []  # no re-exec, no `cockpit update` subprocess (would raise)
    assert "cockpit update" in capsys.readouterr().err


def test_watch_restart_no_reexec_when_update_fails(monkeypatch, capsys):
    monkeypatch.setattr("cockpit.cockpit.main", lambda argv: 42)
    monkeypatch.setattr(cli, "_running_as_installed_cockpit", lambda: True)
    monkeypatch.setattr("subprocess.run", lambda cmd, *a, **k: _Completed(1))
    execs = []
    monkeypatch.setattr(cli.os, "execvp", lambda f, a: execs.append((f, a)))

    assert cli.main(["watch"]) == 1  # surface the failure
    assert execs == []  # a failed update does NOT relaunch
    assert "update failed" in capsys.readouterr().err


def test_watch_restart_reports_exec_failure(monkeypatch, capsys):
    monkeypatch.setattr("cockpit.cockpit.main", lambda argv: 42)
    monkeypatch.setattr(cli, "_running_as_installed_cockpit", lambda: True)
    monkeypatch.setattr(cli, "_restore_terminal_foreground", lambda: None)
    monkeypatch.setattr("subprocess.run", lambda cmd, *a, **k: _Completed(0))

    def boom(file, args):
        raise OSError("cockpit: not found")

    monkeypatch.setattr(cli.os, "execvp", boom)

    rc = cli.main(["watch"])
    assert rc == 1  # exec failed → non-zero, no uncaught traceback
    assert "relaunch failed" in capsys.readouterr().err


def test_watch_clean_exit_does_not_self_update(monkeypatch):
    monkeypatch.setattr("cockpit.cockpit.main", lambda argv: 0)
    execs = []
    monkeypatch.setattr(cli.os, "execvp", lambda f, a: execs.append((f, a)))
    # subprocess.run is the autouse raiser; a clean exit must not reach it.
    assert cli.main(["watch"]) == 0
    assert execs == []  # no re-exec
