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
from argparse import Namespace
from unittest.mock import patch

import pytest

import cockpit.lib.nudge_cli as nudge_cli
from cockpit.lib.nudges import NudgePref


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


def test_resolve_pr_explicit_arg_skips_gh_pr_view():
    # The number is given, but the *repo* still has to be resolved — a pref key
    # is per-repo, so `gh repo view` runs either way.
    with (
        patch("subprocess.run") as run,
        patch.object(nudge_cli, "repo_nwo", return_value=("acme-org", "acme")),
    ):
        assert nudge_cli._resolve_pr(42) == (42, "acme", "acme__42")
    run.assert_not_called()


def test_resolve_pr_falls_back_to_gh_when_no_arg():
    with (
        patch("subprocess.run", return_value=_completed(stdout="55\n")),
        patch.object(nudge_cli, "repo_nwo", return_value=("acme-org", "acme")),
    ):
        assert nudge_cli._resolve_pr(None) == (55, "acme", "acme__55")


def test_resolve_pr_exits_2_when_the_repo_cannot_be_resolved(capsys):
    # Off-GitHub / outside a checkout: there is no repo to scope the pref to, and
    # falling back to a bare number would silently re-share it across repos.
    with (
        patch.object(nudge_cli, "repo_nwo", side_effect=RuntimeError("gh failed")),
        pytest.raises(SystemExit) as exc,
    ):
        nudge_cli._resolve_pr(42)
    assert exc.value.code == 2
    assert "keyed per repo" in capsys.readouterr().err


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


# ── snooze / wake — mirror the TUI's `z`, mocked at the collaborator boundary ─


def _patched_snooze_collaborators(**overrides):
    defaults = dict(
        _resolve_pr=lambda arg: (7, "acme", "acme__7"),
        load_pref=lambda key: NudgePref(),
        current_branch=lambda cwd: "feature",
        find_pr_payload_for_cwd=lambda cwd, branch: {
            "total": 3,
            "review": "APPROVED",
            "nudge": "ci",
        },
    )
    defaults.update(overrides)
    return defaults


def test_snooze_stamps_wake_signature_and_kicks_daemon():
    with (
        patch.multiple(nudge_cli, **_patched_snooze_collaborators()),
        patch.object(nudge_cli, "save_pref") as save_pref,
        patch.object(nudge_cli, "restamp_pref") as restamp_pref,
        patch.object(nudge_cli, "kick_running") as kick_running,
    ):
        rc = nudge_cli._cmd_snooze(Namespace(pr=None))
    assert rc == 0
    saved_key, saved_pref = save_pref.call_args[0]
    assert saved_key == "acme__7"
    assert saved_pref.snoozed is True
    assert saved_pref.wake_on == "3|APPROVED"
    assert saved_pref.wake_nudge == "ci"
    restamp_pref.assert_called_once()
    kick_running.assert_called_once_with(quiet=True)


def test_snooze_clears_an_existing_mute():
    with (
        patch.multiple(
            nudge_cli,
            **_patched_snooze_collaborators(
                load_pref=lambda key: NudgePref(muted=True, reason="copilot"),
            ),
        ),
        patch.object(nudge_cli, "save_pref") as save_pref,
        patch.object(nudge_cli, "restamp_pref"),
        patch.object(nudge_cli, "kick_running"),
    ):
        nudge_cli._cmd_snooze(Namespace(pr=None))
    saved_pref = save_pref.call_args[0][1]
    assert saved_pref.muted is False
    assert saved_pref.reason == ""


def test_snooze_already_snoozed_is_a_noop(capsys):
    with (
        patch.multiple(
            nudge_cli,
            **_patched_snooze_collaborators(
                load_pref=lambda key: NudgePref(snoozed=True)
            ),
        ),
        patch.object(nudge_cli, "save_pref") as save_pref,
        patch.object(nudge_cli, "restamp_pref") as restamp_pref,
        patch.object(nudge_cli, "kick_running") as kick_running,
    ):
        rc = nudge_cli._cmd_snooze(Namespace(pr=None))
    assert rc == 0
    assert "already snoozed" in capsys.readouterr().out
    save_pref.assert_not_called()
    restamp_pref.assert_not_called()
    kick_running.assert_not_called()


def test_wake_clears_snooze_fields():
    with (
        patch.multiple(
            nudge_cli,
            **_patched_snooze_collaborators(
                load_pref=lambda key: NudgePref(
                    snoozed=True, wake_on="3|APPROVED", wake_nudge="ci"
                ),
            ),
        ),
        patch.object(nudge_cli, "save_pref") as save_pref,
        patch.object(nudge_cli, "restamp_pref") as restamp_pref,
        patch.object(nudge_cli, "kick_running") as kick_running,
    ):
        rc = nudge_cli._cmd_wake(Namespace(pr=None))
    assert rc == 0
    saved_pref = save_pref.call_args[0][1]
    assert saved_pref.snoozed is False
    assert saved_pref.wake_on == ""
    assert saved_pref.wake_nudge == ""
    restamp_pref.assert_called_once()
    kick_running.assert_called_once_with(quiet=True)


def test_wake_when_not_snoozed_is_a_noop(capsys):
    with (
        patch.multiple(nudge_cli, **_patched_snooze_collaborators()),
        patch.object(nudge_cli, "save_pref") as save_pref,
        patch.object(nudge_cli, "restamp_pref") as restamp_pref,
        patch.object(nudge_cli, "kick_running") as kick_running,
    ):
        rc = nudge_cli._cmd_wake(Namespace(pr=None))
    assert rc == 0
    assert "not snoozed" in capsys.readouterr().out
    save_pref.assert_not_called()
    restamp_pref.assert_not_called()
    kick_running.assert_not_called()


# ── argparse routing smoke test — every subcommand parses and dispatches ───


@pytest.mark.parametrize(
    "argv,func_name",
    [
        (["mute", "1"], "_cmd_mute"),
        (["unmute", "1"], "_cmd_unmute"),
        (["snooze", "1"], "_cmd_snooze"),
        (["wake", "1"], "_cmd_wake"),
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
