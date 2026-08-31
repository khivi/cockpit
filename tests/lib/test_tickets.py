"""Tests for the ticket-provider abstraction (`lib.tickets`)."""

from __future__ import annotations

from unittest.mock import ANY, patch

import pytest

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


def test_provider_for_needs_an_explicit_provider():
    # `tickets.keys` alone doesn't name a provider — Jira declares the same
    # field, so the block has to say which one it is.
    assert tickets.provider_for({}, {"tickets": {"keys": ["PE"]}}) is None


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
    repo = {"tickets": {"provider": "github", "dev_done": "qa ok"}}
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
    f.assert_called_once_with(["PE-1"], api_key=ANY)


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


@pytest.mark.parametrize(
    "provider,ref,body,expected",
    [
        (
            "LINEAR",
            "pe-9",  # case-insensitive: the footer id is canonicalised upper
            "Linear: [PE-9](https://linear.app/x/issue/PE-9)",
            "https://linear.app/x/issue/PE-9",
        ),
        (
            "JIRA",
            "proj-3",
            "Jira: [PROJ-3](https://x.atlassian.net/browse/PROJ-3)",
            "https://x.atlassian.net/browse/PROJ-3",
        ),
        (
            "TRELLO",
            "aB3xY",  # short links are case-SENSITIVE, matched verbatim
            "Trello: [Fix login](https://trello.com/c/aB3xY)",
            "https://trello.com/c/aB3xY",
        ),
    ],
)
def test_ticket_url_takes_a_body_instead_of_fetching_one(provider, ref, body, expected):
    """A caller holding the PR body skips the `gh pr body` entirely.

    This is what lets the daemon resolve these links for the PR cache — one
    parse per PR instead of one subprocess per delivered ticket — and it is the
    only reason the TUI can render the Ticket cell as a hyperlink at all
    (a renderer may not shell out)."""
    with patch.object(tickets, "pr_body") as pb:
        assert getattr(tickets, provider).ticket_url(ref, body=body) == expected
    pb.assert_not_called()


def test_ticket_url_with_a_body_carrying_no_footer_is_none():
    """An empty body is an answer, not a reason to go fetch a better one."""
    with patch.object(tickets, "pr_body") as pb:
        assert tickets.LINEAR.ticket_url("PE-9", body="") is None
        assert tickets.LINEAR.ticket_url("PE-9", body="no footer here") is None
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
    repo = {"tickets": {"provider": "jira", "dev_done": "In Review"}}
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
        ["PROJ-1"], site_url="https://x.atlassian.net", email="me@x.com", token=ANY
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


# ── credential env-*name* fields (shared `token_env` across two providers) ───


def test_token_env_is_valid_under_both_jira_and_trello():
    # The one field name two providers declare. The allowed set is composed per
    # *active* provider, so a shared name must validate under either.
    assert tickets.tickets_field_errors({"token_env": "JIRA_ACME"}, "jira") == []
    assert tickets.tickets_field_errors({"token_env": "TRELLO_ACME"}, "trello") == []


def test_token_env_is_still_rejected_for_providers_that_dont_declare_it():
    # Linear declares `token_env` since the credential rename; GitHub has no
    # credential field at all, so it and `none` still reject one.
    for provider in ("github", "none"):
        errs = tickets.tickets_field_errors({"token_env": "X"}, provider)
        assert len(errs) == 1 and "token_env" in errs[0]


def test_every_credential_provider_accepts_token_env():
    for provider in ("linear", "jira", "trello"):
        assert tickets.tickets_field_errors({"token_env": "X"}, provider) == []


def test_canonical_workflow_fields_are_accepted_by_every_provider():
    # `dev_done` / `merge_done` replaced four provider-specific spellings, so
    # each must validate wherever its predecessor did.
    for provider in ("linear", "jira", "trello"):
        assert tickets.tickets_field_errors({"dev_done": "X"}, provider) == []
        assert tickets.tickets_field_errors({"merge_done": "X"}, provider) == []
    # GitHub closes its issue on merge rather than moving it, so it has a
    # dev-done value but no merge-done one.
    assert tickets.tickets_field_errors({"dev_done": "X"}, "github") == []
    assert tickets.tickets_field_errors({"merge_done": "X"}, "github") != []


