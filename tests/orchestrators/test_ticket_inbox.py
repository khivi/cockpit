"""Tests for cockpit/orchestrators/ticket_inbox.py — the accumulator the slow
tick fills per repo and drains once.

Orchestrator layer, so the provider is mocked at the `TicketProvider` boundary:
what's under test is the grouping (one fetch per provider+credential+bucket), the
partial guard, and the `in_flight` stamp — not the four leaf fetches, which have
their own tests against their own transports.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cockpit.orchestrators.ticket_inbox import TicketInbox, publish


class _Provider:
    """A `TicketProvider` stand-in recording every `fetch_my_open` call."""

    def __init__(self, name="linear", result=None, results=None, done=()):
        self.name = name
        self._result = result if result is not None else []
        self._results = list(results) if results is not None else None
        self._done = list(done)
        self.calls: list[dict] = []

    def done_values(self, cfg, repo_entry):
        return list(self._done)

    def fetch_my_open(self, scopes, *, nwos, cfg, repo_entry, states=None):
        self.calls.append(
            {
                "scopes": list(scopes),
                "nwos": list(nwos),
                "repo": repo_entry,
                "states": states,
            }
        )
        if self._results is not None:
            return self._results.pop(0)
        return self._result


def _ticket(tid: str, updated: str = "2026-09-09T00:00:00Z") -> dict:
    return {
        "id": tid,
        "team": tid.split("-")[0],
        "title": f"Work on {tid}",
        "state": "Todo",
        "url": f"https://linear.app/acme/issue/{tid}",
        "updated_at": updated,
    }


def _repo(name: str) -> dict:
    return {"name": name, "path": f"/repos/{name}"}


def _inbox(*repos, partial=False, active=None) -> TicketInbox:
    inbox = TicketInbox(partial=partial, active=set(active or ()))
    for kwargs in repos:
        inbox.add(**kwargs)
    return inbox


def _entry(bucket="acme", *, name="widgets", scopes=("PE",), cred=("LINEAR_API_KEY",)):
    return {
        "bucket": bucket,
        "provider_name": "linear",
        "cred": cred,
        "scopes": scopes,
        "nwo": f"acme/{name}",
        "repo_entry": _repo(name),
    }


@pytest.fixture
def provider():
    prov = _Provider()
    with patch("cockpit.orchestrators.ticket_inbox.provider_for", return_value=prov):
        yield prov


@pytest.fixture
def written():
    with patch("cockpit.orchestrators.ticket_inbox.write_ticket_inbox") as w:
        yield w


# ── grouping ────────────────────────────────────────────────────────────────


def test_repos_sharing_a_credential_and_bucket_cost_one_fetch(provider, written):
    """The whole point: five repos on one Linear workspace, one round-trip."""
    provider._result = [_ticket("PE-1")]
    inbox = _inbox(
        _entry(name="widgets", scopes=("PE",)),
        _entry(name="tools", scopes=("ENG",)),
        _entry(name="docs", scopes=("PE",)),
    )
    out = publish(inbox, {})

    assert len(provider.calls) == 1
    # the union of every member's scopes, deduped and sorted
    assert provider.calls[0]["scopes"] == ["ENG", "PE"]
    assert provider.calls[0]["nwos"] == ["acme/docs", "acme/tools", "acme/widgets"]
    assert list(out) == ["acme"]


def test_two_credentials_in_one_bucket_are_fetched_separately(provider, written):
    """Asking one workspace about another's ticket answers about a different
    issue that merely shares an identifier."""
    provider._results = [[_ticket("PE-1")], [_ticket("ENG-2")]]
    inbox = _inbox(
        _entry(name="a", cred=("LINEAR_API_KEY",)),
        _entry(name="b", cred=("OTHER_LINEAR_KEY",)),
    )
    out = publish(inbox, {})

    assert len(provider.calls) == 2
    assert [t["id"] for t in out["acme"]] == ["PE-1", "ENG-2"]  # merged into one bucket


def test_two_buckets_on_one_credential_are_fetched_separately(provider, written):
    """The payload keys by org, so a shared credential still writes two files."""
    provider._results = [[_ticket("PE-1")], [_ticket("ENG-2")]]
    inbox = _inbox(_entry(bucket="acme"), _entry(bucket="widgets-co", name="w"))
    out = publish(inbox, {})

    assert len(provider.calls) == 2
    assert sorted(out) == ["acme", "widgets-co"]
    assert {c.args[0] for c in written.call_args_list} == {"acme", "widgets-co"}


def test_a_repo_with_no_bucket_is_not_recorded():
    inbox = _inbox(_entry(bucket=""))
    assert inbox.repos == []


# ── the partial guard ───────────────────────────────────────────────────────


def test_partial_cycle_writes_nothing(provider, written):
    """A repo that never reported makes its bucket *shrink*, which reads as
    tickets having been finished."""
    provider._result = [_ticket("PE-1")]
    out = publish(_inbox(_entry(), partial=True), {})

    assert out == {}
    written.assert_not_called()


def test_a_failed_fetch_suspends_only_its_own_bucket(provider, written):
    provider._results = [None, [_ticket("ENG-2")]]
    inbox = _inbox(_entry(bucket="acme"), _entry(bucket="widgets-co", name="w"))
    out = publish(inbox, {})

    assert list(out) == ["widgets-co"]
    assert [c.args[0] for c in written.call_args_list] == ["widgets-co"]


def test_a_failed_group_suspends_a_bucket_its_sibling_group_filled(provider, written):
    """A half-filled bucket is still short, so it keeps the payload it had."""
    provider._results = [[_ticket("PE-1")], None]
    inbox = _inbox(
        _entry(name="a", cred=("KEY_A",)),
        _entry(name="b", cred=("KEY_B",)),
    )
    assert publish(inbox, {}) == {}
    written.assert_not_called()


def test_an_empty_answer_is_written_not_suspended(provider, written):
    """`[]` is "asked, nothing assigned" — a real state the inbox must show."""
    provider._result = []
    out = publish(_inbox(_entry()), {})

    assert out == {"acme": []}
    written.assert_called_once_with("acme", [])


def test_a_repo_with_no_provider_is_skipped(written):
    with patch("cockpit.orchestrators.ticket_inbox.provider_for", return_value=None):
        assert publish(_inbox(_entry()), {}) == {}
    written.assert_not_called()


# ── dedup, ordering, in_flight ──────────────────────────────────────────────


def test_a_ticket_seen_by_two_groups_appears_once(provider, written):
    provider._results = [[_ticket("PE-1")], [_ticket("pe-1"), _ticket("PE-2")]]
    inbox = _inbox(_entry(name="a", cred=("A",)), _entry(name="b", cred=("B",)))
    out = publish(inbox, {})

    assert [t["id"] for t in out["acme"]] == ["PE-1", "PE-2"]  # casefolded dedup


def test_tickets_are_newest_first(provider, written):
    provider._result = [
        _ticket("PE-old", "2026-09-01T00:00:00Z"),
        _ticket("PE-new", "2026-09-09T00:00:00Z"),
    ]
    out = publish(_inbox(_entry()), {})
    assert [t["id"] for t in out["acme"]] == ["PE-new", "PE-old"]


def test_in_flight_is_stamped_on_every_ticket_including_false(provider, written):
    """Always written, both values — a conditional stamp would leave a row
    advertising work you already started."""
    provider._result = [_ticket("PE-1"), _ticket("PE-2")]
    out = publish(_inbox(_entry(), active={"pe-1"}), {})

    assert [(t["id"], t["in_flight"]) for t in out["acme"]] == [
        ("PE-1", True),
        ("PE-2", False),
    ]


def test_in_flight_matches_casefolded(provider, written):
    provider._result = [_ticket("PE-1")]
    out = publish(_inbox(_entry(), active={"PE-1".casefold()}), {})
    assert out["acme"][0]["in_flight"] is True


# ── dry ─────────────────────────────────────────────────────────────────────


def test_dry_reports_without_writing(provider, written):
    provider._result = [_ticket("PE-1")]
    out = publish(_inbox(_entry()), {}, dry=True)

    assert [t["id"] for t in out["acme"]] == ["PE-1"]
    written.assert_not_called()


# ── active_ids — the in-flight signal, shared by both ticks ─────────────────


def test_active_ids_reads_the_branch_slug():
    from cockpit.orchestrators.ticket_inbox import active_ids

    assert active_ids(["khivi/pe-412-fix-login"], []) == {"pe-412"}


def test_active_ids_reads_a_delivery_footer():
    """Provider-neutral, which is what covers a Trello short link and a GitHub
    `owner/repo#N` — neither ever appears in a branch name."""
    from cockpit.orchestrators.ticket_inbox import active_ids

    assert active_ids([], ["aB3dZ9", "acme/widgets#77"]) == {
        "ab3dz9",
        "acme/widgets#77",
    }


def test_active_ids_unions_both_signals():
    from cockpit.orchestrators.ticket_inbox import active_ids

    assert active_ids(["khivi/PE-1-x"], ["ENG-2"]) == {"pe-1", "eng-2"}


def test_active_ids_ignores_branches_with_no_ticket_and_empty_ids():
    from cockpit.orchestrators.ticket_inbox import active_ids

    assert active_ids(["main", "khivi/no-ticket", ""], ["", ""]) == set()


# ── the done-state filter ───────────────────────────────────────────────────


def test_a_ticket_in_a_done_state_is_dropped(written):
    """`dev_done` and `merge_done` are the two states the user has already named
    as "not work to start" — a tracker's own active filter doesn't answer that,
    since a workspace whose review column is typed `started` reports a merged
    ticket as assigned and active forever."""
    prov = _Provider(
        result=[
            _ticket("PE-1"),
            {**_ticket("PE-2"), "state": "In Review"},
            {**_ticket("PE-3"), "state": "merged"},  # casefolded
        ],
        done=["In Review", "Merged"],
    )
    with patch("cockpit.orchestrators.ticket_inbox.provider_for", return_value=prov):
        out = publish(_inbox(_entry()), {})
    assert [t["id"] for t in out["acme"]] == ["PE-1"]


def test_no_configured_done_states_filters_nothing(written):
    """GitHub's `done_values` is empty — an issue is open or closed, and the
    fetch already asked only for the open ones."""
    prov = _Provider(result=[_ticket("PE-1"), {**_ticket("PE-2"), "state": "Done"}])
    with patch("cockpit.orchestrators.ticket_inbox.provider_for", return_value=prov):
        out = publish(_inbox(_entry()), {})
    assert [t["id"] for t in out["acme"]] == ["PE-1", "PE-2"]


def test_a_ticket_with_no_state_survives_the_filter(written):
    prov = _Provider(result=[{**_ticket("PE-1"), "state": ""}], done=["Done"])
    with patch("cockpit.orchestrators.ticket_inbox.provider_for", return_value=prov):
        out = publish(_inbox(_entry()), {})
    assert [t["id"] for t in out["acme"]] == ["PE-1"]


def test_a_bucket_emptied_by_the_filter_is_still_written(written):
    """Distinct from a failed fetch: the tracker answered, and the answer is that
    everything assigned to me is finished."""
    prov = _Provider(result=[{**_ticket("PE-1"), "state": "Done"}], done=["Done"])
    with patch("cockpit.orchestrators.ticket_inbox.provider_for", return_value=prov):
        out = publish(_inbox(_entry()), {})
    assert out == {"acme": []}
    written.assert_called_once_with("acme", [])


# ── tickets.inbox_states: the whole filter, when set ────────────────────────


def _states_entry(name: str, states, **over):
    entry = _entry(name=name, **over)
    entry["repo_entry"] = {
        "name": name,
        "path": f"/repos/{name}",
        "tickets": {"inbox_states": states},
    }
    return entry


def test_inbox_states_reach_the_fetch_as_the_group_union(provider, written):
    """Resolved per repo, unioned across the group like `scopes` — the fetch is
    shared, so an override can only widen what the shared call asks for."""
    inbox = _inbox(
        _states_entry("widgets", ["Backlog"]),
        _states_entry("tools", ["Backlog", "In Review"]),
    )
    publish(inbox, {})
    assert provider.calls[0]["states"] == ["Backlog", "In Review"]


def test_unset_inbox_states_pass_none_to_the_fetch(provider, written):
    publish(_inbox(_entry()), {})
    assert provider.calls[0]["states"] is None


def test_inbox_states_skip_the_done_drop(written):
    """An explicitly listed state is wanted even when it equals `dev_done` — the
    filter the fetch applied IS the whole filter."""
    prov = _Provider(
        result=[{**_ticket("PE-1"), "state": "In Review"}],
        done=["In Review"],
    )
    with patch("cockpit.orchestrators.ticket_inbox.provider_for", return_value=prov):
        out = publish(_inbox(_states_entry("widgets", ["In Review"])), {})
    assert [t["id"] for t in out["acme"]] == ["PE-1"]


def test_unset_inbox_states_keep_the_done_drop(written):
    prov = _Provider(
        result=[{**_ticket("PE-1"), "state": "In Review"}],
        done=["In Review"],
    )
    with patch("cockpit.orchestrators.ticket_inbox.provider_for", return_value=prov):
        out = publish(_inbox(_entry()), {})
    assert out == {"acme": []}
