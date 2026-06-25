"""Ticket-provider abstraction — the single place that maps the `tickets` enum
(``none | linear | github | jira``) onto the per-provider functions.

Without this, the slow tick would sprinkle `provider == "github" ? github_x :
linear_x` ternaries across the prefetch / devdone path. `provider_for(cfg,
repo_entry)` resolves a repo to its `TicketProvider` (or None for
`tickets: none`); the rest of `cycle.py` then calls the provider's strategy
methods instead of branching on a name.

The provider holds the *pure* picks (the dev-done state/label name, the PR-body
footer parser) and the state-fetch (which differs by transport — Linear GraphQL
vs `gh issue view`) but normalizes to one shape: `{id: dev-done-comparable
state}`. The ctx-bound *write* path (the merge-done writer — markers, printing,
cached viewer) stays in `cycle.py` and dispatches on `provider.name`, since it's
orchestration, not a leaf strategy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import (
    github_dev_done_label,
    jira_dev_done_status,
    jira_email,
    jira_site_url,
    linear_dev_done_state,
    repo_tickets,
)
from .gh import pr_body
from .github_issues import CONFIG_FIELDS as _GITHUB_CONFIG_FIELDS
from .github_issues import fetch_issues, issue_url, parse_github_issue_refs
from .jira import CONFIG_FIELDS as _JIRA_CONFIG_FIELDS
from .jira import fetch_issue_statuses, parse_jira_footer_links, parse_jira_footers
from .linear import CONFIG_FIELDS as _LINEAR_CONFIG_FIELDS
from .linear import fetch_ticket_states, parse_linear_footer_links, parse_linear_footers

# ── config-field schema (drives preflight validation) ───────────────────────
#
# Each provider declares its own `CONFIG_FIELDS` (in `linear.py` / `github_issues
# .py`) as `(name, kind)` pairs; the common fields below apply to every provider.
# `kind` maps to a (predicate, human-description) here, so preflight validates a
# `tickets` block against the *active provider's* schema instead of a hardcoded
# field list — and rejects fields that don't belong to that provider.
_FIELD_KINDS: dict[str, tuple[Callable[[object], bool], str]] = {
    "str": (lambda v: isinstance(v, str), "a string"),
    "str_list": (
        lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
        "a list of strings",
    ),
    "bool": (lambda v: isinstance(v, bool), "true or false"),
}

# Fields valid for every provider (in addition to `provider` itself).
_COMMON_CONFIG_FIELDS: tuple[tuple[str, str], ...] = (("close_on_merge", "bool"),)

_PROVIDER_CONFIG_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "linear": _LINEAR_CONFIG_FIELDS,
    "github": _GITHUB_CONFIG_FIELDS,
    "jira": _JIRA_CONFIG_FIELDS,
}


def tickets_field_errors(block: dict, provider_name: str) -> list[str]:
    """Validation errors for a `tickets` object `block` under `provider_name` —
    unknown fields and type mismatches — as ready-to-print messages (each begins
    `tickets.…`). Empty when valid. Pure: no exit, so preflight maps the first to
    its own `_die`. `none`/unknown providers accept only the common fields.
    """
    allowed = dict(
        _COMMON_CONFIG_FIELDS + _PROVIDER_CONFIG_FIELDS.get(provider_name, ())
    )
    errors: list[str] = []
    for key, val in block.items():
        if key == "provider":
            continue
        kind = allowed.get(key)
        if kind is None:
            names = ", ".join(sorted(allowed)) or "(none)"
            errors.append(
                f"tickets has an unknown field {key!r} for provider "
                f"{provider_name!r} (allowed: {names})."
            )
            continue
        check, desc = _FIELD_KINDS[kind]
        if val is not None and not check(val):
            errors.append(f"tickets.{key} must be {desc}, got {val!r}.")
    return errors


@dataclass(frozen=True)
class TicketProvider:
    """A ticket provider's strategy. Pure/leaf — never touches the daemon's
    per-cycle state (that orchestration stays in `cycle.py`)."""

    name: str
    # (cfg, repo_entry) → the dev-done state/label name the `devdone=` pill
    # matches against (per-repo, since the `tickets` block can be repo-scoped).
    dev_done_value: Callable[[dict, dict | None], str]
    # (pr_body, repo_nwo) → the ids of the tickets the PR delivers.
    parse_footers: Callable[[str, str], list[str]]
    # (ids, repo_nwo, repo_dir, cfg, repo_entry) → {id: dev-done-comparable
    # state name}. The values compare casefold-equal to `dev_done_value(cfg,
    # repo_entry)` exactly when the ticket/issue is dev-done, so
    # `_track_dev_done` is provider-neutral.
    fetch_states: Callable[..., dict[str, str | None]]
    # (ref, *, repo_nwo, repo_dir, pr_number) → the ticket's web URL, or None.
    # GitHub builds it deterministically from ref + repo_nwo; Linear has no
    # constructable URL (workspace slug unknown), so it reads the PR body's
    # `Linear: [ID](url)` footer link via repo_dir + pr_number. Both ignore the
    # kwargs the other needs — the TUI's "open ticket" action passes all four so
    # neither provider has to branch on the caller.
    ticket_url: Callable[..., str | None]


def _github_fetch_states(
    ids: list[str],
    *,
    repo_nwo: str,
    repo_dir: str,
    cfg: dict,
    repo_entry: dict | None = None,
) -> dict[str, str | None]:
    """`{ref: state}` for GitHub issues. The value is the configured dev-done
    label (`github_dev_done_label`) when the issue carries it, else the issue's
    open/closed state — so the same casefold comparison in `_track_dev_done`
    lights the pill for a dev-done issue exactly as it does for a Linear ticket
    in its dev-done state. Unreadable issues map to None.
    """
    label = github_dev_done_label(cfg, repo_entry)
    label_cf = label.casefold()
    issues = fetch_issues(ids, repo_nwo=repo_nwo, repo_dir=repo_dir)
    out: dict[str, str | None] = {}
    for ref, issue in issues.items():
        if issue and label_cf in (issue.get("labels") or []):
            out[ref] = label
        else:
            out[ref] = (issue or {}).get("state")
    return out


def _github_ticket_url(
    ref: str,
    *,
    repo_nwo: str | None = None,
    repo_dir: str | None = None,
    pr_number: int | None = None,
) -> str | None:
    """Deterministic GitHub issue URL from the delivered ref + the PR's repo nwo.
    No network: `repo_dir`/`pr_number` are unused (kept for the uniform
    `ticket_url` signature)."""
    return issue_url(ref, repo_nwo)


def _linear_ticket_url(
    ref: str,
    *,
    repo_nwo: str | None = None,
    repo_dir: str | None = None,
    pr_number: int | None = None,
) -> str | None:
    """The Linear ticket URL — read from the PR body's `Linear: [ID](url)` footer
    link (the canonical URL can't be hand-constructed; the workspace slug isn't
    known). Needs `repo_dir` (the worktree, so `gh` resolves the repo) and
    `pr_number`; `repo_nwo` is unused. None when the body can't be fetched or has
    no matching footer link."""
    if not repo_dir or not pr_number:
        return None
    links = dict(parse_linear_footer_links(pr_body(Path(repo_dir), pr_number)))
    return links.get(ref.upper())


def _linear_fetch_states(
    ids: list[str],
    *,
    repo_nwo: str,
    repo_dir: str,
    cfg: dict,
    repo_entry: dict | None = None,
) -> dict[str, str | None]:
    """`{id: workflow-state name}` via the batched Linear query (one per team).
    The repo_nwo/repo_dir/cfg/repo_entry kwargs are unused — Linear keys off the
    global `LINEAR_API_KEY` — but kept for a uniform `fetch_states` signature.
    """
    return fetch_ticket_states(ids)


def _jira_fetch_states(
    ids: list[str],
    *,
    repo_nwo: str,
    repo_dir: str,
    cfg: dict,
    repo_entry: dict | None = None,
) -> dict[str, str | None]:
    """`{key: status name}` via the Jira REST API (one GET per key). `site_url`
    and `email` come from the `tickets` config block; the token from
    `$JIRA_API_TOKEN`. The repo_nwo/repo_dir kwargs are unused — Jira keys off
    the global site/email/token — but kept for a uniform `fetch_states` signature.
    All keys map to None when the site or email is unconfigured (feature off)."""
    site = jira_site_url(cfg, repo_entry)
    email = jira_email(cfg, repo_entry)
    if not site or not email:
        return {i: None for i in ids}
    return fetch_issue_statuses(ids, site_url=site, email=email)


def _jira_ticket_url(
    ref: str,
    *,
    repo_nwo: str | None = None,
    repo_dir: str | None = None,
    pr_number: int | None = None,
) -> str | None:
    """The Jira issue URL — read from the PR body's `Jira: [PROJ-123](url)` footer
    link, uniform with Linear's `_linear_ticket_url` (the cfg-less `ticket_url`
    signature can't thread `site_url`, and the delivery footer carries the URL
    anyway). Needs `repo_dir` + `pr_number`; None when the body can't be fetched
    or has no matching footer link."""
    if not repo_dir or not pr_number:
        return None
    links = dict(parse_jira_footer_links(pr_body(Path(repo_dir), pr_number)))
    return links.get(ref.upper())


LINEAR = TicketProvider(
    name="linear",
    dev_done_value=linear_dev_done_state,
    parse_footers=lambda body, _nwo: parse_linear_footers(body),
    fetch_states=_linear_fetch_states,
    ticket_url=_linear_ticket_url,
)

JIRA = TicketProvider(
    name="jira",
    dev_done_value=jira_dev_done_status,
    parse_footers=lambda body, _nwo: parse_jira_footers(body),
    fetch_states=_jira_fetch_states,
    ticket_url=_jira_ticket_url,
)

GITHUB = TicketProvider(
    name="github",
    dev_done_value=github_dev_done_label,
    parse_footers=parse_github_issue_refs,
    fetch_states=_github_fetch_states,
    ticket_url=_github_ticket_url,
)

_PROVIDERS: dict[str, TicketProvider] = {
    "linear": LINEAR,
    "github": GITHUB,
    "jira": JIRA,
}


def provider_for(
    cfg: dict | None = None, repo_entry: dict | None = None
) -> TicketProvider | None:
    """The repo's `TicketProvider`, or None for `tickets: none` — the single
    entry point the slow tick uses instead of branching on the enum string."""
    return _PROVIDERS.get(repo_tickets(cfg, repo_entry))
