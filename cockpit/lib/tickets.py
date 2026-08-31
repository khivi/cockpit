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
    github_dev_done,
    jira_api_token,
    jira_dev_done,
    jira_email,
    jira_site_url,
    jira_token_env,
    linear_api_key,
    linear_dev_done,
    linear_project,
    linear_token_env,
    repo_tickets,
    trello_api_key,
    trello_api_token,
    trello_board,
    trello_dev_done,
    trello_key_env,
    trello_token_env,
)
from .gh import pr_body
from .github_issues import CONFIG_FIELDS as _GITHUB_CONFIG_FIELDS
from .github_issues import fetch_issues, issue_url, parse_github_issue_refs
from .jira import CONFIG_FIELDS as _JIRA_CONFIG_FIELDS
from .jira import (
    fetch_issue_statuses,
    fetch_issue_summaries,
    parse_jira_footer_links,
    parse_jira_footers,
)
from .linear import CONFIG_FIELDS as _LINEAR_CONFIG_FIELDS
from .linear import (
    fetch_ticket_project,
    fetch_ticket_states,
    fetch_ticket_titles,
    parse_linear_footer_links,
    parse_linear_footers,
)
from .trello import CONFIG_FIELDS as _TRELLO_CONFIG_FIELDS
from .trello import (
    card_short_link,
    fetch_card_board,
    fetch_card_lists,
    fetch_card_names,
    parse_trello_footer_links,
    parse_trello_footers,
)

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
    "trello": _TRELLO_CONFIG_FIELDS,
}