def test_superseded_workflow_spellings_are_no_longer_in_the_schema():
    # They are rejected here as unknown-for-the-provider; `preflight._check_legacy`
    # is what turns that into a message naming the replacement, since this
    # function can't tell a rename from a typo.
    for field, provider in [
        ("dev_done_state", "linear"),
        ("merge_done_state", "linear"),
        ("dev_done_label", "github"),
        ("dev_done_status", "jira"),
        ("merge_done_status", "jira"),
        ("dev_done_list", "trello"),
        ("merge_done_list", "trello"),
        ("api_key_env", "linear"),
    ]:
        assert tickets.tickets_field_errors({field: "X"}, provider) != [], field


def test_credential_env_fields_are_per_provider():
    assert tickets.tickets_field_errors({"token_env": "LIN"}, "linear") == []
    assert tickets.tickets_field_errors({"key_env": "K"}, "trello") == []
    assert tickets.tickets_field_errors({"key_env": "K"}, "jira") != []


def test_credential_env_fields_must_be_strings():
    errs = tickets.tickets_field_errors({"token_env": 7}, "linear")
    assert len(errs) == 1 and "must be a string" in errs[0]


def test_linear_fetch_states_threads_the_resolved_key(monkeypatch):
    monkeypatch.setenv("LIN_ACME", "lin_secret")
    repo = {"tickets": {"provider": "linear", "token_env": "LIN_ACME"}}
    with patch.object(tickets, "fetch_ticket_states", return_value={}) as f:
        tickets.LINEAR.fetch_states(
            ["PE-1"], repo_nwo="o/r", repo_dir="/", cfg={}, repo_entry=repo
        )
    f.assert_called_once_with(["PE-1"], api_key="lin_secret")


def test_linear_fetch_titles_threads_the_resolved_key(monkeypatch):
    monkeypatch.setenv("LIN_ACME", "lin_secret")
    repo = {"tickets": {"provider": "linear", "token_env": "LIN_ACME"}}
    with patch.object(tickets, "fetch_ticket_titles", return_value={}) as f:
        tickets.LINEAR.fetch_titles(
            ["PE-1"], repo_nwo="o/r", repo_dir="/", cfg={}, repo_entry=repo
        )
    f.assert_called_once_with(["PE-1"], api_key="lin_secret")


def test_linear_fetch_states_passes_none_when_the_named_var_is_unset(monkeypatch):
    monkeypatch.delenv("LIN_MISSING", raising=False)
    repo = {"tickets": {"provider": "linear", "token_env": "LIN_MISSING"}}
    with patch.object(tickets, "fetch_ticket_states", return_value={}) as f:
        tickets.LINEAR.fetch_states(
            ["PE-1"], repo_nwo="o/r", repo_dir="/", cfg={}, repo_entry=repo
        )
    f.assert_called_once_with(["PE-1"], api_key=None)


def test_jira_fetch_states_threads_the_resolved_token(monkeypatch):
    monkeypatch.setenv("JIRA_ACME", "jira_secret")
    cfg = {"tickets": {"provider": "jira", "site_url": "https://x.atlassian.net"}}
    repo = {"tickets": {"email": "me@x.com", "token_env": "JIRA_ACME"}}
    with patch.object(tickets, "fetch_issue_statuses", return_value={}) as f:
        tickets.JIRA.fetch_states(
            ["PROJ-1"], repo_nwo="o/r", repo_dir="/", cfg=cfg, repo_entry=repo
        )
    assert f.call_args.kwargs["token"] == "jira_secret"


def test_trello_fetch_states_threads_the_resolved_key_and_token(monkeypatch):
    monkeypatch.setenv("TRELLO_K", "k1")
    monkeypatch.setenv("TRELLO_T", "t1")
    repo = {
        "tickets": {
            "provider": "trello",
            "key_env": "TRELLO_K",
            "token_env": "TRELLO_T",
        }
    }
    with patch.object(tickets, "fetch_card_lists", return_value={}) as f:
        tickets.TRELLO.fetch_states(
            ["aB3"], repo_nwo="o/r", repo_dir="/", cfg={}, repo_entry=repo
        )
    f.assert_called_once_with(["aB3"], key="k1", token="t1")


