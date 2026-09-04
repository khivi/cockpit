"""Tests for the `cockpit` console dispatcher (cockpit/cli.py).

Mocks each leaf module's `main` at the dispatch boundary and asserts routing +
argv passing. The leaf behaviour is covered by each module's own tests.
"""

from __future__ import annotations

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


def test_usage_lists_shims_apart_from_typed_subcommands(capsys):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    head, _, shims = out.partition("config-invoked shims:")
    assert shims, "usage must name the config-invoked shims separately"
    for sub in cli._SUBCOMMANDS:
        assert sub in head
    for sub in cli._SHIM_SUBCOMMANDS:
        assert sub not in head and sub in shims


@pytest.mark.parametrize("sub", cli._SHIM_SUBCOMMANDS)
def test_shims_dispatch_despite_being_off_the_usage_line(monkeypatch, sub, capsys):
    """The two tuples differ only in what `_usage` advertises — a shim left out
    of `_SUBCOMMANDS` must still route, or every installed settings.json /
    starship.toml command string breaks."""
    monkeypatch.setattr("cockpit.statusline.main", lambda: 0)
    monkeypatch.setattr("cockpit.starship.main", lambda argv: 0)
    monkeypatch.setattr("os.execvp", lambda *a: 0)
    assert cli.main([sub, "warm"]) == 0
    assert "unknown subcommand" not in capsys.readouterr().err


def test_version_flag_prints_version(capsys):
    assert cli.main(["--version"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("cockpit ")


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


def test_idle_pill_execs_bundled_script(monkeypatch):
    seen = {}

    def fake_execvp(file, args):
        seen["file"] = file
        seen["args"] = args

    monkeypatch.setattr("os.execvp", fake_execvp)
    assert cli.main(["idle-pill", "stop"]) == 0
    assert seen["file"] == "bash"
    assert seen["args"][0] == "bash"
    assert seen["args"][1].endswith("cockpit/hooks/cmux-idle-pill.sh")
    assert seen["args"][2] == "stop"


def test_idle_pill_missing_script_is_not_fatal(monkeypatch, capsys):
    monkeypatch.setattr(cli, "__file__", "/nonexistent/cli.py")
    # No os.execvp patch: the missing-script guard must return before exec.
    assert cli.main(["idle-pill", "stop"]) == 0
    assert "idle-pill script missing" in capsys.readouterr().err


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


def test_broadcast_passes_rest(monkeypatch):
    seen = {}

    def fake(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("cockpit.broadcast.main", fake)
    assert cli.main(["broadcast", "/compact", "--dry"]) == 0
    assert seen["argv"] == ["/compact", "--dry"]


def test_config_passes_rest(monkeypatch):
    seen = {}

    def fake(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("cockpit.config_cmd.main", fake)
    assert cli.main(["config", "inspect", "--repo", "svc-auth"]) == 0
    assert seen["argv"] == ["inspect", "--repo", "svc-auth"]


def test_diff_passes_rest(monkeypatch):
    seen = {}

    def fake(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("cockpit.diff.main", fake)
    assert cli.main(["diff", "--branch", "--base", "origin/main"]) == 0
    assert seen["argv"] == ["--branch", "--base", "origin/main"]