def tickets_field_errors(block: dict, provider_name: str) -> list[str]:
    """Validation errors for a `tickets` object `block` under `provider_name` —
    unknown fields and type mismatches — as ready-to-print messages (each begins
    `tickets.…`). Empty when valid. Pure: no exit, so preflight maps the first to
    its own `_die`. `none`/unknown providers accept only the common fields.

    The allowed set is composed from the *active* provider only, so a field name
    two providers share (`token_env`, declared by both Jira and Trello) validates
    under either without being accepted under Linear/GitHub.
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
    # (ids, repo_nwo, repo_dir, cfg, repo_entry) → {id: human-title or None}.
    # Same signature as `fetch_states`; the enrichment cockpit writes into the PR
    # cache so a statusline consumer (cship) shows the ticket name beside its id
    # without its own API round-trip. None per id on any failure/unset creds.
    fetch_titles: Callable[..., dict[str, str | None]]
    # (ref, candidates, cfg) → the subset of `candidates` (config repo entries)
    # the ticket `ref` belongs to. The tiebreaker for a ticket id that routes to
    # more than one repo — the *identifier* carries only the outer container
    # (Linear team, Jira project key) or none at all (a Trello card short link),
    # and repos sharing one all match it. Called ONLY when a cheap match already
    # returned >1 candidate, so the providers that need a network fetch to answer
    # (Linear: the issue's project; Trello: the card's board) never pay for it in
    # the common single-match case. Returns `candidates` unchanged when it
    # can't improve on them — no repo configures the discriminator, the fetch
    # failed, or nothing matched — so an inconclusive narrow degrades to the
    # caller's existing ambiguity path rather than narrowing to zero.
    narrow_repos: Callable[[str, list[dict], dict], list[dict]]
    # (ref, *, repo_nwo, repo_dir, pr_number, body) → the ticket's web URL, or
    # None. GitHub builds it deterministically from ref + repo_nwo; Linear has no
    # constructable URL (workspace slug unknown), so it reads the PR body's
    # `Linear: [ID](url)` footer link via repo_dir + pr_number. Both ignore the
    # kwargs the other needs — the TUI's "open ticket" action passes every one of
    # them so neither provider has to branch on the caller.
    #
    # `body` is the PR body when the caller already holds it, and it makes the
    # three footer-reading providers network-free: the daemon has `pr.body` every
    # cycle, so it resolves the URL for the PR cache without a `gh pr body` of its
    # own. Passing it skips the fetch entirely; omitting it keeps the fetch, so a
    # caller that holds no body is unaffected.
    ticket_url: Callable[..., str | None]
    # (cfg, repo_entry) → the env var *names* this provider needs credentials in,
    # resolved for that repo (so a per-org `token_env` yields the org's name).
    # Names only — a value never reaches this seam. Empty for GitHub, which
    # authenticates through `gh`; two for Trello, which needs a key *and* a token.
    # `preflight` warns on the unset ones so a missing credential surfaces at
    # start rather than as a silently unresolved ticket cell cycles later.
    credential_envs: Callable[[dict, dict | None], list[str]]


def _github_fetch_states(
    ids: list[str],
    *,
    repo_nwo: str,
    repo_dir: str,
    cfg: dict,
    repo_entry: dict | None = None,
) -> dict[str, str | None]:
    """`{ref: state}` for GitHub issues. The value is the configured dev-done
    label (`github_dev_done`) when the issue carries it, else the issue's
    open/closed state — so the same casefold comparison in `_track_dev_done`
    lights the pill for a dev-done issue exactly as it does for a Linear ticket
    in its dev-done state. Unreadable issues map to None.
    """
    label = github_dev_done(cfg, repo_entry)
    label_cf = label.casefold()
    issues = fetch_issues(ids, repo_nwo=repo_nwo, repo_dir=repo_dir)
    out: dict[str, str | None] = {}
    for ref, issue in issues.items():
        if issue and label_cf in (issue.get("labels") or []):
            out[ref] = label
        else:
            out[ref] = (issue or {}).get("state")
    return out


def _github_fetch_titles(
    ids: list[str],
    *,
    repo_nwo: str,
    repo_dir: str,
    cfg: dict,
    repo_entry: dict | None = None,
) -> dict[str, str | None]:
    """`{ref: issue-title or None}`. `fetch_issues` already returns the title, so
    this just projects it. Unreadable issues map to None."""
    issues = fetch_issues(ids, repo_nwo=repo_nwo, repo_dir=repo_dir)
    return {ref: (issue or {}).get("title") for ref, issue in issues.items()}


def _github_ticket_url(
    ref: str,
    *,
    repo_nwo: str | None = None,
    repo_dir: str | None = None,
    pr_number: int | None = None,
    body: str | None = None,
) -> str | None:
    """Deterministic GitHub issue URL from the delivered ref + the PR's repo nwo.
    No network: `repo_dir`/`pr_number`/`body` are unused (kept for the uniform
    `ticket_url` signature)."""
    return issue_url(ref, repo_nwo)


def _footer_url(
    parse: Callable[[str], list[tuple[str, str]]],
    ref: str,
    *,
    repo_dir: str | None,
    pr_number: int | None,
    body: str | None,
) -> str | None:
    """The delivery-footer link for `ref`, shared by the three providers whose
    ticket URL can only be *read* rather than constructed (Linear's workspace
    slug, Jira's site, Trello's board/card slug are all unknown from the id).

    `body` short-circuits the fetch when the caller already holds the PR body —
    the daemon does, every cycle, so it resolves these URLs for the PR cache
    without a `gh pr body` per ticket. Without one, `repo_dir` + `pr_number`
    fetch it, which is the TUI's fallback path for a not-yet-restamped cache."""
    if body is None:
        if not repo_dir or not pr_number:
            return None
        body = pr_body(Path(repo_dir), pr_number)
    return dict(parse(body)).get(ref)


def _linear_ticket_url(
    ref: str,
    *,
    repo_nwo: str | None = None,
    repo_dir: str | None = None,
    pr_number: int | None = None,
    body: str | None = None,
) -> str | None:
    """The Linear ticket URL — read from the PR body's `Linear: [ID](url)` footer
    link (the canonical URL can't be hand-constructed; the workspace slug isn't
    known). Takes the body directly, or fetches it from `repo_dir` (the worktree,
    so `gh` resolves the repo) + `pr_number`; `repo_nwo` is unused. None when the
    body can't be fetched or has no matching footer link."""
    return _footer_url(
        parse_linear_footer_links,
        ref.upper(),
        repo_dir=repo_dir,
        pr_number=pr_number,
        body=body,
    )


