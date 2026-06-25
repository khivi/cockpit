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
    cfg: dict = {}
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


def test_github_ticket_url_is_deterministic():
    # Same-repo ref resolves its nwo from repo_nwo; cross-repo ref carries its
    # own. No PR-body fetch.
    p = tickets.GITHUB
    assert (
        p.ticket_url("#42", repo_nwo="o/r", repo_dir="/x", pr_number=7)
        == "https://github.com/o/r/issues/42"
    )
    assert (
        p.ticket_url("other/x#9", repo_nwo="o/r")
        == "https://github.com/other/x/issues/9"
    )


def test_github_ticket_url_none_without_nwo():
    # A bare `#N` with no repo nwo can't be resolved to a URL.
    assert tickets.GITHUB.ticket_url("#5", repo_nwo=None) is None


def test_linear_ticket_url_reads_footer_link():
    body = "Linear: [PE-9](https://linear.app/x/issue/PE-9)"
    with patch.object(tickets, "pr_body", return_value=body) as pb:
        url = tickets.LINEAR.ticket_url(
            "PE-9", repo_nwo="o/r", repo_dir="/wt", pr_number=7
        )
    assert url == "https://linear.app/x/issue/PE-9"
    pb.assert_called_once()


def test_linear_ticket_url_none_without_pr_context():
    # No repo_dir / pr_number → can't fetch the body → no URL (no network call).
    with patch.object(tickets, "pr_body") as pb:
        assert tickets.LINEAR.ticket_url("PE-9", repo_dir=None, pr_number=None) is None
    pb.assert_not_called()


# ── jira provider ───────────────────────────────────────────────────────────


def test_provider_for_jira():
    p = tickets.provider_for({"tickets": "jira"}, {})
    assert p is not None and p.name == "jira"


def test_provider_for_jira_object():
    p = tickets.provider_for({"tickets": {"provider": "jira"}}, {})
    assert p is not None and p.name == "jira"


def test_jira_parse_footers_ignores_nwo():
    p = tickets.JIRA
    assert p.parse_footers("Jira: [PROJ-1](u)", "o/r") == ["PROJ-1"]


def test_jira_dev_done_value_default():
    assert tickets.JIRA.dev_done_value({}, None) == "Dev Done"


def test_jira_dev_done_value_custom():
    repo = {"tickets": {"provider": "jira", "dev_done_status": "In Review"}}
    assert tickets.JIRA.dev_done_value({}, repo) == "In Review"


def test_jira_fetch_states_delegates_with_site_and_email():
    cfg = {"tickets": {"provider": "jira", "site_url": "https://x.atlassian.net"}}
    repo = {"tickets": {"email": "me@x.com"}}
    with patch.object(
        tickets, "fetch_issue_statuses", return_value={"PROJ-1": "Done"}
    ) as f:
        out = tickets.JIRA.fetch_states(
            ["PROJ-1"], repo_nwo="o/r", repo_dir="/", cfg=cfg, repo_entry=repo
        )
    assert out == {"PROJ-1": "Done"}
    f.assert_called_once_with(
        ["PROJ-1"], site_url="https://x.atlassian.net", email="me@x.com"
    )


def test_jira_fetch_states_all_none_when_unconfigured():
    # No site/email → feature off → all None, no REST call.
    with patch.object(tickets, "fetch_issue_statuses") as f:
        out = tickets.JIRA.fetch_states(
            ["PROJ-1"], repo_nwo="o/r", repo_dir="/", cfg={}, repo_entry=None
        )
    assert out == {"PROJ-1": None}
    f.assert_not_called()


def test_jira_ticket_url_reads_footer_link():
    body = "Jira: [PROJ-9](https://acme.atlassian.net/browse/PROJ-9)"
    with patch.object(tickets, "pr_body", return_value=body) as pb:
        url = tickets.JIRA.ticket_url(
            "proj-9", repo_nwo="o/r", repo_dir="/wt", pr_number=7
        )
    assert url == "https://acme.atlassian.net/browse/PROJ-9"
    pb.assert_called_once()


def test_jira_ticket_url_none_without_pr_context():
    with patch.object(tickets, "pr_body") as pb:
        assert tickets.JIRA.ticket_url("PROJ-9", repo_dir=None, pr_number=None) is None
    pb.assert_not_called()


def test_jira_config_fields_rejected_for_other_provider():
    # A jira-only field under github must be flagged (and vice versa).
    errs = tickets.tickets_field_errors(
        {"provider": "github", "site_url": "x"}, "github"
    )
    assert errs and "site_url" in errs[0]
