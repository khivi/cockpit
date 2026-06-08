"""Tests for the `cockpit` console dispatcher (cockpit/cli.py).

Mocks each leaf module's `main` at the dispatch boundary and asserts routing +
argv reshaping. The leaf behaviour is covered by each module's own tests.
"""

from __future__ import annotations

import sys

import pytest

import cockpit.cli as cli


def test_no_args_prints_usage(capsys):
    rc = cli.main([])
    assert rc == 2
    assert "usage: cockpit" in capsys.readouterr().out


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


@pytest.mark.parametrize("sub,mod", [("repos", "repos")])
def test_noarg_subcommands(monkeypatch, sub, mod):
    called = []

    def fake():
        called.append(True)
        return 7

    monkeypatch.setattr(f"cockpit.{mod}.main", fake)
    assert cli.main([sub]) == 7
    assert called == [True]


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
