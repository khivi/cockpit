"""`cockpit config` — read-only introspection of the config cockpit resolved.

`config.py::apply_org_defaults` merges the `orgs` block into each member repo
at `load_config()` time (repo scalars win; block-valued fields union per-field,
repo winning), and `expand_sidebar_tags` then substitutes `{repo}`. Neither is
ever persisted — every config writer re-reads `config.json` from disk, so the
merged shape exists only in memory for the life of a process. With several
repos inheriting an org block, there is otherwise no way to see what a repo
actually resolved to without re-running the merge by hand.

`cockpit config inspect` prints exactly that: `load_config()`'s return value,
pretty-printed. `--repo NAME` scopes to one repo entry (matched the way
`broadcast.py::_repo_label` does — the `name`-or-basename identity,
casefolded; an unknown name exits 2 listing the configured repos, the same
contract `broadcast --repo` already gives) and additionally surfaces what that
repo's `tickets` block resolves to: the `TicketProvider`
(`lib.tickets.provider_for`) and the *names* of the credential env vars it
needs (`TicketProvider.credential_envs`), each flagged set/unset.

`cockpit config tickets` is the same resolution aimed at a *session* rather
than an operator: it defaults to the cwd's repo, so a Claude session parked in
a worktree can ask which tracker its repo files against without knowing the
repo's configured name. It is the read half of the ticket-identity seam the
spawn prompt's `tickets_block` states on turn one — the prompt is a snapshot
frozen at spawn, this re-reads live config, which is what a session past a
compact or a config edit needs. **Do not** cache either one: this resolves a
local JSON read plus one `git worktree list`, so a stored copy would only be
staler than asking.

Every field it prints comes off the `TicketProvider` (`inbox_scopes`,
`credential_envs`), never a `provider == "linear"` branch — that class exists
to hold the per-provider knowledge, and the scoping field is `keys` for two
providers, `board` for a third and absent for the fourth.

Read-only, like `lib/starship.py`'s field printers: no `gh`, no network, no
cache or config write. `load_config()` itself never mutates `config.json` — the
org merge and `{repo}` expansion happen in memory only. The one subprocess is
`tickets`' cwd resolution, a `git worktree list --porcelain` via
`config.discover_repo`.

Never prints a credential *value*. The set/unset check is `bool(os.environ.get
(name))` — the name is read into output, the value never is, matching the rule
every other warning/log/error message in this codebase already follows for a
credential env var.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from cockpit.lib.config import discover_repo, load_config, ticket_mcp_server
from cockpit.lib.tickets import provider_for


def _dump(value: object) -> str:
    """Pretty JSON, non-ASCII left alone. `ensure_ascii` would print a
    `sidebar_tag`'s emoji back as `\\ud83d\\udee1` — and a resolved
    `sidebar_tag` is one of the two things this command exists to show."""
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def _repo_label(repo: dict) -> str:
    """The repo's one identity — the same `name`-or-basename `broadcast._repo_label`
    uses. Kept as its own copy rather than imported: `cockpit.broadcast` pulls in
    the cmux-only import block for a three-line helper this module has no other
    use for."""
    return repo.get("name") or Path(os.path.expanduser(repo["path"])).name


def _find_repo(cfg: dict, name: str) -> dict | None:
    for repo in cfg.get("repos", []):
        if _repo_label(repo).casefold() == name.casefold():
            r: dict = repo
            return r
    return None


def _credential_status(cfg: dict, repo: dict) -> dict[str, bool]:
    """`{env var name: currently set}` for the repo's resolved ticket provider.
    Empty for `tickets: none` and for GitHub (which authenticates through `gh`
    and declares no credential env vars). Never reads a value into a variable
    that reaches output — only `bool(os.environ.get(name))`."""
    provider = provider_for(cfg, repo)
    if provider is None:
        return {}
    return {
        name: bool(os.environ.get(name)) for name in provider.credential_envs(cfg, repo)
    }


def _print_credentials(cfg: dict, repo: dict) -> None:
    """The set/unset table, in the one place both verbs render it — a credential
    value must never reach output, so that constraint gets a single site."""
    creds = _credential_status(cfg, repo)
    if not creds:
        return
    print("credentials:")
    for name, is_set in sorted(creds.items()):
        print(f"  {name}: {'set' if is_set else 'unset'}")


def _unknown_repo(cfg: dict, name: str, verb: str) -> int:
    known = ", ".join(sorted(_repo_label(r) for r in cfg.get("repos", [])))
    print(
        f"cockpit config {verb}: unknown repo {name!r}; configured: {known or '(none)'}",
        file=sys.stderr,
    )
    return 2


def _cmd_inspect(args: argparse.Namespace) -> int:
    cfg = load_config()
    if not args.repo:
        print(_dump(cfg))
        return 0

    repo = _find_repo(cfg, args.repo)
    if repo is None:
        return _unknown_repo(cfg, args.repo, "inspect")

    print(_dump(repo))
    provider = provider_for(cfg, repo)
    print()
    print(f"tickets.provider: {provider.name if provider else 'none'}")
    _print_credentials(cfg, repo)
    return 0


def _cmd_tickets(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.repo:
        repo = _find_repo(cfg, args.repo)
        if repo is None:
            return _unknown_repo(cfg, args.repo, "tickets")
    else:
        # Defaulting to the cwd is the point of this verb: a session knows which
        # worktree it is in and not what the config calls the repo.
        repo = discover_repo()
        if repo is None:
            known = ", ".join(sorted(_repo_label(r) for r in cfg.get("repos", [])))
            print(
                "cockpit config tickets: the cwd is not inside a configured repo; "
                f"configured: {known or '(none)'}",
                file=sys.stderr,
            )
            return 2

    print(f"repo: {_repo_label(repo)}")
    provider = provider_for(cfg, repo)
    print(f"provider: {provider.name if provider else 'none'}")
    if provider is None:
        return 0

    scopes = provider.inbox_scopes(cfg, repo)
    print(f"scope: {', '.join(scopes) if scopes else '(none declared)'}")
    server = ticket_mcp_server(cfg, repo)
    print(f"mcp server: {server or '(none declared)'}")
    _print_credentials(cfg, repo)
    return 0


def main(argv: list[str] | None = None) -> int:
    # A short description of its own rather than `__doc__`: the module docstring
    # is written for whoever maintains this and names the functions it describes,
    # and `--help` is user-facing, where a name that a refactor can invalidate
    # does not belong.
    p = argparse.ArgumentParser(
        prog="cockpit config",
        description="Show the configuration cockpit actually resolved, after "
        "org defaults are merged in and tag placeholders are expanded. "
        "Read-only — reaches nothing outside this process.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    inspect = sub.add_parser(
        "inspect",
        help="Print the effective config (post-org-merge, post-{repo}-expansion).",
    )
    inspect.add_argument(
        "--repo",
        metavar="NAME",
        help="Scope to one configured repo, named as the dashboard names it "
        "(case-insensitive), and also show its resolved ticket provider + "
        "credential env var names. Default: the whole effective config.",
    )
    inspect.set_defaults(func=_cmd_inspect)

    tickets = sub.add_parser(
        "tickets",
        help="Print the ticket tracker this repo files against — provider, the "
        "scope it files into, and which env vars hold its credentials.",
    )
    tickets.add_argument(
        "--repo",
        metavar="NAME",
        help="Scope to one configured repo by name (case-insensitive). "
        "Default: the repo the cwd belongs to.",
    )
    tickets.set_defaults(func=_cmd_tickets)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