def _linear_fetch_states(
    ids: list[str],
    *,
    repo_nwo: str,
    repo_dir: str,
    cfg: dict,
    repo_entry: dict | None = None,
) -> dict[str, str | None]:
    """`{id: workflow-state name}` via the batched Linear query (one per team).
    The API key is resolved per-repo (`config.linear_api_key` → the env var named
    by `tickets.token_env`), so two orgs can use different Linear workspaces.
    repo_nwo/repo_dir are unused, kept for a uniform `fetch_states` signature.
    """
    return fetch_ticket_states(ids, api_key=linear_api_key(cfg, repo_entry) or None)


def _linear_fetch_titles(
    ids: list[str],
    *,
    repo_nwo: str,
    repo_dir: str,
    cfg: dict,
    repo_entry: dict | None = None,
) -> dict[str, str | None]:
    """`{id: title}` via the batched Linear query (one per team). Same per-repo
    key resolution as `_linear_fetch_states`; repo_nwo/repo_dir unused, kept for
    the uniform `fetch_titles` signature."""
    return fetch_ticket_titles(ids, api_key=linear_api_key(cfg, repo_entry) or None)


def _jira_fetch_states(
    ids: list[str],
    *,
    repo_nwo: str,
    repo_dir: str,
    cfg: dict,
    repo_entry: dict | None = None,
) -> dict[str, str | None]:
    """`{key: status name}` via the Jira REST API (one GET per key). `site_url`
    and `email` come from the `tickets` config block; the token from the env var
    named by `tickets.token_env` (default `JIRA_API_TOKEN`), resolved per-repo so
    two orgs can hit different Jira sites. The repo_nwo/repo_dir kwargs are
    unused, kept for a uniform `fetch_states` signature. All keys map to None
    when the site or email is unconfigured (feature off)."""
    site = jira_site_url(cfg, repo_entry)
    email = jira_email(cfg, repo_entry)
    if not site or not email:
        return {i: None for i in ids}
    return fetch_issue_statuses(
        ids, site_url=site, email=email, token=jira_api_token(cfg, repo_entry) or None
    )


def _jira_fetch_titles(
    ids: list[str],
    *,
    repo_nwo: str,
    repo_dir: str,
    cfg: dict,
    repo_entry: dict | None = None,
) -> dict[str, str | None]:
    """`{key: summary}` via the Jira REST API (one GET per key). All keys map to
    None when the site or email is unconfigured (feature off)."""
    site = jira_site_url(cfg, repo_entry)
    email = jira_email(cfg, repo_entry)
    if not site or not email:
        return {i: None for i in ids}
    return fetch_issue_summaries(
        ids, site_url=site, email=email, token=jira_api_token(cfg, repo_entry) or None
    )


def _jira_ticket_url(
    ref: str,
    *,
    repo_nwo: str | None = None,
    repo_dir: str | None = None,
    pr_number: int | None = None,
    body: str | None = None,
) -> str | None:
    """The Jira issue URL — read from the PR body's `Jira: [PROJ-123](url)` footer
    link, uniform with Linear's `_linear_ticket_url` (the cfg-less `ticket_url`
    signature can't thread `site_url`, and the delivery footer carries the URL
    anyway). Takes the body directly, or fetches it from `repo_dir` + `pr_number`;
    None when the body can't be fetched or has no matching footer link."""
    return _footer_url(
        parse_jira_footer_links,
        ref.upper(),
        repo_dir=repo_dir,
        pr_number=pr_number,
        body=body,
    )


def _trello_fetch_states(
    ids: list[str],
    *,
    repo_nwo: str,
    repo_dir: str,
    cfg: dict,
    repo_entry: dict | None = None,
) -> dict[str, str | None]:
    """`{short_link: list-name}` via the Trello REST API (one GET per card). The
    key+token are resolved per-repo (the env vars named by `tickets.key_env` /
    `tickets.token_env`, defaults `TRELLO_API_KEY`/`TRELLO_API_TOKEN`), so two
    orgs can use different Trello accounts. repo_nwo/repo_dir are unused, kept
    for a uniform `fetch_states` signature. All ids map to None when creds are
    unset (feature off)."""
    return fetch_card_lists(
        ids,
        key=trello_api_key(cfg, repo_entry) or None,
        token=trello_api_token(cfg, repo_entry) or None,
    )


