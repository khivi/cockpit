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
    assert brief.load(path)["last"] == {
        "date": "2026-10-30",
        "at": "",
        "prs": {},
        "tickets": {},
    }


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


@pytest.mark.covers("brief.tickets~1")
def test_baseline_carries_the_ticket_states_and_timestamp(tmp_path):
    path = tmp_path / "brief.json"
    brief.record(
        {}, "2026-10-29", path, at="2026-10-29T18:00:00", tickets={"A-1": "Todo"}
    )
    brief.record({}, "2026-10-30", path, at="2026-10-30T08:00:00")
    baseline = brief.load(path)["baseline"]
    assert baseline["at"] == "2026-10-29T18:00:00"
    assert baseline["tickets"] == {"A-1": "Todo"}


@pytest.mark.covers("brief.tickets~1")
def test_ticket_states_spans_the_inbox_and_delivered_tickets():
    inboxes = {"org": [{"id": "A-1", "state": "Todo"}, {"title": "no id"}]}
    payloads = {
        "o_r__pr-1": _pr(ticket={"tickets": [{"id": "A-2", "state": "In Review"}]}),
        "o_r__pr-2": _pr(),
    }
    assert brief.ticket_states(inboxes, payloads) == {
        "A-1": "Todo",
        "A-2": "In Review",
    }


@pytest.mark.covers("brief.tickets~1")
def test_ticket_diff_names_new_and_moved_tickets_only():
    before = {"A-1": "Todo", "A-2": "Todo", "A-3": "Todo"}
    after = {"A-1": "Todo", "A-2": "In Progress", "A-4": "Backlog"}
    titles = {"A-2": "Fix login"}
    assert brief.ticket_diff(before, after, titles) == [
        ("A-2", "Fix login", "Todo → In Progress"),
        ("A-4", "", "new (Backlog)"),
    ]


@pytest.mark.covers("brief.tickets~1")
def test_ticket_titles_span_the_inbox_and_delivered_tickets():
    inboxes = {"org": [{"id": "A-1", "title": "Fix login"}]}
    payloads = {"o_r__pr-1": _pr(ticket={"tickets": [{"id": "A-2", "title": "Ship"}]})}
    assert brief.ticket_titles(inboxes, payloads) == {"A-1": "Fix login", "A-2": "Ship"}


@pytest.mark.covers("brief.commits~1")
def test_commits_drop_squash_merges_the_pr_section_reports():
    log = "abc1\tAna\tfeat: x (#12)\nabc2\tBo\tfix: y (#13)\nabc3\tBo\tdirect push\n"
    assert brief.commits(log, {"12"}) == [
        ("abc2", "Bo", "fix: y (#13)"),
        ("abc3", "Bo", "direct push"),
    ]


@pytest.mark.covers("brief.start-here~1")
def test_start_here_ranks_by_kind_and_caps():
    items = [
        ("ticket", "T-1", "new ticket"),
        ("pr", "o/r#1", "ci"),
        ("spawn", "o/r#2", "spawn failed"),
        ("session", "r main", "waiting on permission"),
        ("pr", "o/r#3", "review"),
    ]
    ranked, more = brief.start_here(items, cap=4)
    assert [i[1] for i in ranked] == ["r main", "o/r#1", "o/r#3", "o/r#2"]
    assert more == 1


@pytest.mark.covers("brief.tickets~1")
def test_a_baseline_without_ticket_states_is_not_an_empty_one():
    assert brief.ticket_baseline({"date": "2026-10-29"}) is None
    assert brief.ticket_baseline(None) is None
    assert brief.ticket_baseline({"tickets": {}}) == {}