# ── narrow_repos — the ticket→repo routing tiebreaker ───────────────────────
#
# Only Linear implements it (the ticket's project); every other provider's
# identifier already resolves as far as it can, so theirs is a passthrough.

_CFG = {"tickets": {"provider": "linear"}}


def _repo(name: str, project: str | None = None) -> dict:
    entry: dict = {"name": name, "tickets": {"keys": ["PE"]}}
    if project is not None:
        entry["tickets"]["project"] = project
    return entry


@pytest.fixture
def linear_key(monkeypatch):
    """Make the default credential resolvable. A candidate group whose key is
    unset is skipped outright (it cannot be asked), so every test that expects a
    fetch against the default `LINEAR_API_KEY` needs this."""
    monkeypatch.setenv("LINEAR_API_KEY", "k")


def test_narrow_repos_picks_the_repo_owning_the_project(linear_key):
    cands = [_repo("payments", "Payments API"), _repo("web", "Web Checkout")]
    with patch(
        "cockpit.lib.tickets.fetch_ticket_project", return_value="Web Checkout"
    ) as fetch:
        got = tickets.LINEAR.narrow_repos("PE-1234", cands, _CFG)
    assert [r["name"] for r in got] == ["web"]
    fetch.assert_called_once_with("PE-1234", api_key=ANY)


def test_narrow_repos_matches_project_case_insensitively(linear_key):
    cands = [_repo("payments", "Payments API"), _repo("web", "Web Checkout")]
    with patch("cockpit.lib.tickets.fetch_ticket_project", return_value="payments api"):
        got = tickets.LINEAR.narrow_repos("PE-1234", cands, _CFG)
    assert [r["name"] for r in got] == ["payments"]


def test_narrow_repos_no_project_configured_skips_the_fetch():
    # Nothing to narrow *by* — must stay offline and leave the set alone.
    cands = [_repo("payments"), _repo("web")]
    with patch("cockpit.lib.tickets.fetch_ticket_project") as fetch:
        got = tickets.LINEAR.narrow_repos("PE-1234", cands, _CFG)
    assert got == cands
    fetch.assert_not_called()


def test_narrow_repos_failed_fetch_leaves_candidates_unchanged(linear_key):
    cands = [_repo("payments", "Payments API"), _repo("web", "Web Checkout")]
    with patch("cockpit.lib.tickets.fetch_ticket_project", return_value=None):
        assert tickets.LINEAR.narrow_repos("PE-1234", cands, _CFG) == cands


def test_narrow_repos_unknown_project_never_narrows_to_zero(linear_key):
    # The ticket's project isn't any repo's — degrade to the caller's existing
    # ambiguity path rather than routing the spawn nowhere.
    cands = [_repo("payments", "Payments API"), _repo("web", "Web Checkout")]
    with patch("cockpit.lib.tickets.fetch_ticket_project", return_value="Mobile"):
        assert tickets.LINEAR.narrow_repos("PE-1234", cands, _CFG) == cands


def test_narrow_repos_keeps_every_repo_sharing_one_project(linear_key):
    cands = [_repo("api", "Payments API"), _repo("web", "Payments API")]
    with patch("cockpit.lib.tickets.fetch_ticket_project", return_value="Payments API"):
        got = tickets.LINEAR.narrow_repos("PE-1234", cands, _CFG)
    assert [r["name"] for r in got] == ["api", "web"]


def _org_repo(name: str, project: str, env: str) -> dict:
    return {"name": name, "tickets": {"project": project, "token_env": env}}