def _trello_fetch_titles(
    ids: list[str],
    *,
    repo_nwo: str,
    repo_dir: str,
    cfg: dict,
    repo_entry: dict | None = None,
) -> dict[str, str | None]:
    """`{short_link: card_name}` via the Trello REST API (one GET per card). Same
    per-repo cred resolution as `_trello_fetch_states`. All ids map to None when
    creds are unset (feature off)."""
    return fetch_card_names(
        ids,
        key=trello_api_key(cfg, repo_entry) or None,
        token=trello_api_token(cfg, repo_entry) or None,
    )


def _trello_ticket_url(
    ref: str,
    *,
    repo_nwo: str | None = None,
    repo_dir: str | None = None,
    pr_number: int | None = None,
    body: str | None = None,
) -> str | None:
    """The Trello card URL — read from the PR body's `Trello: [title](url)` footer
    link (uniform with Linear/Jira; a card URL can't be hand-constructed from the
    short link without the board/card slug). Takes the body directly, or fetches
    it from `repo_dir` + `pr_number`; None when the body can't be fetched or has
    no matching footer link. The short link is case-sensitive, so the lookup keys
    on `ref` verbatim (no upper/lower)."""
    return _footer_url(
        parse_trello_footer_links,
        ref,
        repo_dir=repo_dir,
        pr_number=pr_number,
        body=body,
    )


def _no_narrow(ref: str, candidates: list[dict], cfg: dict) -> list[dict]:
    """`narrow_repos` for a provider whose identifier already resolves the repo as
    far as it can: GitHub (the issue ref carries `owner/repo`, so routing never
    reaches an ambiguous set) and Jira (its "project" **is** the key prefix the
    free match already used — the Linear *team* analogue, not the Linear *project*
    one — so there is no container left below it to discriminate on). Leaves the
    candidates alone."""
    return candidates


def _linear_narrow_repos(ref: str, candidates: list[dict], cfg: dict) -> list[dict]:
    """Narrow `candidates` to the repos whose `tickets.project` is the Linear
    project ticket `ref` belongs to.

    The many-repos-one-team case: they all declare the same `tickets.keys`, so the
    identifier alone matches every one of them. The project isn't in the
    identifier, so this costs one GraphQL fetch — paid only here, i.e. only once
    the free key match came back ambiguous.

    Returns `candidates` untouched when no candidate declares a project, the fetch
    fails, or the resolved project matches none of them — so the caller's existing
    "ambiguous, fall back" path still runs instead of this narrowing to zero.

    Candidates are grouped by their **resolved credential** (`linear_token_env`,
    which walks repo → org → global → default), and each group is asked with its
    own key. A team key is *workspace*-scoped, so two orgs on separate Linear
    workspaces can each own an `ENG` team and both match `ENG-1234` — querying one
    org's workspace about the other's ticket would answer about a *different*
    issue that merely shares an identifier, and mis-route the spawn. Grouping is
    also why this costs exactly one fetch in the ordinary single-workspace case:
    the extra round-trips appear only when the candidates genuinely span
    workspaces, which is the only way to answer correctly. Repos declaring no
    project never join a group, so they never trigger a fetch.

    Groups are tried in config order and the first confident hit wins. Two
    workspaces both holding a live `ENG-1234` in a project some repo claims is
    irreducibly ambiguous — the identifier cannot distinguish them — so first-hit
    is as good as any rule and never worse than the un-narrowed fallback.
    """
    by_env: dict[str, list[dict]] = {}
    for repo in candidates:
        if linear_project(cfg, repo):
            by_env.setdefault(linear_token_env(cfg, repo), []).append(repo)
    for group in by_env.values():
        key = linear_api_key(cfg, group[0]) or None
        if not key:
            continue
        project = fetch_ticket_project(ref, api_key=key)
        if not project:
            continue
        wanted = project.casefold()
        hit = [r for r in group if (linear_project(cfg, r) or "").casefold() == wanted]
        if hit:
            return hit
    return candidates


