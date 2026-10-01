"""Diagnose one ticket-inbox bucket end to end — the answer to "why is this org
empty?".

A bucket that renders no tickets is the one failure the inbox cannot explain
itself. `fetch_my_open` already separates "couldn't ask" from "answered with
nothing" (`TicketInbox.partial`), but every *configuration* fault collapses into
the second: an unset credential, a typo'd `tickets.keys`, a board the account
can't see and a genuinely empty queue all produce the same blank fold. So this
asks the questions the fetch can't, per repo in the bucket:

- the resolved **provider** (`tickets: none` means the repo contributes nothing)
- whether each **credential** env var is set — by name, never by value
- the **connection**: `TicketProvider.whoami`, each provider's own only-mine
  identity fetch, which is the cheapest call that proves the credential works
- the declared **scope** and whether the tracker knows it
  (`TicketProvider.verify_scopes`) — a Linear team, a Jira project, a Trello
  board name
- the declared **MCP server**, and whether Claude Code can reach it
  (`lib/mcp.py`) — the one probe cockpit makes, and it gates nothing

The two tracker questions come off the `TicketProvider`, so nothing here
branches on a provider name — the class rule. Read-only: no cache cell, no
config write, no git. One identity call plus at most one scope call per repo;
repos in an org usually share a credential, which this deliberately does not
dedup — each repo resolves its own provider, credential and scope through the
org merge, and a check that asked once per credential could not report a repo
whose own block overrides it. The MCP probe *is* deduped per cwd, since it is
the expensive one (it connects to every server) and its answer is a property of
the directory rather than of the repo entry.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import ticket_mcp_server
from .mcp import list_mcp_servers
from .tickets import provider_for


@dataclass(frozen=True)
class RepoCheck:
    """One repo's verdict. Every field is either config cockpit resolved or an
    answer a tracker gave; nothing here is inferred from a blank inbox."""

    repo: str
    #: "" for `tickets: none` — the repo is in the bucket for its PRs, not its
    #: tickets, and every field below is then vacuous.
    provider: str = ""
    #: env var name → currently set. Names only; a value never enters this dict.
    credentials: dict[str, bool] = field(default_factory=dict)
    scopes: list[str] = field(default_factory=list)
    mcp_server: str = ""
    #: True once `whoami` answered. None means the call was never made (no
    #: provider), which must not read as a failed connection.
    connected: bool | None = None
    #: Declared scopes the tracker did not recognise. None = couldn't ask, which
    #: is not the same as "all of them are fine".
    unknown_scopes: list[str] | None = None
    #: What `claude mcp list` says about `mcp_server`: its health word, `""` when
    #: the listing did not name it, or None when the probe could not be run. The
    #: last two must stay apart — a managed connector has been reported absent
    #: while live, so "not listed" carries a caveat that "not checked" does not.
    mcp_health: str | None = None


def bucket_repos(cfg: dict, bucket: str) -> list[dict]:
    """The config entries feeding inbox bucket `bucket`.

    The inbox keys a bucket by a repo's `org`, falling back to its `name` — so
    this inverts `cycle._review_bucket_key`'s rule. Reading `org` as a bucket
    label is not an org-aware reader: the resolution below `load_config` is
    untouched, and the label is just a string to match.
    """
    return [
        repo
        for repo in cfg.get("repos", []) or []
        if str(repo.get("org") or repo.get("name") or "") == bucket
    ]


def all_buckets(cfg: dict) -> list[str]:
    """Every bucket the config can produce, sorted — what `c` on an empty inbox
    checks. Derived from the same rule `bucket_repos` inverts, so the two cannot
    disagree about what a bucket is."""
    return sorted(
        {
            str(repo.get("org") or repo.get("name") or "")
            for repo in cfg.get("repos", []) or []
        }
        - {""}
    )


def _repo_label(repo: dict) -> str:
    """The repo's one identity — the `name`-or-basename every other surface
    names it by."""
    return str(repo.get("name") or Path(os.path.expanduser(repo["path"])).name)


def check_repo(
    cfg: dict, repo: dict, *, mcp_cache: dict[str, dict[str, str] | None] | None = None
) -> RepoCheck:
    """Diagnose one repo's ticket setup. Never raises.

    `mcp_cache` memoizes the MCP probe per cwd across a bucket — pass the same
    dict to every call in one run. Omitting it re-probes per repo, which is
    correct but pays the connect-to-every-server cost again.
    """
    label = _repo_label(repo)
    provider = provider_for(cfg, repo)
    if provider is None:
        return RepoCheck(repo=label)
    creds = {
        name: bool(os.environ.get(name)) for name in provider.credential_envs(cfg, repo)
    }
    scopes = list(provider.inbox_scopes(cfg, repo))
    repo_dir = str(Path(os.path.expanduser(repo["path"])))
    # `whoami` first: a scope answer from an unauthenticated credential is
    # indistinguishable from "the tracker knows none of these", and the report
    # should blame the credential rather than the config.
    connected = provider.whoami(cfg, repo, repo_dir) is not None
    known = (
        provider.verify_scopes(scopes, cfg=cfg, repo_entry=repo) if connected else None
    )
    unknown = None if known is None else [s for s in scopes if s not in known]
    server = ticket_mcp_server(cfg, repo) or ""
    return RepoCheck(
        repo=label,
        provider=provider.name,
        credentials=creds,
        scopes=scopes,
        mcp_server=server,
        connected=connected,
        unknown_scopes=unknown,
        mcp_health=_mcp_health(server, repo_dir, mcp_cache),
    )


def _mcp_health(
    server: str,
    repo_dir: str,
    cache: dict[str, dict[str, str] | None] | None,
) -> str | None:
    """`server`'s health word, `""` when the listing didn't name it, or None.

    A repo declaring no server probes nothing: there is no name to look up, and
    the probe is the expensive call here.
    """
    if not server:
        return None
    if cache is None:
        cache = {}
    if repo_dir not in cache:
        cache[repo_dir] = list_mcp_servers(repo_dir=repo_dir)
    listing = cache[repo_dir]
    if listing is None:
        return None
    return listing.get(server, "")


def check_bucket(
    cfg: dict, bucket: str, *, on_repo: Callable[[str], None] | None = None
) -> list[RepoCheck]:
    """Every repo in `bucket`, in config order, sharing one MCP probe per cwd.

    `on_repo` is called with each repo's label *before* it is checked — the repo
    is the only unit a caller can report progress against, since one `check_repo`
    is up to three round-trips with nothing observable between them. It is also
    the cancellation seam: a callback that raises stops the run where it stands,
    which is the only way to interrupt a blocking fetch cooperatively.
    """
    mcp_cache: dict[str, dict[str, str] | None] = {}
    checks = []
    for repo in bucket_repos(cfg, bucket):
        if on_repo is not None:
            on_repo(_repo_label(repo))
        checks.append(check_repo(cfg, repo, mcp_cache=mcp_cache))
    return checks


def _repo_lines(check: RepoCheck) -> list[str]:
    lines = [check.repo, f"  provider: {check.provider or 'none'}"]
    if not check.provider:
        lines.append("  this repo tracks no tickets — it contributes none")
        return lines
    for name, is_set in sorted(check.credentials.items()):
        lines.append(f"  credential {name}: {'set' if is_set else 'UNSET'}")
    lines.append(f"  connection: {'ok' if check.connected else 'FAIL'}")
    if check.scopes:
        lines.append(f"  scope: {', '.join(check.scopes)}")
        if check.unknown_scopes is None:
            lines.append("  scope names: not checked (the tracker couldn't be asked)")
        elif check.unknown_scopes:
            lines.append(
                "  scope names: FAIL — the tracker knows no "
                f"{', '.join(check.unknown_scopes)}"
            )
        else:
            lines.append("  scope names: ok")
    else:
        lines.append("  scope: (none declared)")
    lines.append(f"  mcp server: {check.mcp_server or '(none declared)'}")
    if check.mcp_server:
        lines.append(f"  mcp reachable: {_mcp_verdict(check.mcp_health)}")
    return lines


def _mcp_verdict(health: str | None) -> str:
    """The `mcp reachable:` line's text.

    "Not listed" is reported as a *probable* miss rather than a fact, because
    `claude mcp list` health-checks by connecting and a managed connector
    handshakes asynchronously — it has called a live server absent. Anything the
    probe did say is passed through verbatim, including a wording cockpit does
    not recognise.
    """
    if health is None:
        return "not checked (`claude mcp list` could not be read)"
    if not health:
        return (
            "`claude mcp list` does not name it — check the spelling, or the "
            "scope it is registered in (a managed connector can also read as "
            "absent while it is still connecting)"
        )
    if health == "connected":
        return "connected"
    return f"{health} — Claude Code lists it but cannot use it"


def format_report(bucket: str, checks: list[RepoCheck]) -> str:
    """The check as the text `ConfigScreen` renders.

    A plain report rather than a pass/fail headline: several of these lines are
    legitimately empty on a working setup (a repo may declare no scope, GitHub
    no credential), so the reader needs the whole picture rather than a verdict
    computed from it.
    """
    if not checks:
        return (
            f"No configured repo files its tickets under {bucket!r}.\n"
            "The bucket is an org name, or a repo name when the repo declares "
            "no org."
        )
    out: list[str] = []
    for check in checks:
        out.extend(_repo_lines(check))
        out.append("")
    return "\n".join(out).rstrip()
