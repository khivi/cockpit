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
    defaults = {
        "_resolve_pr": lambda arg: (7, "acme", "acme__7"),
        "load_pref": lambda key: NudgePref(),
        "current_branch": lambda cwd: "feature",
        "find_pr_payload_for_cwd": lambda cwd, branch: {
            "total": 3,
            "review": "APPROVED",
            "nudge": "ci",
            "headRefOid": "cafe",
        },
        # Unstacked by default: `_warn_split_chain` reads the repo's cached
        # payloads, and every snooze/wake test would otherwise depend on them.
        "load_pr_payloads_by_branch": lambda repo: {},
    }
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
    assert saved_pref.wake_head == "cafe"
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


def test_snooze_again_re_stamps_and_kicks(capsys):
    # The re-stamp + kick pair is the only thing that converges a pref and a
    # screen that disagree, so a second snooze must not short-circuit into a
    # no-op — that left `already snoozed` as a dead end.
    with (
        patch.multiple(
            nudge_cli,
            **_patched_snooze_collaborators(
                load_pref=lambda key: NudgePref(snoozed=True, wake_on="1|")
            ),
        ),
        patch.object(nudge_cli, "save_pref") as save_pref,
        patch.object(nudge_cli, "restamp_pref") as restamp_pref,
        patch.object(nudge_cli, "kick_running") as kick_running,
    ):
        rc = nudge_cli._cmd_snooze(Namespace(pr=None))
    assert rc == 0
    assert "re-snoozed PR #7" in capsys.readouterr().out
    assert save_pref.call_args[0][1].wake_on == "3|APPROVED"
    restamp_pref.assert_called_once()
    kick_running.assert_called_once_with(quiet=True)


def test_re_snooze_keeps_its_wake_snapshots_when_no_payload_is_cached():
    # Blanking a live `wake_on` to "0|" would wake the snooze on the next tick —
    # the opposite of what a re-snooze was asked for.
    with (
        patch.multiple(
            nudge_cli,
            **_patched_snooze_collaborators(
                load_pref=lambda key: NudgePref(
                    snoozed=True, wake_on="3|APPROVED", wake_nudge="ci"
                ),
                find_pr_payload_for_cwd=lambda cwd, branch: None,
            ),
        ),
        patch.object(nudge_cli, "save_pref") as save_pref,
        patch.object(nudge_cli, "restamp_pref"),
        patch.object(nudge_cli, "kick_running"),
    ):
        nudge_cli._cmd_snooze(Namespace(pr=None))
    saved_pref = save_pref.call_args[0][1]
    assert saved_pref.wake_on == "3|APPROVED"
    assert saved_pref.wake_nudge == "ci"