def _trello_narrow_repos(ref: str, candidates: list[dict], cfg: dict) -> list[dict]:
    """Narrow `candidates` to the repos whose `tickets.board` is the board the
    card `ref` (a card URL or a bare short link) lives on.

    Trello's whole route, not a tiebreaker: a short link carries no board, no
    project and no key prefix, so there is nothing free to match on first — which
    is exactly why the *caller* only reaches this once some repo has opted in by
    declaring a `board`. A candidate declaring none never joins a group, so a
    config with no boards costs zero network calls and narrows nothing.

    Mirrors `_linear_narrow_repos`'s three properties. It never narrows to zero
    (no board configured, the fetch failed, or the resolved board matches nobody →
    `candidates` unchanged, so the caller's ambiguity path still runs). It groups
    candidates by their **resolved credential pair** (`trello_key_env` +
    `trello_token_env`, each walking repo → org → global → default) and asks each
    group with its own key+token: a board name is *account*-scoped exactly as a
    Linear team key is workspace-scoped, so two orgs can each own an "Engineering"
    board, and asking one account about the other's card answers about a different
    card — or 404s — and mis-routes the spawn. And it is routing-only: nothing
    downstream reads `board`. Groups are tried in config order, first confident hit
    wins, and a group whose credentials are unset is skipped (it can't be asked).
    """
    short = card_short_link(ref) or ref
    by_creds: dict[tuple[str, str], list[dict]] = {}
    for repo in candidates:
        if trello_board(cfg, repo):
            creds = (trello_key_env(cfg, repo), trello_token_env(cfg, repo))
            by_creds.setdefault(creds, []).append(repo)
    for group in by_creds.values():
        key = trello_api_key(cfg, group[0]) or None
        token = trello_api_token(cfg, group[0]) or None
        if not key or not token:
            continue
        board = fetch_card_board(short, key=key, token=token)
        if not board:
            continue
        wanted = board.casefold()
        hit = [r for r in group if (trello_board(cfg, r) or "").casefold() == wanted]
        if hit:
            return hit
    return candidates


LINEAR = TicketProvider(
    name="linear",
    dev_done_value=linear_dev_done,
    parse_footers=lambda body, _nwo: parse_linear_footers(body),
    fetch_states=_linear_fetch_states,
    fetch_titles=_linear_fetch_titles,
    narrow_repos=_linear_narrow_repos,
    ticket_url=_linear_ticket_url,
    credential_envs=lambda cfg, repo: [linear_token_env(cfg, repo)],
)

JIRA = TicketProvider(
    name="jira",
    dev_done_value=jira_dev_done,
    parse_footers=lambda body, _nwo: parse_jira_footers(body),
    fetch_states=_jira_fetch_states,
    fetch_titles=_jira_fetch_titles,
    narrow_repos=_no_narrow,
    ticket_url=_jira_ticket_url,
    credential_envs=lambda cfg, repo: [jira_token_env(cfg, repo)],
)

GITHUB = TicketProvider(
    name="github",
    dev_done_value=github_dev_done,
    parse_footers=parse_github_issue_refs,
    fetch_states=_github_fetch_states,
    fetch_titles=_github_fetch_titles,
    narrow_repos=_no_narrow,
    ticket_url=_github_ticket_url,
    # `gh` owns the auth; there is no cockpit-read env var to warn about.
    credential_envs=lambda _cfg, _repo: [],
)

TRELLO = TicketProvider(
    name="trello",
    dev_done_value=trello_dev_done,
    parse_footers=lambda body, _nwo: parse_trello_footers(body),
    fetch_states=_trello_fetch_states,
    fetch_titles=_trello_fetch_titles,
    narrow_repos=_trello_narrow_repos,
    ticket_url=_trello_ticket_url,
    credential_envs=lambda cfg, repo: [
        trello_key_env(cfg, repo),
        trello_token_env(cfg, repo),
    ],
)

_PROVIDERS: dict[str, TicketProvider] = {
    "linear": LINEAR,
    "github": GITHUB,
    "jira": JIRA,
    "trello": TRELLO,
}


def provider_for(
    cfg: dict | None = None, repo_entry: dict | None = None
) -> TicketProvider | None:
    """The repo's `TicketProvider`, or None for `tickets: none` — the single
    entry point the slow tick uses instead of branching on the enum string."""
    return _PROVIDERS.get(repo_tickets(cfg, repo_entry))
