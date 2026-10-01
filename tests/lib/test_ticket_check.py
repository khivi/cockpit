"""Tests for cockpit/lib/ticket_check.py — the per-org ticket diagnostic.

Composition over the two `TicketProvider` seams, so these mock the provider
rather than the transports: whether `verify_team_keys` can read Linear is
`tests/lib/test_linear.py`'s question, and the one here is whether an
unrecognised scope, a failed connection and an unasked tracker stay three
distinguishable answers on the way to the report.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cockpit.lib import ticket_check
from cockpit.lib.ticket_check import (
    RepoCheck,
    bucket_repos,
    check_bucket,
    check_repo,
    format_report,
)


def _cfg(*repos: dict) -> dict:
    return {"repos": list(repos)}


def _repo(name: str, *, org: str | None = None, path: str = "/tmp/r") -> dict:
    entry: dict = {"name": name, "path": path}
    if org:
        entry["org"] = org
    return entry


class _Provider:
    """A `TicketProvider` stand-in: only the five attributes `check_repo` reads."""

    def __init__(
        self,
        *,
        name: str = "linear",
        creds: list[str] | None = None,
        scopes: list[str] | None = None,
        who: str | None = "me",
        known: list[str] | None = None,
    ) -> None:
        self.name = name
        self._creds = creds or []
        self._scopes = scopes or []
        self._who = who
        self._known = known
        self.verify_calls = 0

    def credential_envs(self, cfg, repo):
        return self._creds

    def inbox_scopes(self, cfg, repo):
        return self._scopes

    def whoami(self, cfg, repo, repo_dir):
        return self._who

    def verify_scopes(self, scopes, *, cfg, repo_entry):
        self.verify_calls += 1
        return self._known


@pytest.fixture
def _provider(monkeypatch):
    """Install a stub provider and hand it back, so a case can read its calls."""

    def _install(prov):
        monkeypatch.setattr(ticket_check, "provider_for", lambda cfg, repo: prov)
        monkeypatch.setattr(ticket_check, "ticket_mcp_server", lambda cfg, repo: "")
        return prov

    return _install


@pytest.mark.covers("ticket-check.bucket~1")
def test_bucket_repos_inverts_the_inbox_bucket_rule():
    cfg = _cfg(
        _repo("widgets", org="acme"),
        _repo("gadgets", org="acme"),
        _repo("solo"),
    )
    assert [r["name"] for r in bucket_repos(cfg, "acme")] == ["widgets", "gadgets"]
    assert [r["name"] for r in bucket_repos(cfg, "solo")] == ["solo"]
    assert bucket_repos(cfg, "nobody") == []


@pytest.mark.covers("ticket-check.no-provider~1")
def test_a_repo_tracking_no_tickets_reaches_no_tracker(monkeypatch):
    monkeypatch.setattr(ticket_check, "provider_for", lambda cfg, repo: None)
    got = check_repo(_cfg(), _repo("solo"))
    assert got == RepoCheck(repo="solo")
    assert "contributes none" in format_report("solo", [got])


@pytest.mark.covers("ticket-check.scope-names~1")
def test_an_unrecognised_scope_is_named_not_merely_counted(_provider):
    prov = _provider(_Provider(scopes=["PE", "TYPO"], known=["PE"]))
    got = check_repo(_cfg(), _repo("widgets"))
    assert got.unknown_scopes == ["TYPO"]
    assert prov.verify_calls == 1
    body = format_report("acme", [got])
    assert "TYPO" in body
    assert "FAIL" in body


@pytest.mark.covers("ticket-check.scope-names~1")
def test_every_scope_recognised_reads_ok(_provider):
    _provider(_Provider(scopes=["PE"], known=["PE"]))
    got = check_repo(_cfg(), _repo("widgets"))
    assert got.unknown_scopes == []
    assert "scope names: ok" in format_report("acme", [got])


@pytest.mark.covers("ticket-check.connection-first~1")
def test_a_failed_connection_suppresses_the_scope_verdict(_provider):
    """A scope answer from an unauthenticated credential is indistinguishable
    from "the tracker knows none of these" — blaming the config there sends the
    reader after the wrong thing."""
    prov = _provider(_Provider(scopes=["PE"], who=None, known=["PE"]))
    got = check_repo(_cfg(), _repo("widgets"))
    assert got.connected is False
    assert got.unknown_scopes is None
    assert prov.verify_calls == 0
    body = format_report("acme", [got])
    assert "connection: FAIL" in body
    assert "couldn't be asked" in body


@pytest.mark.covers("ticket-check.couldnt-ask~1")
def test_an_unasked_tracker_never_reads_as_all_scopes_fine(_provider):
    _provider(_Provider(scopes=["PE"], known=None))
    got = check_repo(_cfg(), _repo("widgets"))
    assert got.unknown_scopes is None
    assert "FAIL" not in format_report("acme", [got]).split("scope names")[1]


@pytest.mark.covers("ticket-check.credentials-by-name~1")
def test_a_credential_is_reported_by_name_and_never_by_value(_provider, monkeypatch):
    monkeypatch.setenv("ACME_LINEAR_KEY", "lin_api_supersecret")
    _provider(_Provider(creds=["ACME_LINEAR_KEY", "UNSET_ONE"]))
    got = check_repo(_cfg(), _repo("widgets"))
    assert got.credentials == {"ACME_LINEAR_KEY": True, "UNSET_ONE": False}
    body = format_report("acme", [got])
    assert "ACME_LINEAR_KEY: set" in body
    assert "UNSET_ONE: UNSET" in body
    assert "supersecret" not in body


def _with_mcp(monkeypatch, server: str, listing: dict | None):
    monkeypatch.setattr(ticket_check, "provider_for", lambda cfg, repo: _Provider())
    monkeypatch.setattr(ticket_check, "ticket_mcp_server", lambda cfg, repo: server)
    monkeypatch.setattr(
        ticket_check, "list_mcp_servers", lambda *, repo_dir=None: listing
    )
    return format_report("acme", [check_repo(_cfg(), _repo("widgets"))])


@pytest.mark.covers("ticket-check.mcp-probe~1")
def test_a_reachable_mcp_server_is_reported_connected(monkeypatch):
    body = _with_mcp(monkeypatch, "linear-acme", {"linear-acme": "connected"})
    assert "mcp server: linear-acme" in body
    assert "mcp reachable: connected" in body


@pytest.mark.covers("ticket-check.mcp-probe~1")
def test_a_listed_but_unusable_mcp_server_says_which(monkeypatch):
    body = _with_mcp(
        monkeypatch, "linear-acme", {"linear-acme": "needs authentication"}
    )
    assert "needs authentication" in body


@pytest.mark.covers("ticket-check.mcp-probe-never-gates~1")
def test_an_unlisted_mcp_server_is_a_probable_miss_not_a_verdict(monkeypatch):
    """`claude mcp list` health-checks by connecting and a managed connector
    handshakes asynchronously, so it has called a live server absent."""
    body = _with_mcp(monkeypatch, "linear-acme", {"something-else": "connected"})
    assert "does not name it" in body
    assert "still connecting" in body


@pytest.mark.covers("ticket-check.mcp-probe-never-gates~1")
def test_an_unreadable_probe_is_not_an_absent_server(monkeypatch):
    body = _with_mcp(monkeypatch, "linear-acme", None)
    assert "not checked" in body
    assert "does not name it" not in body


@pytest.mark.covers("ticket-check.mcp-probe~1")
def test_a_repo_declaring_no_mcp_server_never_probes(monkeypatch):
    probes: list = []
    monkeypatch.setattr(ticket_check, "provider_for", lambda cfg, repo: _Provider())
    monkeypatch.setattr(ticket_check, "ticket_mcp_server", lambda cfg, repo: "")

    def _probe(*, repo_dir=None):
        probes.append(repo_dir)
        return {}

    monkeypatch.setattr(ticket_check, "list_mcp_servers", _probe)
    body = format_report("acme", [check_repo(_cfg(), _repo("widgets"))])
    assert probes == []
    assert "mcp server: (none declared)" in body
    assert "mcp reachable" not in body


@pytest.mark.covers("ticket-check.mcp-probe~1")
def test_the_probe_is_shared_across_a_bucket_at_one_cwd(monkeypatch):
    """It connects to every server, so it is the one call here worth deduping —
    and its answer is a property of the directory, not of the repo entry."""
    probes: list = []
    monkeypatch.setattr(ticket_check, "provider_for", lambda cfg, repo: _Provider())
    monkeypatch.setattr(ticket_check, "ticket_mcp_server", lambda cfg, repo: "lin")

    def _probe(*, repo_dir=None):
        probes.append(repo_dir)
        return {"lin": "connected"}

    monkeypatch.setattr(ticket_check, "list_mcp_servers", _probe)
    cfg = _cfg(
        _repo("widgets", org="acme", path="/tmp/same"),
        _repo("gadgets", org="acme", path="/tmp/same"),
        _repo("other", org="acme", path="/tmp/elsewhere"),
    )
    check_bucket(cfg, "acme")
    assert probes == ["/tmp/same", "/tmp/elsewhere"]


@pytest.mark.covers("ticket-check.bucket~1")
def test_check_bucket_walks_the_bucket_in_config_order(_provider):
    _provider(_Provider())
    cfg = _cfg(_repo("widgets", org="acme"), _repo("gadgets", org="acme"))
    assert [c.repo for c in check_bucket(cfg, "acme")] == ["widgets", "gadgets"]


def test_an_unknown_bucket_says_what_a_bucket_is():
    body = format_report("typo", [])
    assert "typo" in body
    assert "org name" in body


@pytest.mark.covers("ticket-check.read-only~1")
def test_the_check_writes_nothing():
    """Asserted structurally: a diagnostic that wrote a cell or a config would
    be a second authority over surfaces only the daemon writes."""
    text = Path(ticket_check.__file__ or "").read_text()
    for banned in ("write_", "save_", "_atomic_write_text", "subprocess"):
        assert banned not in text


def test_an_unnamed_repo_falls_back_to_its_path_basename(monkeypatch):
    monkeypatch.setattr(ticket_check, "provider_for", lambda cfg, repo: None)
    assert check_repo(_cfg(), {"path": "/tmp/dotfiles"}).repo == "dotfiles"


@pytest.mark.covers("ticket-check.bucket~1")
def test_all_buckets_agrees_with_bucket_repos():
    cfg = _cfg(
        _repo("widgets", org="acme"), _repo("gadgets", org="acme"), _repo("solo")
    )
    buckets = ticket_check.all_buckets(cfg)
    assert buckets == ["acme", "solo"]
    assert [r for b in buckets for r in bucket_repos(cfg, b)] == cfg["repos"]
