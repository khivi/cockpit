"""Linear ticket helpers.

Two regex surfaces, both pure:

  * `LINEAR_RE` — finds a Linear ticket id *inside* a string (branch name,
    typically). Uppercase-only. Used by the statusline pill.
  * `LINEAR_RE_CI` — case-insensitive *fullmatch* regex for classifying a
    raw positional argument as a Linear id. Used by `spawn.detect_source`.

Both accept any 2–6 letter prefix joined to digits by `-` (`PE-1234`,
`ENG-4012`). The upper bound on prefix length is the main guard against
unrelated ids (`HTTP-200`, `UTF-8`).

The Linear ticket *body* (title, description) is still fetched by Claude
itself via the Linear MCP on the first turn of a spawned workspace — the
daemon can't reach the MCP. There is deliberately **no** `claude mcp list`
pre-flight before seeding that prompt (see `cockpit.lib.slack`, which states
the same rule): the probe health-checks each server by connecting to it, and a
claude.ai-managed connector handshakes asynchronously, so it reports the Linear
entry missing while it is live. A `False` from it silently downgraded the spawn
to a plain branch — the exact false-negative the Slack/Jira/Trello/GitHub paths
were all written to avoid. `prompts/linear.txt` carries the same retry-then-STOP
step as `prompts/jira.txt`, which handles a genuinely absent connector
in-session. But the daemon *does* make direct GraphQL calls:

  * read-only — `fetch_ticket_states` (the `devdone=` pill),
    `fetch_ticket_project` (the ticket→repo routing tiebreaker), plus
    `fetch_viewer_id` / `fetch_ticket_meta` /
    `fetch_team_states` (the merge-transition eligibility checks);
  * the one *write* — `update_ticket_state`, the `issueUpdate` mutation that
    moves a ticket's workflow state. It is reached only by the opt-in
    `linear_done_on_merge` path in the slow tick (see
    `cycle._transition_merged_tickets`); the *policy* (which ticket, when,
    skip-if-already-done) lives there, this module just performs the call.

Every call takes an explicit `api_key` (the caller resolves it per repo via
`config.linear_api_key` — the env var named by `tickets.api_key_env`, default
`LINEAR_API_KEY`, which is also the back-compat fallback here) and degrades to
None/False — never raises — on a missing key, timeout, or API error.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

LINEAR_RE = re.compile(r"[A-Z]{2,6}-[0-9]+")
LINEAR_RE_CI = re.compile(r"[A-Za-z]{2,6}-[0-9]+")

# The "Copy link" URL for an issue — what you actually have in the clipboard
# when you reach for `cockpit new` / the TUI's `n`, and the shape a bare-id
# match can't classify (it falls through to `branch`, where git rejects the URL
# as a branch name). The identifier is positional here (the segment after
# `/issue/`), so unlike `LINEAR_RE_CI` this needs no 2–6 letter guard against
# unrelated ids — there is no ambiguity with a branch name to defend against.
LINEAR_ISSUE_URL_RE = re.compile(
    r"https?://linear\.app/[^/]+/issue/([A-Za-z0-9]+-[0-9]+)", re.IGNORECASE
)

# The Linear-specific fields the `tickets` config block accepts, as
# `(name, kind)` where kind ∈ {"str", "str_list", "bool"} (resolved to a
# validator in `tickets.py`). The provider owns its own config surface; this is
# the *specification* that drives preflight validation (common fields like
# `provider`/`close_on_merge` are added by `tickets.py`). Keep in sync with the
# Linear readers in `config.py` (`linear_team_keys`, `linear_dev_done_state`,
# `linear_merge_done_state`).
# `api_key_env` names the env var the key is read from (default
# `LINEAR_API_KEY`) — never the key itself. That indirection is what lets two
# orgs on separate Linear workspaces each carry their own credential.
# `project` names the Linear *project* this repo's work lives in — the
# tiebreaker for the many-repos-one-team shape, where every repo declares the
# same `keys` and a bare `ENG-1234` can't say which repo it belongs to. It is
# routing-only (nothing else reads it), and the project is *not* in the
# identifier, so resolving it costs a fetch — which is why callers narrow only
# when the free `keys` match is already ambiguous.
CONFIG_FIELDS: tuple[tuple[str, str], ...] = (
    ("keys", "str_list"),
    ("project", "str"),
    ("dev_done_state", "str"),
    ("merge_done_state", "str"),
    ("api_key_env", "str"),
)

# A PR *delivers* a ticket only via the explicit `Linear: [PE-1234](url)` footer
# that `start-linear-ticket` / the morning-align cross-link step append to the PR
# body — NOT via the branch-slug regex above (which catches predecessor /
# follow-up / "reapply X" mentions the PR doesn't actually deliver). This mirrors
# the strict delivery signal in the morning-align `linear_delivery.py` helper.
# Anchored to line start so a mention buried in prose isn't a footer. Matched
# case-insensitively (the `Linear:` label and the id can be any case — branch
# slugs lowercase the id); `parse_linear_footers` uppercases captures to the
# canonical `PE-1234` form so display, dedup, and GraphQL lookups stay stable.
LINEAR_FOOTER_RE = re.compile(
    r"^Linear:\s*\[([A-Za-z]+-[0-9]+)\]", re.MULTILINE | re.IGNORECASE
)
# Same footer, capturing the markdown link target so callers can open the exact
# Linear URL (never hand-construct one — the workspace slug isn't known here).
LINEAR_FOOTER_LINK_RE = re.compile(
    r"^Linear:\s*\[([A-Za-z]+-[0-9]+)\]\((\S+?)\)", re.MULTILINE | re.IGNORECASE
)

# Linear's public GraphQL endpoint. A *personal API key* authenticates with the
# raw key in the `Authorization` header (no `Bearer` prefix — that form is for
# OAuth access tokens). The daemon never logs the key.
LINEAR_API_URL = "https://api.linear.app/graphql"
LINEAR_API_KEY_ENV = "LINEAR_API_KEY"

# One slow-tick fetch per gated PR. A bounded budget keeps a hung Linear from
# stalling the reconcile; a timeout degrades to None (pill stays off) like any
# other failure.
_TICKET_STATE_TIMEOUT_SECONDS = 10

# One query per team: a whole team's worth of ticket numbers in one
# round-trip (`number:{in:[…]}` instead of `{eq:…}`). The slow tick collects
# every ticket due for a state refresh across all of a repo's open PRs and
# resolves them with one query per team rather than one per ticket.
_TICKET_STATES_BATCH_QUERY = (
    "query($team:String!,$numbers:[Float!]!){"
    "issues(filter:{team:{key:{eq:$team}},number:{in:$numbers}}){"
    "nodes{identifier state{name}}}}"
)

# Same team-batched shape, but pulling each ticket's human `title` — the
# cache-enrichment field a statusline consumer (cship) reads to show the ticket
# name next to its id, so it never needs its own Linear round-trip.
_TICKET_TITLES_BATCH_QUERY = (
    "query($team:String!,$numbers:[Float!]!){"
    "issues(filter:{team:{key:{eq:$team}},number:{in:$numbers}}){"
    "nodes{identifier title}}}"
)

# Same team-key + number filter, but pulling the extra fields the
# merge-transition path needs: the opaque issue `id` (UUID — what `issueUpdate`
# wants), the state `type` (so a *canceled* ticket is never resurrected — note
# "Dev Done"/"In QA"/"Done" all share `type: completed`, so type alone can't
# tell "already final"), the `assignee` id (gate: only move my own tickets),
# and the `team` id (to resolve the target state's UUID for that team).
_TICKET_META_QUERY = (
    "query($team:String!,$number:Float!){"
    "issues(filter:{team:{key:{eq:$team}},number:{eq:$number}}){"
    "nodes{id identifier state{name type} assignee{id} team{id}}}}"
)

# The ticket's Linear *project* — the routing tiebreaker (see `CONFIG_FIELDS`).
# Same team-key + number filter as `_TICKET_META_QUERY`, pulling only the project
# name. `Issue.project` is nullable: an issue filed outside any project resolves
# to None, which narrows nothing.
_TICKET_PROJECT_QUERY = (
    "query($team:String!,$number:Float!){"
    "issues(filter:{team:{key:{eq:$team}},number:{eq:$number}}){"
    "nodes{project{name}}}}"
)

# The API key's own user ("me") — the gate for "only transition tickets
# assigned to me". A personal key authenticates as its owner, so `viewer` is
# exactly the configured user without any extra identity config.
_VIEWER_QUERY = "query{viewer{id}}"

# A team's workflow states (name → UUID). `issueUpdate` needs the state UUID,
# not its display name, so the merge-transition path resolves the target name
# through this once per team.
_TEAM_STATES_QUERY = "query($id:String!){team(id:$id){states{nodes{id name}}}}"

# The one mutation in this module: move a ticket to a workflow state by UUID.
_ISSUE_UPDATE_MUTATION = (
    "mutation($id:String!,$stateId:String!){"
    "issueUpdate(id:$id,input:{stateId:$stateId}){success}}"
)


def extract_ticket(branch: str) -> str:
    """Return the first Linear ticket id in `branch` (uppercased), or "" if none.

    Branch-slug heuristic — fine for the statusline footer's id pill, but NOT a
    *delivery* signal. Use `parse_linear_footers` for "which tickets does this PR
    deliver".
    """
    if not branch:
        return ""
    m = LINEAR_RE.search(branch.upper())
    return m.group(0) if m else ""


def parse_linear_footers(body: str) -> list[str]:
    """Return the de-duplicated, order-preserving list of ticket ids declared in
    `body`'s `Linear: [PE-1234](url)` footer line(s) — the strict set of tickets
    the PR delivers. Ids are uppercased to the canonical `PE-1234` form (the
    footer match is case-insensitive). Empty when `body` is falsy or has no footer.
    """
    if not body:
        return []
    return list(dict.fromkeys(tid.upper() for tid in LINEAR_FOOTER_RE.findall(body)))


def parse_linear_footer_links(body: str) -> list[tuple[str, str]]:
    """`(ticket_id, url)` pairs from `body`'s `Linear: [PE-1234](url)` footer(s),
    de-duplicated by id, order-preserving. Ids are uppercased to the canonical
    `PE-1234` form (the footer match is case-insensitive) so they key the same as
    `parse_linear_footers` output. Empty when `body` is falsy or carries no footer
    link. Use this to open the canonical Linear URL rather than constructing one
    from the id (the workspace slug isn't known here)."""
    if not body:
        return []
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for tid, url in LINEAR_FOOTER_LINK_RE.findall(body):
        tid = tid.upper()
        if tid not in seen:
            seen.add(tid)
            out.append((tid, url))
    return out


def _post_graphql(query: str, variables: dict, *, api_key: str, timeout: float):
    """POST a GraphQL `query`/`variables` to Linear; return the `data` dict or
    None on any failure. Never raises. The raw key authenticates in the
    `Authorization` header (no `Bearer` prefix) and is never logged. Shared by
    every read/write helper below.
    """
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        LINEAR_API_URL,
        data=body,
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    return (payload or {}).get("data")


def fetch_ticket_states(
    ticket_ids: list[str], *, api_key: str | None = None
) -> dict[str, str | None]:
    """Return a `{ticket_id: state_name_or_None}` map covering every id in
    `ticket_ids`.

    Ids are grouped by team key and each team is resolved in a single
    `number:{in:[…]}` query, so a repo's whole crop of due tickets costs one
    round-trip per team instead of one per ticket. Matching back to the input is
    case-insensitive (the canonical `identifier` Linear returns is uppercase).

    Every input id appears in the result. An id maps to None when its state can't
    be determined — unset key, unparsable id, no matching issue, or that team's
    query failing — and a failure is isolated to its own team (other teams keep
    their fetched states). Never raises.
    """
    out: dict[str, str | None] = {tid: None for tid in ticket_ids}
    key = api_key or os.environ.get(LINEAR_API_KEY_ENV)
    if not key:
        return out

    # team key (uppercased) → set of issue numbers; plus a case-folded id lookup
    # to map each returned `identifier` back to the caller's original id. An
    # unparsable id is never grouped, so it simply stays None.
    by_team: dict[str, set[float]] = {}
    id_by_upper: dict[str, str] = {}
    for tid in ticket_ids:
        if not LINEAR_RE_CI.fullmatch(tid or ""):
            continue
        team, _, num = tid.partition("-")
        try:
            number = float(int(num))
        except ValueError:
            continue
        by_team.setdefault(team.upper(), set()).add(number)
        id_by_upper[tid.upper()] = tid

    for team, numbers in by_team.items():
        data = _post_graphql(
            _TICKET_STATES_BATCH_QUERY,
            {"team": team, "numbers": sorted(numbers)},
            api_key=key,
            timeout=_TICKET_STATE_TIMEOUT_SECONDS,
        )
        nodes = ((data or {}).get("issues") or {}).get("nodes")
        if not nodes:
            continue  # team query failed / no matches → its ids stay None
        for node in nodes:
            orig = id_by_upper.get((node.get("identifier") or "").upper())
            if orig is not None:
                out[orig] = (node.get("state") or {}).get("name") or None
    return out


def fetch_ticket_titles(
    ticket_ids: list[str], *, api_key: str | None = None
) -> dict[str, str | None]:
    """Return a `{ticket_id: title_or_None}` map covering every id in
    `ticket_ids`.

    Same team-batched round-trip and error isolation as `fetch_ticket_states`
    (one query per team, every input id present, None on any failure) — this one
    pulls the ticket's human title for the PR-cache enrichment rather than its
    workflow state. Never raises.
    """
    out: dict[str, str | None] = {tid: None for tid in ticket_ids}
    key = api_key or os.environ.get(LINEAR_API_KEY_ENV)
    if not key:
        return out

    by_team: dict[str, set[float]] = {}
    id_by_upper: dict[str, str] = {}
    for tid in ticket_ids:
        if not LINEAR_RE_CI.fullmatch(tid or ""):
            continue
        team, _, num = tid.partition("-")
        try:
            number = float(int(num))
        except ValueError:
            continue
        by_team.setdefault(team.upper(), set()).add(number)
        id_by_upper[tid.upper()] = tid

    for team, numbers in by_team.items():
        data = _post_graphql(
            _TICKET_TITLES_BATCH_QUERY,
            {"team": team, "numbers": sorted(numbers)},
            api_key=key,
            timeout=_TICKET_STATE_TIMEOUT_SECONDS,
        )
        nodes = ((data or {}).get("issues") or {}).get("nodes")
        if not nodes:
            continue
        for node in nodes:
            orig = id_by_upper.get((node.get("identifier") or "").upper())
            if orig is not None:
                out[orig] = node.get("title") or None
    return out


def fetch_ticket_project(ticket_id: str, *, api_key: str | None = None) -> str | None:
    """Return the name of the Linear project `ticket_id` belongs to, or None.

    The routing tiebreaker: an identifier names its *team* (`ENG-1234`) but never
    its project, so a repo declaring `tickets.project` can only be matched after
    this fetch. Callers pay it lazily — only when the free team-key match already
    returned more than one repo.

    None — never raises — on a missing key, an unparsable id, an issue filed
    outside any project (`Issue.project` is nullable), or any API failure. Every
    None case simply leaves the caller's candidate set un-narrowed.
    """
    key = api_key or os.environ.get(LINEAR_API_KEY_ENV)
    if not key:
        return None
    if not LINEAR_RE_CI.fullmatch(ticket_id or ""):
        return None
    team, _, num = ticket_id.partition("-")
    try:
        number = float(int(num))
    except ValueError:
        return None
    data = _post_graphql(
        _TICKET_PROJECT_QUERY,
        {"team": team.upper(), "number": number},
        api_key=key,
        timeout=_TICKET_STATE_TIMEOUT_SECONDS,
    )
    nodes = ((data or {}).get("issues") or {}).get("nodes")
    if not nodes:
        return None
    return ((nodes[0].get("project") or {}).get("name")) or None


def fetch_viewer_id(*, api_key: str | None = None) -> str | None:
    """Return the Linear user id of the API key's owner ("me"), or None.

    The gate for "only transition tickets assigned to me": a personal API key
    authenticates as its owner, so `viewer` is exactly the configured user with
    no extra identity config. None when the key is unset or the query fails —
    the caller then transitions nothing (fail-safe: never touch a ticket we
    can't confirm is ours).
    """
    key = api_key or os.environ.get(LINEAR_API_KEY_ENV)
    if not key:
        return None
    data = _post_graphql(
        _VIEWER_QUERY, {}, api_key=key, timeout=_TICKET_STATE_TIMEOUT_SECONDS
    )
    return ((data or {}).get("viewer") or {}).get("id") or None


def fetch_ticket_meta(ticket_id: str, *, api_key: str | None = None) -> dict | None:
    """Return the merge-transition metadata for `ticket_id`, or None.

    `{"id": <uuid>, "state": <name>, "type": <state-type>, "assignee_id":
    <uuid|None>, "team_id": <uuid>}`. The UUID `id` is what `issueUpdate` wants;
    `type` distinguishes a *canceled* ticket (never resurrect) from a merely
    `completed`-typed source column like "Dev Done"; `assignee_id` and `team_id`
    drive the only-mine gate and the target-state resolution.

    None — never raises — on missing key, an unparsable id, or any API failure.
    """
    key = api_key or os.environ.get(LINEAR_API_KEY_ENV)
    if not key:
        return None
    if not LINEAR_RE_CI.fullmatch(ticket_id or ""):
        return None
    team, _, num = ticket_id.partition("-")
    try:
        number = float(int(num))
    except ValueError:
        return None
    data = _post_graphql(
        _TICKET_META_QUERY,
        {"team": team.upper(), "number": number},
        api_key=key,
        timeout=_TICKET_STATE_TIMEOUT_SECONDS,
    )
    nodes = ((data or {}).get("issues") or {}).get("nodes")
    if not nodes:
        return None
    node = nodes[0]
    state = node.get("state") or {}
    return {
        "id": node.get("id"),
        "state": state.get("name"),
        "type": state.get("type"),
        "assignee_id": (node.get("assignee") or {}).get("id"),
        "team_id": (node.get("team") or {}).get("id"),
    }


def fetch_team_states(team_id: str, *, api_key: str | None = None) -> dict | None:
    """Return a `{state-name-casefolded: state-uuid}` map for `team_id`, or None.

    `issueUpdate` takes a state UUID, not its display name, so the
    merge-transition path resolves the configured target name through this map.
    Casefolded keys mirror the case-insensitive matching the dev-done pill uses.
    None on missing key or API failure.
    """
    key = api_key or os.environ.get(LINEAR_API_KEY_ENV)
    if not key or not team_id:
        return None
    data = _post_graphql(
        _TEAM_STATES_QUERY,
        {"id": team_id},
        api_key=key,
        timeout=_TICKET_STATE_TIMEOUT_SECONDS,
    )
    nodes = (((data or {}).get("team") or {}).get("states") or {}).get("nodes")
    if nodes is None:
        return None
    out: dict[str, str] = {}
    for n in nodes:
        name = n.get("name")
        sid = n.get("id")
        if name and sid:
            out[name.casefold()] = sid
    return out


def update_ticket_state(
    issue_uuid: str, state_id: str, *, api_key: str | None = None
) -> bool:
    """Move the issue `issue_uuid` to workflow state `state_id` (both UUIDs).

    The module's one *write*. Returns True iff the `issueUpdate` mutation
    reported `success`; False on missing key, missing args, or any API failure.
    Never raises. Callers (cycle._transition_merged_tickets) own the policy of
    *whether* to call this — this just performs the mutation.
    """
    key = api_key or os.environ.get(LINEAR_API_KEY_ENV)
    if not key or not issue_uuid or not state_id:
        return False
    data = _post_graphql(
        _ISSUE_UPDATE_MUTATION,
        {"id": issue_uuid, "stateId": state_id},
        api_key=key,
        timeout=_TICKET_STATE_TIMEOUT_SECONDS,
    )
    return bool(((data or {}).get("issueUpdate") or {}).get("success"))
