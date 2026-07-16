"""Trello ticket helpers — the `tickets: trello` provider.

The Trello analog of `lib.jira`. Same contract: every call degrades to
None/False/empty — never raises — on missing creds, a timeout, or an API error,
so a flaky Trello can't stall the reconcile.

Trello has no global "status": a card lives in a *list* (a board column). So the
`devdone=` / merge-done comparison keys off the card's current **list name**
(casefold-matched against the configured `dev_done_list` / `merge_done_list`) —
the exact same shape as Jira's status-name match, just a different noun. Neither
field has a default (Trello boards name their lists arbitrarily — "Doing",
"Review", "Done", …), so an unset field means that feature is simply inert.

Like Linear/Jira, the card *body* (name, description, comments) is read by Claude
via the official Trello MCP on a spawned worktree's first turn — the daemon can't
reach an MCP. But the daemon *does* make direct REST calls (Trello REST API v1):

  * read-only — `fetch_card_lists` (the `devdone=` pill), plus `fetch_myself`
    / `fetch_card_meta` (the merge-move eligibility checks);
  * the one *write* — `move_card`, reached only by the opt-in `close_on_merge`
    path in the slow tick (`cycle._transition_merged_trello`); the *policy*
    (which card, when) lives there, this module just performs the call.

Auth is Trello's classic key+token, passed as query params: both come from the
env (`TRELLO_API_KEY` + `TRELLO_API_TOKEN`). This is separate from the OAuth the
MCP uses — the daemon is headless, so it uses the REST credential pair. Neither
value is ever logged.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

TRELLO_API_KEY_ENV = "TRELLO_API_KEY"
TRELLO_API_TOKEN_ENV = "TRELLO_API_TOKEN"

# The Trello-specific fields the `tickets` config block accepts, as `(name,
# kind)` (kind resolved to a validator in `tickets.py`). No credential fields —
# key+token are env-only (see the module docstring). Neither list field has a
# default: an unset `dev_done_list` leaves the pill off, an unset
# `merge_done_list` leaves the merge-move off (Trello list names are arbitrary,
# so there's nothing safe to guess). Keep in sync with the readers in
# `config.py` (`trello_dev_done_list`, `trello_merge_done_list`).
CONFIG_FIELDS: tuple[tuple[str, str], ...] = (
    ("dev_done_list", "str"),
    ("merge_done_list", "str"),
)

# A Trello card short link — the `[A-Za-z0-9]+` id in `trello.com/c/<shortLink>`.
# Case-sensitive (unlike a Jira key), so it's never upper/lower-cased.
_SHORT = r"[A-Za-z0-9]+"

# A card URL, anchored so a trailing `/<num>-<slug>`, query, or fragment is
# tolerated. Used both for spawn-source detection and to seed the codename branch.
TRELLO_CARD_URL_RE = re.compile(rf"https?://trello\.com/c/({_SHORT})", re.IGNORECASE)

# A PR *delivers* a Trello card only via the explicit `Trello: [title](url)`
# footer (mirrors Linear/Jira's strict, line-anchored delivery footer) — NOT a
# bare mention, which would catch predecessor / follow-up cards the PR doesn't
# deliver. The captured id is the card short link out of the URL (the bracket
# text is just the human title).
TRELLO_FOOTER_RE = re.compile(
    rf"^Trello:\s*\[[^\]]*\]\(\s*(https?://trello\.com/c/{_SHORT}[^)\s]*)\s*\)",
    re.MULTILINE | re.IGNORECASE,
)

# Bound each REST call so a hung Trello can't stall the slow tick.
_TIMEOUT_SECONDS = 10
_API_BASE = "https://api.trello.com/1"


def card_short_link(url: str) -> str | None:
    """The card short link in `url` (`trello.com/c/<shortLink>`), or None."""
    m = TRELLO_CARD_URL_RE.search(url)
    return m.group(1) if m else None


def trello_seed(url: str) -> str:
    """The stable identity of a Trello card URL, for the codename branch seed —
    the card short link (lowercased for a deterministic seed), invariant to the
    optional `/<num>-<slug>` tail, query, or fragment. Falls back to the
    query/fragment-stripped URL when no short link is found (defensive —
    `detect_source` only routes recognized URLs here)."""
    sl = card_short_link(url)
    if sl:
        return sl.lower()
    return url.split("?", 1)[0].split("#", 1)[0]


def parse_trello_footers(body: str) -> list[str]:
    """The de-duplicated, order-preserving card short links declared in `body`'s
    `Trello: [title](url)` footer line(s) — the strict set the PR delivers. Short
    links keep their original case (Trello ids are case-sensitive). Empty when
    `body` is falsy or has no footer — the Trello analog of
    `linear.parse_linear_footers`.
    """
    # `parse_trello_footer_links` already de-dups by short link, order-preserving.
    return [sl for sl, _url in parse_trello_footer_links(body)]


def parse_trello_footer_links(body: str) -> list[tuple[str, str]]:
    """`(short_link, url)` pairs from `body`'s `Trello: [title](url)` footer(s),
    de-duplicated by short link, order-preserving. Empty when `body` is falsy or
    carries no footer link.
    """
    if not body:
        return []
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for url in TRELLO_FOOTER_RE.findall(body):
        sl = card_short_link(url)
        if sl and sl not in seen:
            seen.add(sl)
            out.append((sl, url))
    return out


def _creds(key: str | None = None, token: str | None = None) -> tuple[str, str] | None:
    """`(key, token)` defaulting to `$TRELLO_API_KEY` / `$TRELLO_API_TOKEN`, or
    None when either is missing — the "feature is off" short-circuit before any
    call."""
    key = key or os.environ.get(TRELLO_API_KEY_ENV)
    token = token or os.environ.get(TRELLO_API_TOKEN_ENV)
    if not key or not token:
        return None
    return key, token


def _request(
    method: str,
    path: str,
    *,
    key: str,
    token: str,
    params: dict[str, str] | None = None,
) -> object | None:
    """Make one Trello REST call and return parsed JSON (or `{}` for an empty
    body), or None on any failure.

    Auth is the `key`+`token` query pair appended to `path`'s params. Mirrors
    `jira._request`'s degrade-never-raise contract: a network error, timeout,
    non-2xx, or unparsable body all collapse to None. The credentials are query
    params (Trello's scheme) and are never logged.
    """
    q = dict(params or {})
    q["key"] = key
    q["token"] = token
    url = f"{_API_BASE}{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json"}, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            text = resp.read().decode()
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    if not text.strip():
        return {}
    try:
        parsed: object = json.loads(text)  # dict or list, per endpoint
    except ValueError:
        return None
    return parsed


def fetch_card_lists(
    short_links: list[str], *, key: str | None = None, token: str | None = None
) -> dict[str, str | None]:
    """`{short_link: list_name_or_None}` for every card — drives the `devdone=`
    pill.

    One `GET /cards/{id}?list=true` per distinct card; a single failure is
    isolated to its own id (stays None). Every input id appears in the result.
    None on unset creds. Never raises.

    ponytail: per-card, not a batched `/batch` call — a PR delivers a handful of
    cards at most. Switch to `/batch?urls=…` if a repo ever delivers dozens.
    """
    out: dict[str, str | None] = {sl: None for sl in short_links}
    creds = _creds(key, token)
    if not creds:
        return out
    k, tok = creds
    for sl in out:
        data = _request(
            "GET",
            f"/cards/{sl}",
            key=k,
            token=tok,
            params={"fields": "id", "list": "true"},
        )
        if isinstance(data, dict):
            out[sl] = ((data.get("list") or {}).get("name")) or None
    return out


def fetch_card_names(
    short_links: list[str], *, key: str | None = None, token: str | None = None
) -> dict[str, str | None]:
    """`{short_link: card_name_or_None}` for every card — the human title for the
    PR-cache enrichment. Same per-card `GET /cards/{id}?fields=name` shape and
    error isolation as `fetch_card_lists`. None on unset creds. Never raises.
    """
    out: dict[str, str | None] = {sl: None for sl in short_links}
    creds = _creds(key, token)
    if not creds:
        return out
    k, tok = creds
    for sl in out:
        data = _request(
            "GET", f"/cards/{sl}", key=k, token=tok, params={"fields": "name"}
        )
        if isinstance(data, dict):
            out[sl] = data.get("name") or None
    return out


def fetch_myself(*, key: str | None = None, token: str | None = None) -> str | None:
    """The authenticated member's Trello id, or None — the "only move my own
    cards" gate (the Trello analog of `jira.fetch_myself`). None on unset creds
    or any API failure, so the caller moves nothing (fail-safe: never touch a
    card we can't confirm is ours)."""
    creds = _creds(key, token)
    if not creds:
        return None
    k, tok = creds
    data = _request("GET", "/members/me", key=k, token=tok, params={"fields": "id"})
    if isinstance(data, dict):
        return data.get("id") or None
    return None


def fetch_card_meta(
    short_link: str, *, key: str | None = None, token: str | None = None
) -> dict | None:
    """`{"list": <name>, "board": <idBoard>, "members": [<memberId>…]}` for the
    card, or None.

    `list` is the current list name (compared against the merge target); `board`
    locates the target list; `members` drives the only-mine gate. None — never
    raises — on unset creds, a missing card, or any API failure.
    """
    creds = _creds(key, token)
    if not creds or not short_link:
        return None
    k, tok = creds
    data = _request(
        "GET",
        f"/cards/{short_link}",
        key=k,
        token=tok,
        params={"fields": "idBoard,idMembers", "list": "true"},
    )
    if not isinstance(data, dict) or not data:
        return None
    return {
        "list": (data.get("list") or {}).get("name"),
        "board": data.get("idBoard"),
        "members": list(data.get("idMembers") or []),
    }


def move_card(
    short_link: str,
    target_list: str,
    *,
    key: str | None = None,
    token: str | None = None,
) -> bool:
    """Move card `short_link` to the list named `target_list` (matched
    case-insensitively) on its own board. The module's one *write*.

    Trello moves a card by setting its `idList`: this resolves the card's board,
    GETs that board's lists, picks the one whose name matches, then PUTs the new
    `idList`. True iff the move fired; False on unset creds, an unknown board /
    no matching list, or any API failure. Never raises. The caller
    (`cycle._transition_merged_trello`) owns the policy of *whether* to call this.
    """
    creds = _creds(key, token)
    if not creds or not short_link or not target_list:
        return False
    k, tok = creds
    card = _request(
        "GET", f"/cards/{short_link}", key=k, token=tok, params={"fields": "idBoard"}
    )
    board = card.get("idBoard") if isinstance(card, dict) else None
    if not board:
        return False
    lists = _request(
        "GET", f"/boards/{board}/lists", key=k, token=tok, params={"fields": "name"}
    )
    target_cf = target_list.casefold()
    target_id: str | None = None
    for lst in lists if isinstance(lists, list) else []:
        if (lst.get("name") or "").casefold() == target_cf:
            target_id = lst.get("id")
            break
    if not target_id:
        return False
    res = _request(
        "PUT", f"/cards/{short_link}", key=k, token=tok, params={"idList": target_id}
    )
    return res is not None
