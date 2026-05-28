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
    import scripts.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    import scripts.lib.nudges as nudges_mod

    importlib.reload(nudges_mod)
    return nudges_mod


def test_load_pref_returns_defaults_when_missing(nudges):
    pref = nudges.load_pref(42)
    assert pref.disabled_categories == set()
    assert pref.until is None
    assert pref.reason == ""
    assert pref.last_nudge_at == 0.0


def test_save_and_load_roundtrip(nudges):
    pref = nudges.NudgePref(
        disabled_categories={"comments"},
        until=time.time() + 3600,
        reason="copilot",
        last_nudge_at=100.0,
        last_nudge_category="comments",
    )
    nudges.save_pref(99, pref)
    loaded = nudges.load_pref(99)
    assert loaded.disabled_categories == {"comments"}
    assert loaded.reason == "copilot"
    assert loaded.last_nudge_at == 100.0
    assert loaded.last_nudge_category == "comments"


def test_should_nudge_blocked_by_category_mute(nudges):
    pref = nudges.NudgePref(disabled_categories={"comments"})
    nudges.save_pref(7, pref)
    assert nudges.should_nudge(7, "comments") is False
    assert nudges.should_nudge(7, "ci") is True


def test_should_nudge_not_blocked_by_recent_record(nudges):
    """No more time-based throttle — slow loop cadence is the implicit rate
    limit. `record_nudge` still updates `last_nudge_at` for `cockpit nudge
    status` display, but should_nudge does not gate on it."""
    now = 1000.0
    nudges.record_nudge(12, "comments", now=now)
    assert nudges.should_nudge(12, "comments", now=now + 1) is True
    assert nudges.should_nudge(12, "ci", now=now + 1) is True


def test_expired_until_auto_clears_mute(nudges):
    pref = nudges.NudgePref(
        disabled_categories={"comments"}, until=500.0, reason="expired"
    )
    nudges.save_pref(33, pref)
    loaded = nudges.load_pref(33, now=600.0)
    assert loaded.disabled_categories == set()
    assert loaded.until is None
    # Persisted to disk, not just to the returned object.
    reloaded = nudges.load_pref(33, now=601.0)
    assert reloaded.disabled_categories == set()


def test_record_nudge_persists_last_nudge_at_across_reload(
    tmp_path, monkeypatch, nudges
):
    """`last_nudge_at` is still serialized so `cockpit nudge status` can
    display "last nudged X ago" — it just no longer gates future nudges."""
    now = 5000.0
    nudges.record_nudge(77, "ci", now=now)
    pref = nudges.load_pref(77, now=now + 50)
    assert pref.last_nudge_at == now
    assert pref.last_nudge_category == "ci"

    # Simulate full process restart by reloading the module.
    importlib.reload(nudges)
    reloaded = nudges.load_pref(77, now=now + 50)
    assert reloaded.last_nudge_at == now
    assert reloaded.last_nudge_category == "ci"


def test_list_prefs_skips_garbage_files(nudges, tmp_path):
    nudges.save_pref(1, nudges.NudgePref(disabled_categories={"comments"}))
    nudges.save_pref(2, nudges.NudgePref())
    (nudges.NUDGE_DIR / "not-a-pr.json").write_text("garbage")
    (nudges.NUDGE_DIR / "3.json").write_text("not json")

    prefs = nudges.list_prefs()
    assert set(prefs.keys()) == {1, 2}


def test_delete_pref(nudges):
    nudges.save_pref(8, nudges.NudgePref(disabled_categories={"ci"}))
    assert nudges.delete_pref(8) is True
    assert nudges.delete_pref(8) is False  # already gone
    assert nudges.load_pref(8).disabled_categories == set()


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


def test_normalize_categories(nudges):
    assert nudges.normalize_categories(None) == set(nudges.KNOWN_CATEGORIES)
    assert nudges.normalize_categories("") == set(nudges.KNOWN_CATEGORIES)
    assert nudges.normalize_categories("comments") == {"comments"}
    assert nudges.normalize_categories("comments,ci") == {"comments", "ci"}
    with pytest.raises(ValueError) as exc:
        nudges.normalize_categories("typo,ci")
    assert "typo" in str(exc.value)


# ── CLI surface ─────────────────────────────────────────────────────────────


@pytest.fixture
def nudge_cli(nudges):
    import scripts.lib.nudge_cli as cli

    importlib.reload(cli)
    return cli


def test_cli_mute_with_explicit_pr(nudges, nudge_cli, capsys):
    rc = nudge_cli.main(
        ["mute", "100", "--categories", "comments", "--reason", "copilot"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "muted PR #100" in out
    pref = nudges.load_pref(100)
    assert pref.disabled_categories == {"comments"}
    assert pref.reason == "copilot"


def test_cli_mute_without_pr_uses_inference(nudges, nudge_cli, monkeypatch, capsys):
    monkeypatch.setattr(nudge_cli, "_infer_pr_number", lambda: 999)
    rc = nudge_cli.main(["mute", "--categories", "ci", "--until", "1h"])
    assert rc == 0
    pref = nudges.load_pref(999)
    assert pref.disabled_categories == {"ci"}
    assert pref.until is not None
    assert pref.until > time.time()


def test_cli_mute_fails_when_pr_cannot_be_inferred(nudge_cli, monkeypatch, capsys):
    monkeypatch.setattr(nudge_cli, "_infer_pr_number", lambda: None)
    with pytest.raises(SystemExit) as exc:
        nudge_cli.main(["mute"])
    assert exc.value.code == 2
    assert "could not infer" in capsys.readouterr().err


def test_cli_unmute(nudges, nudge_cli, capsys):
    nudges.save_pref(50, nudges.NudgePref(disabled_categories={"comments"}, reason="x"))
    rc = nudge_cli.main(["unmute", "50"])
    assert rc == 0
    assert "unmuted PR #50" in capsys.readouterr().out
    assert nudges.load_pref(50).disabled_categories == set()


def test_cli_list_filters_to_muted(nudges, nudge_cli, capsys):
    nudges.save_pref(1, nudges.NudgePref(disabled_categories={"comments"}))
    nudges.save_pref(2, nudges.NudgePref(last_nudge_at=time.time()))  # not muted
    rc = nudge_cli.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "#1" in out
    assert "#2" not in out


def test_cli_status_reports_last_nudge(nudges, nudge_cli, capsys):
    nudges.record_nudge(60, "comments")
    rc = nudge_cli.main(["status", "60"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #60: not muted" in out
    assert "last nudge" in out


def test_cli_forget_deletes_file(nudges, nudge_cli, capsys):
    nudges.save_pref(70, nudges.NudgePref(disabled_categories={"ci"}))
    rc = nudge_cli.main(["forget", "70"])
    assert rc == 0
    assert nudges.load_pref(70).disabled_categories == set()
    # Second forget reports the absence rather than erroring.
    rc2 = nudge_cli.main(["forget", "70"])
    assert rc2 == 0
    assert "no nudge file" in capsys.readouterr().out


def test_cli_mute_rejects_bad_categories(nudge_cli, capsys):
    rc = nudge_cli.main(["mute", "100", "--categories", "bogus"])
    assert rc == 2
    assert "bogus" in capsys.readouterr().err


def test_cli_mute_rejects_bad_duration(nudge_cli, capsys):
    rc = nudge_cli.main(["mute", "100", "--until", "forever"])
    assert rc == 2
    assert "invalid duration" in capsys.readouterr().err
