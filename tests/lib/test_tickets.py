"""Tests for the ticket-provider abstraction (`lib.tickets`)."""

from __future__ import annotations

from unittest.mock import patch

from cockpit.lib import tickets


def test_provider_for_linear():
    p = tickets.provider_for({"tickets": "linear"}, {})
    assert p is not None and p.name == "linear"


def test_provider_for_github_string_shorthand():
    p = tickets.provider_for({"tickets": "github"}, {})
    assert p is not None and p.name == "github"


def test_provider_for_github_object():
    p = tickets.provider_for({"tickets": {"provider": "github"}}, {})
    assert p is not None and p.name == "github"


def test_provider_for_none():
    assert tickets.provider_for({}, {}) is None
    assert tickets.provider_for({"tickets": "none"}, {}) is None


def test_provider_for_linear_keys_back_compat():
    # No `tickets` anywhere, but repo has linear_keys → linear provider.
    p = tickets.provider_for({}, {"linear_keys": ["PE"]})
    assert p is not None and p.name == "linear"


def test_linear_parse_footers_ignores_nwo():
    p = tickets.LINEAR
    assert p.parse_footers("Linear: [PE-1](u)", "o/r") == ["PE-1"]


def test_github_parse_footers_uses_nwo():
    p = tickets.GITHUB
    assert p.parse_footers("Closes #5", "o/r") == ["#5"]
    assert p.parse_footers("Closes other/x#5", "o/r") == ["other/x#5"]


def test_linear_dev_done_value_default():
    assert tickets.LINEAR.dev_done_value({}, None) == "Dev Done"


def test_github_dev_done_value_default():
    assert tickets.GITHUB.dev_done_value({}, None) == "ready for review"


def test_github_fetch_states_maps_label_to_dev_done():
    # An issue carrying the dev-done label (default "ready for review") maps to
    # that value so `_track_dev_done` lights the pill; others keep open/closed.
    issues = {
        "#1": {"labels": ["ready for review", "bug"], "state": "open"},
        "#2": {"labels": ["bug"], "state": "open"},
        "#3": None,
    }
    with patch.object(tickets, "fetch_issues", return_value=issues):
        out = tickets.GITHUB.fetch_states(
            ["#1", "#2", "#3"], repo_nwo="o/r", repo_dir="/tmp", cfg={}
        )
    assert out == {"#1": "ready for review", "#2": "open", "#3": None}


def test_github_fetch_states_custom_label_from_object():
    issues = {"#1": {"labels": ["qa ok"], "state": "open"}}
    cfg = {}
    repo = {"tickets": {"provider": "github", "dev_done_label": "qa ok"}}
    with patch.object(tickets, "fetch_issues", return_value=issues):
        out = tickets.GITHUB.fetch_states(
            ["#1"], repo_nwo="o/r", repo_dir="/", cfg=cfg, repo_entry=repo
        )
    assert out == {"#1": "qa ok"}


def test_linear_fetch_states_delegates():
    with patch.object(
        tickets, "fetch_ticket_states", return_value={"PE-1": "Dev Done"}
    ) as f:
        out = tickets.LINEAR.fetch_states(
            ["PE-1"], repo_nwo="o/r", repo_dir="/", cfg={}, repo_entry=None
        )
    assert out == {"PE-1": "Dev Done"}
    f.assert_called_once_with(["PE-1"])