def test_wake_clears_snooze_fields():
    with (
        patch.multiple(
            nudge_cli,
            **_patched_snooze_collaborators(
                load_pref=lambda key: NudgePref(
                    snoozed=True,
                    wake_on="3|APPROVED",
                    wake_nudge="ci",
                    wake_head="cafe",
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
    assert saved_pref.wake_head == ""
    restamp_pref.assert_called_once()
    kick_running.assert_called_once_with(quiet=True)


def test_wake_when_already_awake_still_re_stamps(capsys):
    # Same convergence argument as the re-snooze above, in the other direction:
    # a row stuck folded is exactly when someone runs `wake` a second time.
    with (
        patch.multiple(nudge_cli, **_patched_snooze_collaborators()),
        patch.object(nudge_cli, "save_pref") as save_pref,
        patch.object(nudge_cli, "restamp_pref") as restamp_pref,
        patch.object(nudge_cli, "kick_running") as kick_running,
    ):
        rc = nudge_cli._cmd_wake(Namespace(pr=None))
    assert rc == 0
    assert "already awake" in capsys.readouterr().out
    assert save_pref.call_args[0][1].snoozed is False
    restamp_pref.assert_called_once()
    kick_running.assert_called_once_with(quiet=True)


# ── the stacked-chain warning — a per-PR snooze below the tip moves nothing ──


def _stacked_payloads(_repo: str) -> dict:
    # `feature` is the root; #8's `tip` branch is stacked on top of it.
    return {
        "feature": {"number": 7, "base": "main", "state": "OPEN"},
        "tip": {"number": 8, "base": "feature", "state": "OPEN"},
    }


def _prefs(**snoozed_by_key):
    return lambda key: NudgePref(snoozed=snoozed_by_key.get(key, False))


def test_snooze_below_an_unsnoozed_tip_names_the_pr_that_has_to_agree(capsys):
    with (
        patch.multiple(
            nudge_cli,
            **_patched_snooze_collaborators(
                load_pr_payloads_by_branch=_stacked_payloads,
                load_pref=_prefs(),
            ),
        ),
        patch.object(nudge_cli, "save_pref"),
        patch.object(nudge_cli, "restamp_pref"),
        patch.object(nudge_cli, "kick_running"),
    ):
        nudge_cli._cmd_snooze(Namespace(pr=None))
    out = capsys.readouterr().out
    assert "stacked under #8 (tip), which is not snoozed" in out
    assert "cockpit nudge snooze 8" in out


def test_no_warning_when_the_tip_already_agrees(capsys):
    with (
        patch.multiple(
            nudge_cli,
            **_patched_snooze_collaborators(
                load_pr_payloads_by_branch=_stacked_payloads,
                load_pref=_prefs(acme__8=True),
            ),
        ),
        patch.object(nudge_cli, "save_pref"),
        patch.object(nudge_cli, "restamp_pref"),
        patch.object(nudge_cli, "kick_running"),
    ):
        nudge_cli._cmd_snooze(Namespace(pr=None))
    assert "stacked under" not in capsys.readouterr().out


def test_no_warning_on_the_tip_itself(capsys):
    with (
        patch.multiple(
            nudge_cli,
            **_patched_snooze_collaborators(
                _resolve_pr=lambda arg: (8, "acme", "acme__8"),
                current_branch=lambda cwd: "tip",
                load_pr_payloads_by_branch=_stacked_payloads,
                load_pref=_prefs(),
            ),
        ),
        patch.object(nudge_cli, "save_pref"),
        patch.object(nudge_cli, "restamp_pref"),
        patch.object(nudge_cli, "kick_running"),
    ):
        nudge_cli._cmd_snooze(Namespace(pr=None))
    assert "stacked under" not in capsys.readouterr().out


def test_wake_below_a_still_snoozed_tip_warns_the_row_stays_folded(capsys):
    with (
        patch.multiple(
            nudge_cli,
            **_patched_snooze_collaborators(
                load_pr_payloads_by_branch=_stacked_payloads,
                load_pref=_prefs(acme__7=True, acme__8=True),
            ),
        ),
        patch.object(nudge_cli, "save_pref"),
        patch.object(nudge_cli, "restamp_pref"),
        patch.object(nudge_cli, "kick_running"),
    ):
        nudge_cli._cmd_wake(Namespace(pr=None))
    out = capsys.readouterr().out
    assert "stacked under #8 (tip), which is still snoozed" in out
    assert "cockpit nudge wake 8" in out


def test_a_merged_parent_is_not_a_chain(capsys):
    # `find_stacks` excludes non-OPEN PRs for the same reason: once the bottom
    # lands, what was stacked on it is just a PR on the trunk.
    with (
        patch.multiple(
            nudge_cli,
            **_patched_snooze_collaborators(
                load_pr_payloads_by_branch=lambda repo: {
                    "feature": {"number": 7, "base": "main", "state": "OPEN"},
                    "tip": {"number": 8, "base": "feature", "state": "MERGED"},
                },
                load_pref=_prefs(),
            ),
        ),
        patch.object(nudge_cli, "save_pref"),
        patch.object(nudge_cli, "restamp_pref"),
        patch.object(nudge_cli, "kick_running"),
    ):
        nudge_cli._cmd_snooze(Namespace(pr=None))
    assert "stacked under" not in capsys.readouterr().out


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
