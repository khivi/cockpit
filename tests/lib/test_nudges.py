"""Tests for the persistent nudge layer + `cockpit nudge` CLI.

Importing `lib.nudges` pulls in `lib.config`, which reads `COCKPIT_HOME` at
import time. Reload via `importlib.reload` after setting the env var so the
tests are hermetic.
"""

from __future__ import annotations

import importlib
import time

import pytest


@pytest.fixture
def nudges(tmp_path, monkeypatch):
    """Isolated COCKPIT_HOME + reloaded nudges module pointing at it."""
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    import cockpit.lib.nudges as nudges_mod

    importlib.reload(nudges_mod)
    return nudges_mod


def K(nudges, pr_number: int, repo: str = "acme") -> str:
    """The per-repo pref key these tests store under. Prefs are keyed
    `<repo>__<number>` because PR numbers collide across repos."""
    return str(nudges.pref_key(repo, pr_number))


def test_load_pref_returns_defaults_when_missing(nudges):
    pref = nudges.load_pref(K(nudges, 42))
    assert pref.muted is False
    assert pref.until is None
    assert pref.reason == ""
    assert pref.last_nudge_at == 0.0


def test_save_and_load_roundtrip(nudges):
    pref = nudges.NudgePref(
        muted=True,
        until=time.time() + 3600,
        reason="copilot",
        last_nudge_at=100.0,
    )
    nudges.save_pref(K(nudges, 99), pref)
    loaded = nudges.load_pref(K(nudges, 99))
    assert loaded.muted is True
    assert loaded.reason == "copilot"
    assert loaded.last_nudge_at == 100.0


def test_legacy_disabled_categories_ignored(nudges):
    # Pre-boolean files stored a `disabled_categories` set; it is ignored now —
    # only the `muted` boolean is read (absent → not muted).
    assert nudges.NudgePref.from_json({"disabled_categories": ["ci"]}).muted is False


def test_should_nudge_blocked_by_mute(nudges):
    nudges.save_pref(K(nudges, 7), nudges.NudgePref(muted=True))
    assert nudges.should_nudge(K(nudges, 7)) is False
    nudges.save_pref(K(nudges, 7), nudges.NudgePref(muted=False))
    assert nudges.should_nudge(K(nudges, 7)) is True


def test_should_nudge_not_blocked_by_recent_record(nudges):
    """No more time-based throttle — slow loop cadence is the implicit rate
    limit. `record_nudge` still updates `last_nudge_at` for `cockpit nudge
    status` display, but should_nudge does not gate on it."""
    now = 1000.0
    nudges.record_nudge(K(nudges, 12), now=now)
    assert nudges.should_nudge(K(nudges, 12), now=now + 1) is True


def test_expired_until_auto_clears_mute(nudges):
    pref = nudges.NudgePref(muted=True, until=500.0, reason="expired")
    nudges.save_pref(K(nudges, 33), pref)
    loaded = nudges.load_pref(K(nudges, 33), now=600.0)
    assert loaded.muted is False
    assert loaded.until is None
    # Persisted to disk, not just to the returned object.
    reloaded = nudges.load_pref(K(nudges, 33), now=601.0)
    assert reloaded.muted is False


def test_record_nudge_persists_last_nudge_at_across_reload(
    tmp_path, monkeypatch, nudges
):
    """`last_nudge_at` is still serialized so `cockpit nudge status` can
    display "last nudged X ago" — it just no longer gates future nudges."""
    now = 5000.0
    nudges.record_nudge(K(nudges, 77), now=now)
    pref = nudges.load_pref(K(nudges, 77), now=now + 50)
    assert pref.last_nudge_at == now

    # Simulate full process restart by reloading the module.
    importlib.reload(nudges)
    reloaded = nudges.load_pref(K(nudges, 77), now=now + 50)
    assert reloaded.last_nudge_at == now


def test_list_prefs_keys_by_stem_and_skips_garbage_files(nudges, tmp_path):
    nudges.save_pref(K(nudges, 1), nudges.NudgePref(muted=True))
    nudges.save_pref(K(nudges, 2), nudges.NudgePref())
    (nudges.NUDGE_DIR / "not-a-pr.json").write_text("garbage")
    (nudges.NUDGE_DIR / "3.json").write_text("not json")

    prefs = nudges.list_prefs()
    assert set(prefs.keys()) == {"acme__1", "acme__2"}


