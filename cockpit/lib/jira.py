"""Jira ticket helpers — the `tickets: jira` provider.

The Jira analog of `lib.linear`. Same contract: every call degrades to
None/False/empty — never raises — on a missing token, an unset site/email, a
timeout, or an API error, so a flaky Jira can't stall the reconcile.

Like Linear, the issue *body* (summary, description) is fetched by Claude via the
Atlassian/Jira MCP on a spawned worktree's first turn — the daemon can't reach an
MCP. But the daemon *does* make direct REST calls (Jira Cloud REST API v3):

  * read-only — `fetch_issue_statuses` (the `devdone=` pill), plus `fetch_myself`
    / `fetch_issue_meta` (the merge-transition eligibility checks);
  * the one *write* — `transition_issue`, reached only by the opt-in
    `close_on_merge` path in the slow tick (`cycle._transition_merged_jira`); the
    *policy* (which issue, when) lives there, this module just performs the call.

Auth is HTTP Basic with `email:token`: the secret `JIRA_API_TOKEN` comes from the
env, the non-secret `site_url` + `email` from the `tickets` config block (see the
`config.jira_*` readers). The token is sent in the `Authorization` header and
never logged.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request

JIRA_API_TOKEN_ENV = "JIRA_API_TOKEN"

# The Jira-specific fields the `tickets` config block accepts, as `(name, kind)`
# (kind resolved to a validator in `tickets.py`). The provider owns its own
# config surface; this *specification* drives preflight validation (common fields
# like `provider`/`close_on_merge` are added by `tickets.py`). Keep in sync with
# the Jira readers in `config.py` (`jira_site_url`, `jira_email`,
# `jira_dev_done_status`, `jira_merge_done_status`).
CONFIG_FIELDS: tuple[tuple[str, str], ...] = (
    ("site_url", "str"),
    ("email", "str"),
    ("dev_done_status", "str"),
    ("merge_done_status", "str"),
)

# A Jira issue key: a project key (a letter then alphanumerics) joined to an
# issue number by `-` (`PROJ-123`, `R2D2-7`).
_KEY = r"[A-Za-z][A-Za-z0-9]*-[0-9]+"

# A PR *delivers* a Jira issue only via the explicit `Jira: [PROJ-123](url)`
# footer (mirrors Linear's strict, line-anchored delivery footer) — NOT a bare
# branch-slug mention, which would catch predecessor / follow-up issues the PR
# doesn't deliver. Case-insensitive; `parse_jira_footers` uppercases captures to
# the canonical `PROJ-123` form so display, dedup, and REST lookups stay stable.
JIRA_FOOTER_RE = re.compile(rf"^Jira:\s*\[({_KEY})\]", re.MULTILINE | re.IGNORECASE)
# Same footer, capturing the markdown link target so the TUI can open the exact
# issue URL out of the PR body (uniform with Linear — see `tickets._jira_ticket_url`).
JIRA_FOOTER_LINK_RE = re.compile(
    rf"^Jira:\s*\[({_KEY})\]\((\S+?)\)", re.MULTILINE | re.IGNORECASE
)

# Bound each REST call so a hung Jira can't stall the slow tick (degrades to
# None, exactly like a Linear GraphQL timeout).
_TIMEOUT_SECONDS = 10


def parse_jira_footers(body: str) -> list[str]:
    """Return the de-duplicated, order-preserving issue keys declared in `body`'s
    `Jira: [PROJ-123](url)` footer line(s) — the strict set the PR delivers. Keys
    are uppercased to the canonical `PROJ-123` form (the footer match is
    case-insensitive). Empty when `body` is falsy or has no footer — the Jira
    analog of `linear.parse_linear_footers`.
    """
    if not body:
        return []
    return list(dict.fromkeys(key.upper() for key in JIRA_FOOTER_RE.findall(body)))


def parse_jira_footer_links(body: str) -> list[tuple[str, str]]:
    """`(key, url)` pairs from `body`'s `Jira: [PROJ-123](url)` footer(s),
    de-duplicated by key, order-preserving. Keys uppercased to canonical form so
    they key the same as `parse_jira_footers`. Empty when `body` is falsy or
    carries no footer link.
    """
    if not body:
        return []
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for key, url in JIRA_FOOTER_LINK_RE.findall(body):
        key = key.upper()
        if key not in seen:
            seen.add(key)
            out.append((key, url))
    return out


def _auth_header(email: str, token: str) -> str:
    """The HTTP Basic `Authorization` header value for `email:token`."""
    return "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()


def _creds(email: str | None, token: str | None) -> tuple[str, str] | None:
    """`(email, token)` with `token` defaulting to `$JIRA_API_TOKEN`, or None
    when either is missing — the "feature is off" short-circuit before any call.
    """
    token = token or os.environ.get(JIRA_API_TOKEN_ENV)
    if not email or not token:
        return None
    return email, token


def _request(
    method: str,
    url: str,
    *,
    email: str,
    token: str,
    payload: dict | None = None,
) -> dict | None:
    """Make one Jira REST call and return parsed JSON (or `{}` for an empty body,
    e.g. a 204 from a transition POST), or None on any failure.

    Mirrors `linear._post_graphql`'s degrade-never-raise contract: a network
    error, timeout, non-2xx, or unparsable body all collapse to None. The token
    authenticates in the `Authorization` header and is never logged.
    """
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": _auth_header(email, token),
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            text = resp.read().decode()
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    if not text.strip():
        return {}
    try:
        data_out: dict = json.loads(text)
    except ValueError:
        return None
    return data_out


def _base(site_url: str) -> str:
    """The REST base, trailing slash stripped (`config.jira_site_url` already
    strips it; this is the defensive belt so `…/atlassian.net/` can't yield a
    `//rest` 404)."""
    return site_url.rstrip("/")


def fetch_issue_statuses(
    keys: list[str], *, site_url: str, email: str, token: str | None = None
) -> dict[str, str | None]:
    """`{key: status_name_or_None}` for every key — drives the `devdone=` pill.

    One `GET /issue/{key}?fields=status` per distinct key; a single failure is
    isolated to its own key (stays None). Every input key appears in the result.
    None on unset creds/site. Never raises.

    ponytail: per-key, not a JQL batch — a PR delivers a handful of issues at
    most. Switch to `/search?jql=key in (…)` if a repo ever delivers dozens.
    """
    out: dict[str, str | None] = {k: None for k in keys}
    creds = _creds(email, token)
    if not creds or not site_url:
        return out
    em, tok = creds
    base = _base(site_url)
    for key in out:
        data = _request(
            "GET", f"{base}/rest/api/3/issue/{key}?fields=status", email=em, token=tok
        )
        status = (((data or {}).get("fields") or {}).get("status") or {}).get("name")
        out[key] = status or None
    return out


def fetch_issue_summaries(
    keys: list[str], *, site_url: str, email: str, token: str | None = None
) -> dict[str, str | None]:
    """`{key: summary_or_None}` for every key — the human title for the PR-cache
    enrichment. Same per-key `GET /issue/{key}?fields=summary` shape and error
    isolation as `fetch_issue_statuses`. None on unset creds/site. Never raises.
    """
    out: dict[str, str | None] = {k: None for k in keys}
    creds = _creds(email, token)
    if not creds or not site_url:
        return out
    em, tok = creds
    base = _base(site_url)
    for key in out:
        data = _request(
            "GET", f"{base}/rest/api/3/issue/{key}?fields=summary", email=em, token=tok
        )
        summary = ((data or {}).get("fields") or {}).get("summary")
        out[key] = summary or None
    return out


def fetch_myself(*, site_url: str, email: str, token: str | None = None) -> str | None:
    """The authenticated user's Jira `accountId`, or None — the "only transition
    my own issues" gate (the Jira analog of `linear.fetch_viewer_id`). None on
    unset creds/site or any API failure, so the caller transitions nothing
    (fail-safe: never touch an issue we can't confirm is ours).
    """
    creds = _creds(email, token)
    if not creds or not site_url:
        return None
    em, tok = creds
    data = _request("GET", f"{_base(site_url)}/rest/api/3/myself", email=em, token=tok)
    return (data or {}).get("accountId") or None


def fetch_issue_meta(
    key: str, *, site_url: str, email: str, token: str | None = None
) -> dict | None:
    """`{"status": <name>, "assignee_id": <accountId|None>}` for `key`, or None.

    `status` is the live status name (compared against the merge target);
    `assignee_id` drives the only-mine gate. None — never raises — on unset
    creds/site, a missing key, or any API failure.
    """
    creds = _creds(email, token)
    if not creds or not site_url or not key:
        return None
    em, tok = creds
    data = _request(
        "GET",
        f"{_base(site_url)}/rest/api/3/issue/{key}?fields=status,assignee",
        email=em,
        token=tok,
    )
    if not data:
        return None
    fields = data.get("fields") or {}
    return {
        "status": (fields.get("status") or {}).get("name"),
        "assignee_id": (fields.get("assignee") or {}).get("accountId"),
    }


def transition_issue(
    key: str,
    target_status: str,
    *,
    site_url: str,
    email: str,
    token: str | None = None,
) -> bool:
    """Move issue `key` to the workflow status named `target_status` (matched
    case-insensitively). The module's one *write*.

    Jira moves issues via *transitions*, not a direct status set: this GETs the
    issue's available transitions, picks the one whose target status name
    matches, then POSTs it. True iff the transition fired; False on unset
    creds/site, no matching transition available, or any API failure. Never
    raises. The caller (`cycle._transition_merged_jira`) owns the policy of
    *whether* to call this.
    """
    creds = _creds(email, token)
    if not creds or not site_url or not key or not target_status:
        return False
    em, tok = creds
    base = _base(site_url)
    data = _request(
        "GET", f"{base}/rest/api/3/issue/{key}/transitions", email=em, token=tok
    )
    target_cf = target_status.casefold()
    transition_id: str | None = None
    for tr in (data or {}).get("transitions") or []:
        to_name = (tr.get("to") or {}).get("name") or ""
        if to_name.casefold() == target_cf:
            transition_id = tr.get("id")
            break
    if not transition_id:
        return False
    res = _request(
        "POST",
        f"{base}/rest/api/3/issue/{key}/transitions",
        email=em,
        token=tok,
        payload={"transition": {"id": transition_id}},
    )
    return res is not None
