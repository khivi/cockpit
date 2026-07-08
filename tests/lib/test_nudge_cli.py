"""Tests for cockpit/lib/nudge_cli.py — the `cockpit nudge` CLI entry point.

CLI entry-point layer: mock at the `gh pr view` subprocess boundary (the
transport `_infer_pr_number` shells out to). `tests/lib/test_nudges.py`
already covers the underlying `nudges` behaviour (mute/unmute/list/status/
forget) and stubs `_infer_pr_number` itself for its own tests — so the real
gh-fallback subprocess path was never exercised. This file fills that gap
plus a routing smoke test for every subcommand.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

import cockpit.lib.nudge_cli as nudge_cli


def _completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout)


# ── _infer_pr_number — the gh subprocess boundary ───────────────────────────


def test_infer_pr_number_happy_path():
    with patch("subprocess.run", return_value=_completed(stdout="123\n")):
        assert nudge_cli._infer_pr_number() == 123


def test_infer_pr_number_gh_failure_is_none():
    with patch("subprocess.run", return_value=_completed(returncode=1, stdout="123")):
        assert nudge_cli._infer_pr_number() is None


def test_infer_pr_number_empty_stdout_is_none():
    with patch("subprocess.run", return_value=_completed(stdout="")):
        assert nudge_cli._infer_pr_number() is None


def test_infer_pr_number_non_int_stdout_is_none():
    with patch("subprocess.run", return_value=_completed(stdout="not-a-number")):
        assert nudge_cli._infer_pr_number() is None


# ── _resolve_pr — explicit arg bypasses gh; fallback exits 2 on failure ─────


def test_resolve_pr_explicit_arg_skips_gh():
    with patch("subprocess.run") as run:
        assert nudge_cli._resolve_pr(42) == 42
    run.assert_not_called()


def test_resolve_pr_falls_back_to_gh_when_no_arg():
    with patch("subprocess.run", return_value=_completed(stdout="55\n")):
        assert nudge_cli._resolve_pr(None) == 55


def test_resolve_pr_exits_2_when_gh_fails_and_no_pr_given(capsys):
    with (
        patch("subprocess.run", return_value=_completed(returncode=1)),
        pytest.raises(SystemExit) as exc,
    ):
        nudge_cli._resolve_pr(None)
    assert exc.value.code == 2
    assert "could not infer" in capsys.readouterr().err


def test_resolve_pr_exits_2_when_gh_returns_no_pr(capsys):
    with (
        patch("subprocess.run", return_value=_completed(stdout="")),
        pytest.raises(SystemExit) as exc,
    ):
        nudge_cli._resolve_pr(None)
    assert exc.value.code == 2
    assert "could not infer" in capsys.readouterr().err


# ── mute --until parse errors ────────────────────────────────────────────


def test_mute_rejects_invalid_duration(capsys):
    # An explicit PR number is given, so this never touches gh or on-disk
    # nudge storage — the parse error short-circuits first.
    rc = nudge_cli.main(["mute", "10", "--until", "bogus"])
    assert rc == 2
    assert "invalid duration" in capsys.readouterr().err


# ── argparse routing smoke test — every subcommand parses and dispatches ───


@pytest.mark.parametrize(
    "argv,func_name",
    [
        (["mute", "1"], "_cmd_mute"),
        (["unmute", "1"], "_cmd_unmute"),
        (["list"], "_cmd_list"),
        (["status", "1"], "_cmd_status"),
        (["forget", "1"], "_cmd_forget"),
    ],
)
def test_subcommand_routes_to_expected_handler(argv, func_name, monkeypatch):
    seen = {}

    def fake(args):
        seen["called"] = True
        return 0

    monkeypatch.setattr(nudge_cli, func_name, fake)
    assert nudge_cli.main(argv) == 0
    assert seen.get("called") is True


def test_no_subcommand_errors():
    with pytest.raises(SystemExit) as exc:
        nudge_cli.main([])
    assert exc.value.code == 2  # required=True subparsers reject a bare invocation