def test_delete_pref_only_touches_its_own_repos_file(nudges):
    # The whole point of the per-repo key: same number, two repos, two files.
    nudges.save_pref(K(nudges, 10), nudges.NudgePref(muted=True))
    nudges.save_pref(K(nudges, 10, "other"), nudges.NudgePref(snoozed=True))
    assert nudges.delete_pref(K(nudges, 10)) is True
    assert nudges.load_pref(K(nudges, 10)).muted is False
    assert nudges.load_pref(K(nudges, 10, "other")).snoozed is True


def test_a_snooze_in_one_repo_leaves_the_same_number_elsewhere_alone(nudges):
    nudges.save_pref(K(nudges, 10), nudges.NudgePref(snoozed=True, wake_on="0|"))
    assert nudges.should_nudge(K(nudges, 10)) is False
    assert nudges.should_nudge(K(nudges, 10, "other")) is True


def test_load_pref_adopts_a_legacy_global_by_number_file(nudges):
    # Pre-`pref_key` files were keyed by number alone. They are read as a
    # fallback so an existing mute survives the re-key — and never unlinked,
    # since several repos may still be reading the same one.
    nudges.NUDGE_DIR.mkdir(parents=True, exist_ok=True)
    (nudges.NUDGE_DIR / "404.json").write_text('{"muted": true, "reason": "legacy"}')
    pref = nudges.load_pref(K(nudges, 404))
    assert pref.muted is True and pref.reason == "legacy"
    # Every repo adopts it, then diverges on the next write.
    assert nudges.load_pref(K(nudges, 404, "other")).muted is True
    nudges.save_pref(K(nudges, 404), nudges.NudgePref(muted=False))
    assert nudges.load_pref(K(nudges, 404)).muted is False
    assert (nudges.NUDGE_DIR / "404.json").exists()
    assert nudges.load_pref(K(nudges, 404, "other")).muted is True


def test_delete_pref(nudges):
    nudges.save_pref(K(nudges, 8), nudges.NudgePref(muted=True))
    assert nudges.delete_pref(K(nudges, 8)) is True
    assert nudges.delete_pref(K(nudges, 8)) is False  # already gone
    assert nudges.load_pref(K(nudges, 8)).muted is False


def test_parse_duration(nudges):
    assert nudges.parse_duration("30s") == 30
    assert nudges.parse_duration("15m") == 900
    assert nudges.parse_duration("2h") == 7200
    assert nudges.parse_duration("7d") == 604800
    assert nudges.parse_duration("1w") == 604800
    with pytest.raises(ValueError):
        nudges.parse_duration("forever")
    with pytest.raises(ValueError):
        nudges.parse_duration("5x")


# ── CLI surface ─────────────────────────────────────────────────────────────


@pytest.fixture
def nudge_cli(nudges, monkeypatch):
    import cockpit.lib.nudge_cli as cli

    importlib.reload(cli)
    # The CLI keys prefs per repo, resolved from the cwd via `gh repo view`.
    # Pin it to `K`'s repo so no test shells out.
    monkeypatch.setattr(cli, "repo_nwo", lambda p: ("acme-org", "acme"))
    return cli


