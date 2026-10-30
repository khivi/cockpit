"""The brief's baseline roll and diff (`cockpit/lib/brief.py`)."""

from __future__ import annotations

import pytest

from cockpit.lib import brief


def _pr(**kw):
    return {
        "title": "t",
        "state": "OPEN",
        "ci": "SUCCESS",
        "review": "",
        "total": 0,
        **kw,
    }


@pytest.mark.covers("brief.baseline~1")
def test_baseline_rolls_once_per_day_to_the_previous_days_last_record(tmp_path):
    path = tmp_path / "brief.json"
    brief.record({"o_r__pr-1": _pr(ci="PENDING")}, "2026-10-29", path)
    brief.record({"o_r__pr-1": _pr(ci="FAILURE")}, "2026-10-29", path)
    assert "baseline" not in brief.load(path)

    brief.record({"o_r__pr-1": _pr(ci="SUCCESS")}, "2026-10-30", path)
    brief.record({"o_r__pr-1": _pr(ci="PENDING")}, "2026-10-30", path)
    state = brief.load(path)
    assert state["baseline"]["date"] == "2026-10-29"
    assert state["baseline"]["prs"]["o_r__pr-1"]["ci"] == "FAILURE"
    assert state["last"]["prs"]["o_r__pr-1"]["ci"] == "PENDING"


def test_unreadable_state_reads_as_empty(tmp_path):
    path = tmp_path / "brief.json"
    path.write_text("{not json")
    assert brief.load(path) == {}
    brief.record({}, "2026-10-30", path)
    assert brief.load(path)["last"] == {"date": "2026-10-30", "prs": {}}


@pytest.mark.covers("brief.diff~1")
def test_diff_names_each_kind_of_change():
    before = brief.digest(
        {
            "o_r__pr-1": _pr(ci="FAILURE", total=2),
            "o_r__pr-2": _pr(title="old", snoozed="snoozed"),
            "o_r__pr-3": _pr(title="gone"),
            "o_r__pr-5": _pr(total=4),
        }
    )
    after = brief.digest(
        {
            "o_r__pr-1": _pr(ci="SUCCESS", review="APPROVED", total=5),
            "o_r__pr-2": _pr(title="old"),
            "o_r__pr-4": _pr(title="fresh"),
            "o_r__pr-5": _pr(total=1),
        }
    )
    got = {(c.key, c.what) for c in brief.diff(before, after)}
    assert got == {
        ("o_r__pr-1", "ci FAILURE → SUCCESS"),
        ("o_r__pr-1", "review none → APPROVED"),
        ("o_r__pr-1", "+3 review comments"),
        ("o_r__pr-2", "woke from snooze"),
        ("o_r__pr-3", "gone (merged, closed or torn down)"),
        ("o_r__pr-4", "new"),
    }


def test_no_change_is_empty():
    d = brief.digest({"o_r__pr-1": _pr()})
    assert brief.diff(d, d) == []


@pytest.mark.covers("brief.needs-you~1")
def test_needs_you_is_open_nudged_unmuted_unsnoozed():
    payloads = {
        "o_r__pr-1": _pr(nudge="ci"),
        "o_r__pr-2": _pr(nudge="ci", muted="muted"),
        "o_r__pr-3": _pr(nudge="ci", snoozed="snoozed"),
        "o_r__pr-4": _pr(nudge="ci", state="MERGED"),
        "o_r__pr-5": _pr(nudge=""),
    }
    assert [k for k, _ in brief.needs_you(payloads)] == ["o_r__pr-1"]


def test_repo_and_number_restores_the_owner_slash():
    assert brief.repo_and_number("my-org_some_repo__pr-12") == (
        "my-org/some_repo",
        "12",
    )


@pytest.mark.covers("brief.live~1")
def test_live_keeps_only_payloads_the_last_cycle_saw_open():
    payloads = {"o_r__pr-1": _pr(), "o_r__pr-2": _pr(), "gone_x__pr-9": _pr()}
    keys = brief.open_keys({"x/o_r": {1: "t"}, "o_r": {2: "t"}})
    state: dict = {"last": {"prs": {k: {} for k in keys}}}
    assert list(brief.live(payloads, state)) == ["o_r__pr-1"]
    assert brief.live(payloads, {}) == payloads