def test_narrow_repos_one_fetch_when_candidates_share_a_credential(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY_ACME", "acme-key")
    cands = [
        _org_repo("a", "P1", "LINEAR_API_KEY_ACME"),
        _org_repo("b", "P2", "LINEAR_API_KEY_ACME"),
    ]
    with patch("cockpit.lib.tickets.fetch_ticket_project", return_value="P2") as fetch:
        got = tickets.LINEAR.narrow_repos("PE-1", cands, _CFG)
    assert [r["name"] for r in got] == ["b"]
    fetch.assert_called_once_with("PE-1", api_key="acme-key")


def test_narrow_repos_asks_each_org_with_its_own_key(monkeypatch):
    # A team key is workspace-scoped: two orgs can each own a `PE` team, so both
    # repos match `PE-1`. Querying only the first org's workspace would answer
    # about a *different* issue that merely shares the identifier.
    monkeypatch.setenv("LINEAR_API_KEY_ACME", "acme-key")
    monkeypatch.setenv("LINEAR_API_KEY_BETA", "beta-key")
    cands = [
        _org_repo("acme-svc", "Acme Billing", "LINEAR_API_KEY_ACME"),
        _org_repo("beta-svc", "Beta Search", "LINEAR_API_KEY_BETA"),
    ]

    def fake_fetch(ref, *, api_key):
        # Only Beta's workspace holds this ticket.
        return "Beta Search" if api_key == "beta-key" else None

    with patch("cockpit.lib.tickets.fetch_ticket_project", side_effect=fake_fetch):
        got = tickets.LINEAR.narrow_repos("PE-1", cands, _CFG)
    assert [r["name"] for r in got] == ["beta-svc"]


def test_narrow_repos_never_matches_a_project_across_workspaces(monkeypatch):
    # Both orgs have a `PE-1`, and Acme's happens to sit in a project named the
    # same as Beta's repo claims. The match must stay inside the group whose key
    # answered — else the spawn routes to the wrong org's repo.
    monkeypatch.setenv("LINEAR_API_KEY_ACME", "acme-key")
    monkeypatch.setenv("LINEAR_API_KEY_BETA", "beta-key")
    cands = [
        _org_repo("acme-svc", "Acme Billing", "LINEAR_API_KEY_ACME"),
        _org_repo("beta-svc", "Shared Name", "LINEAR_API_KEY_BETA"),
    ]

    def fake_fetch(ref, *, api_key):
        return "Shared Name" if api_key == "acme-key" else "Beta Search"

    with patch("cockpit.lib.tickets.fetch_ticket_project", side_effect=fake_fetch):
        got = tickets.LINEAR.narrow_repos("PE-1", cands, _CFG)
    # Acme answered "Shared Name" but no *Acme* repo claims it; Beta answered
    # "Beta Search" which no Beta repo claims → inconclusive, unchanged.
    assert got == cands


def test_narrow_repos_skips_a_group_whose_credential_is_unset(monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY_ACME", raising=False)
    monkeypatch.setenv("LINEAR_API_KEY_BETA", "beta-key")
    cands = [
        _org_repo("acme-svc", "Acme Billing", "LINEAR_API_KEY_ACME"),
        _org_repo("beta-svc", "Beta Search", "LINEAR_API_KEY_BETA"),
    ]
    with patch(
        "cockpit.lib.tickets.fetch_ticket_project", return_value="Beta Search"
    ) as fetch:
        got = tickets.LINEAR.narrow_repos("PE-1", cands, _CFG)
    assert [r["name"] for r in got] == ["beta-svc"]
    fetch.assert_called_once_with("PE-1", api_key="beta-key")


def test_narrow_repos_is_a_passthrough_for_github_and_jira():
    # GitHub's ref carries `owner/repo`; Jira's "project" *is* the key prefix the
    # free match already used. Neither has a container left to discriminate on.
    cands = [{"name": "a"}, {"name": "b"}]
    for provider in (tickets.GITHUB, tickets.JIRA):
        assert provider.narrow_repos("ref", cands, _CFG) == cands


# ── narrow_repos, Trello — the *whole* route, not a tiebreak ────────────────
#
# A card short link carries no board, so there is no free first-stage match;
# declaring `tickets.board` is the opt-in that makes the fetch worth paying for.

_TRELLO_CFG = {"tickets": {"provider": "trello"}}
_CARD_URL = "https://trello.com/c/aB3dZ9/7-fix-oauth"


def _trello_repo(name: str, board: str | None = None, **envs: str) -> dict:
    entry: dict = {"name": name, "tickets": dict(envs)}
    if board is not None:
        entry["tickets"]["board"] = board
    return entry


@pytest.fixture
def trello_creds(monkeypatch):
    """The default key+token pair resolvable. A group missing either is skipped
    outright (it cannot be asked), so any test expecting a fetch needs this."""
    monkeypatch.setenv("TRELLO_API_KEY", "k")
    monkeypatch.setenv("TRELLO_API_TOKEN", "t")


def test_trello_narrow_picks_the_repo_owning_the_board(trello_creds):
    cands = [_trello_repo("api", "Platform"), _trello_repo("web", "Engineering")]
    with patch(
        "cockpit.lib.tickets.fetch_card_board", return_value="Engineering"
    ) as fetch:
        got = tickets.TRELLO.narrow_repos(_CARD_URL, cands, _TRELLO_CFG)
    assert [r["name"] for r in got] == ["web"]
    # The URL is reduced to the card's short link before the fetch.
    fetch.assert_called_once_with("aB3dZ9", key="k", token="t")


def test_trello_narrow_accepts_a_bare_short_link(trello_creds):
    cands = [_trello_repo("api", "Platform"), _trello_repo("web", "Engineering")]
    with patch(
        "cockpit.lib.tickets.fetch_card_board", return_value="Platform"
    ) as fetch:
        got = tickets.TRELLO.narrow_repos("aB3dZ9", cands, _TRELLO_CFG)
    assert [r["name"] for r in got] == ["api"]
    fetch.assert_called_once_with("aB3dZ9", key="k", token="t")


def test_trello_narrow_matches_board_case_insensitively(trello_creds):
    cands = [_trello_repo("api", "Platform"), _trello_repo("web", "Engineering")]
    with patch("cockpit.lib.tickets.fetch_card_board", return_value="engineering"):
        got = tickets.TRELLO.narrow_repos(_CARD_URL, cands, _TRELLO_CFG)
    assert [r["name"] for r in got] == ["web"]


def test_trello_narrow_no_board_configured_skips_the_fetch():
    # The opt-in gate: nobody declares a board → zero network calls, no narrowing.
    cands = [_trello_repo("api"), _trello_repo("web")]
    with patch("cockpit.lib.tickets.fetch_card_board") as fetch:
        got = tickets.TRELLO.narrow_repos(_CARD_URL, cands, _TRELLO_CFG)
    assert got == cands
    fetch.assert_not_called()


def test_trello_narrow_failed_fetch_leaves_candidates_unchanged(trello_creds):
    cands = [_trello_repo("api", "Platform"), _trello_repo("web", "Engineering")]
    with patch("cockpit.lib.tickets.fetch_card_board", return_value=None):
        assert tickets.TRELLO.narrow_repos(_CARD_URL, cands, _TRELLO_CFG) == cands


def test_trello_narrow_unknown_board_never_narrows_to_zero(trello_creds):
    cands = [_trello_repo("api", "Platform"), _trello_repo("web", "Engineering")]
    with patch("cockpit.lib.tickets.fetch_card_board", return_value="Marketing"):
        assert tickets.TRELLO.narrow_repos(_CARD_URL, cands, _TRELLO_CFG) == cands


def test_trello_narrow_keeps_every_repo_sharing_one_board(trello_creds):
    cands = [_trello_repo("api", "Engineering"), _trello_repo("web", "Engineering")]
    with patch("cockpit.lib.tickets.fetch_card_board", return_value="Engineering"):
        got = tickets.TRELLO.narrow_repos(_CARD_URL, cands, _TRELLO_CFG)
    assert [r["name"] for r in got] == ["api", "web"]


def test_trello_narrow_asks_each_account_with_its_own_credential_pair(monkeypatch):
    # A board name is account-scoped exactly as a Linear team key is
    # workspace-scoped: two orgs can each own an "Engineering" board, and asking
    # one account about the other's card answers about a different card / 404s.
    monkeypatch.setenv("TK_ACME", "acme-k")
    monkeypatch.setenv("TT_ACME", "acme-t")
    monkeypatch.setenv("TK_BETA", "beta-k")
    monkeypatch.setenv("TT_BETA", "beta-t")
    cands = [
        _trello_repo("acme-svc", "Acme Eng", key_env="TK_ACME", token_env="TT_ACME"),
        _trello_repo("beta-svc", "Beta Eng", key_env="TK_BETA", token_env="TT_BETA"),
    ]

    def fake_fetch(short, *, key, token):
        return "Beta Eng" if (key, token) == ("beta-k", "beta-t") else None

    with patch("cockpit.lib.tickets.fetch_card_board", side_effect=fake_fetch):
        got = tickets.TRELLO.narrow_repos(_CARD_URL, cands, _TRELLO_CFG)
    assert [r["name"] for r in got] == ["beta-svc"]


def test_trello_narrow_never_matches_a_board_across_accounts(monkeypatch):
    # Acme's account answers with a name Beta's repo claims. The match must stay
    # inside the group whose credentials answered, else the spawn routes to the
    # wrong org's repo.
    monkeypatch.setenv("TK_ACME", "acme-k")
    monkeypatch.setenv("TT_ACME", "acme-t")
    monkeypatch.setenv("TK_BETA", "beta-k")
    monkeypatch.setenv("TT_BETA", "beta-t")
    cands = [
        _trello_repo("acme-svc", "Acme Eng", key_env="TK_ACME", token_env="TT_ACME"),
        _trello_repo("beta-svc", "Shared Name", key_env="TK_BETA", token_env="TT_BETA"),
    ]

    def fake_fetch(short, *, key, token):
        return "Shared Name" if key == "acme-k" else "Beta Eng"

    with patch("cockpit.lib.tickets.fetch_card_board", side_effect=fake_fetch):
        assert tickets.TRELLO.narrow_repos(_CARD_URL, cands, _TRELLO_CFG) == cands


def test_trello_narrow_one_fetch_when_candidates_share_a_credential(monkeypatch):
    monkeypatch.setenv("TK_ACME", "acme-k")
    monkeypatch.setenv("TT_ACME", "acme-t")
    cands = [
        _trello_repo("a", "B1", key_env="TK_ACME", token_env="TT_ACME"),
        _trello_repo("b", "B2", key_env="TK_ACME", token_env="TT_ACME"),
    ]
    with patch("cockpit.lib.tickets.fetch_card_board", return_value="B2") as fetch:
        got = tickets.TRELLO.narrow_repos(_CARD_URL, cands, _TRELLO_CFG)
    assert [r["name"] for r in got] == ["b"]
    fetch.assert_called_once_with("aB3dZ9", key="acme-k", token="acme-t")


def test_trello_narrow_skips_a_group_missing_half_its_credential_pair(monkeypatch):
    # Trello needs key *and* token; a group with only one can't be asked.
    monkeypatch.setenv("TK_ACME", "acme-k")
    monkeypatch.delenv("TT_ACME", raising=False)
    monkeypatch.setenv("TK_BETA", "beta-k")
    monkeypatch.setenv("TT_BETA", "beta-t")
    cands = [
        _trello_repo("acme-svc", "Acme Eng", key_env="TK_ACME", token_env="TT_ACME"),
        _trello_repo("beta-svc", "Beta Eng", key_env="TK_BETA", token_env="TT_BETA"),
    ]
    with patch(
        "cockpit.lib.tickets.fetch_card_board", return_value="Beta Eng"
    ) as fetch:
        got = tickets.TRELLO.narrow_repos(_CARD_URL, cands, _TRELLO_CFG)
    assert [r["name"] for r in got] == ["beta-svc"]
    fetch.assert_called_once_with("aB3dZ9", key="beta-k", token="beta-t")


def test_every_provider_declares_credential_envs_the_stripper_knows_about():
    """`credential_envs` (what preflight warns about) and
    `config.credential_env_names` (what `_bg_spawn_pr` strips from a spawned
    session's environment) must name the same variables. A provider gaining a
    credential in one and not the other either warns about a name nothing holds,
    or — the dangerous direction — leaks a key into an agent session running over
    an untrusted PR diff."""
    from cockpit.lib.config import credential_env_names

    known = credential_env_names({})
    for name, provider in tickets._PROVIDERS.items():
        for env in provider.credential_envs({}, None):
            assert env in known, f"{name} credential {env} is not stripped on spawn"


def test_github_declares_no_credential_env():
    # `gh` owns that auth; a warning here would name a variable nobody sets.
    assert tickets.GITHUB.credential_envs({}, None) == []


def test_trello_declares_both_halves_of_its_credential_pair():
    assert tickets.TRELLO.credential_envs({}, None) == [
        "TRELLO_API_KEY",
        "TRELLO_API_TOKEN",
    ]