def test_cli_mute_with_explicit_pr(nudges, nudge_cli, capsys):
    rc = nudge_cli.main(["mute", "100", "--reason", "copilot"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "muted PR #100" in out
    pref = nudges.load_pref(K(nudges, 100))
    assert pref.muted is True
    assert pref.reason == "copilot"


def test_cli_mute_without_pr_uses_inference(nudges, nudge_cli, monkeypatch, capsys):
    monkeypatch.setattr(nudge_cli, "_infer_pr_number", lambda: 999)
    rc = nudge_cli.main(["mute", "--until", "1h"])
    assert rc == 0
    pref = nudges.load_pref(K(nudges, 999))
    assert pref.muted is True
    assert pref.until is not None
    assert pref.until > time.time()


def test_cli_mute_fails_when_pr_cannot_be_inferred(nudge_cli, monkeypatch, capsys):
    monkeypatch.setattr(nudge_cli, "_infer_pr_number", lambda: None)
    with pytest.raises(SystemExit) as exc:
        nudge_cli.main(["mute"])
    assert exc.value.code == 2
    assert "could not infer" in capsys.readouterr().err


def test_cli_unmute(nudges, nudge_cli, capsys):
    nudges.save_pref(K(nudges, 50), nudges.NudgePref(muted=True, reason="x"))
    rc = nudge_cli.main(["unmute", "50"])
    assert rc == 0
    assert "unmuted PR #50" in capsys.readouterr().out
    assert nudges.load_pref(K(nudges, 50)).muted is False


def test_cli_list_filters_to_muted(nudges, nudge_cli, capsys):
    nudges.save_pref(K(nudges, 1), nudges.NudgePref(muted=True))
    nudges.save_pref(
        K(nudges, 2), nudges.NudgePref(last_nudge_at=time.time())
    )  # not muted
    rc = nudge_cli.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "#1" in out
    assert "#2" not in out


def test_cli_status_reports_last_nudge(nudges, nudge_cli, capsys):
    nudges.record_nudge(K(nudges, 60))
    rc = nudge_cli.main(["status", "60"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #60: not muted" in out
    assert "last nudge" in out


def test_cli_forget_deletes_file(nudges, nudge_cli, capsys):
    nudges.save_pref(K(nudges, 70), nudges.NudgePref(muted=True))
    rc = nudge_cli.main(["forget", "70"])
    assert rc == 0
    assert nudges.load_pref(K(nudges, 70)).muted is False
    # Second forget reports the absence rather than erroring.
    rc2 = nudge_cli.main(["forget", "70"])
    assert rc2 == 0
    assert "no nudge file" in capsys.readouterr().out


def test_cli_mute_rejects_bad_duration(nudge_cli, capsys):
    rc = nudge_cli.main(["mute", "100", "--until", "forever"])
    assert rc == 2
    assert "invalid duration" in capsys.readouterr().err


# ── snooze: silences like a mute, but expires on review activity ─────────────


def test_snooze_round_trips_through_json(nudges):
    nudges.save_pref(
        K(nudges, 80), nudges.NudgePref(snoozed=True, wake_on="2|APPROVED")
    )
    pref = nudges.load_pref(K(nudges, 80))
    assert pref.snoozed is True
    assert pref.wake_on == "2|APPROVED"


def test_pref_without_snooze_keys_loads_as_awake(nudges):
    # A pref file written before snooze existed must still load.
    nudges.NUDGE_DIR.mkdir(parents=True, exist_ok=True)
    (nudges.NUDGE_DIR / "81.json").write_text('{"muted": true}')
    pref = nudges.load_pref(K(nudges, 81))
    assert pref.muted is True
    assert pref.snoozed is False
    assert pref.wake_on == ""


def test_snooze_blocks_nudging(nudges):
    nudges.save_pref(K(nudges, 82), nudges.NudgePref(snoozed=True, wake_on="0|"))
    assert nudges.should_nudge(K(nudges, 82)) is False


def test_quiet_covers_both_mute_and_snooze(nudges):
    assert nudges.NudgePref().quiet is False
    assert nudges.NudgePref(muted=True).quiet is True
    assert nudges.NudgePref(snoozed=True).quiet is True


def test_wake_signature_changes_with_comments_or_decision(nudges):
    base = nudges.wake_signature(0, "")
    assert nudges.wake_signature(0, "") == base
    assert nudges.wake_signature(1, "") != base
    assert nudges.wake_signature(0, "APPROVED") != base


def test_snooze_does_not_expire_on_the_clock(nudges):
    # `until` is the mute's expiry; a snooze waits on an event, so a far-past
    # `until` must not silently wake it (only the daemon's signature check does).
    nudges.save_pref(
        K(nudges, 83), nudges.NudgePref(snoozed=True, wake_on="0|", until=1.0)
    )
    assert nudges.load_pref(K(nudges, 83)).snoozed is True


def test_cli_status_reports_a_snooze(nudges, nudge_cli, capsys):
    nudges.save_pref(K(nudges, 84), nudges.NudgePref(snoozed=True, wake_on="0|"))
    assert nudge_cli.main(["status", "84"]) == 0
    assert "snoozed until a new comment or review" in capsys.readouterr().out
