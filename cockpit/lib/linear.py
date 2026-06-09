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
daemon can't reach the MCP. But the daemon *does* make direct GraphQL calls:

  * read-only — `fetch_ticket_state` (the `devdone=` pill), plus
    `fetch_viewer_id` / `fetch_ticket_meta` / `fetch_team_states` (the
    merge-transition eligibility checks);
  * the one *write* — `update_ticket_state`, the `issueUpdate` mutation that
    moves a ticket's workflow state. It is reached only by the opt-in
    `linear_done_on_merge` path in the slow tick (see
    `cycle._transition_merged_tickets`); the *policy* (which ticket, when,
    skip-if-already-done) lives there, this module just performs the call.

Every call is gated on the `LINEAR_API_KEY` env var and degrades to
None/False — never raises — on a missing key, timeout, or API error.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request

LINEAR_RE = re.compile(r"[A-Z]{2,6}-[0-9]+")
LINEAR_RE_CI = re.compile(r"[A-Za-z]{2,6}-[0-9]+")

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

# Filter by team key + issue number rather than the opaque UUID `issue(id:)`
# wants — we only have the human identifier (`PE-1234`) from the branch name.
_TICKET_STATE_QUERY = (
    "query($team:String!,$number:Float!){"
    "issues(filter:{team:{key:{eq:$team}},number:{eq:$number}}){"
    "nodes{identifier state{name}}}}"
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

# `claude mcp list` health-checks each server by connecting to it, not just
# dumping config. A managed connector (claude.ai) handshakes asynchronously —
# ~6s typically, 30s+ when several worktrees spawn at once. A 3s budget timed
# out before the Linear connector reported, so the pre-flight returned None
# (proceed-anyway) instead of a definitive True/False. 15s lets the typical
# handshake finish and yield a real answer while still capping a hung `claude`.
# A heavily-loaded connector that exceeds this still degrades safely: timeout →
# None → seeded prompt, whose in-session retry loop covers the late connect.
_MCP_LIST_TIMEOUT_SECONDS = 15


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
    seen: set[str] = set()
    out: list[str] = []
    for tid in LINEAR_FOOTER_RE.findall(body):
        tid = tid.upper()
        if tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


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


def linear_mcp_available() -> bool | None:
    """Return True/False if `claude mcp list` definitively says, else None.

    Runs `claude mcp list` with a bounded timeout. Returns:
      * True  — stdout contains a case-insensitive `linear` substring.
      * False — command ran cleanly with no Linear entry in stdout.
      * None  — the `claude` binary is missing, the command failed/timed out,
                or any other reason we couldn't tell. Callers treat None as
                "proceed with the smart flow anyway" (Claude itself will
                STOP on the first turn if the MCP is truly missing).

    No network — `claude mcp list` is a local config dump.
    """
    try:
        res = subprocess.run(
            ["claude", "mcp", "list"],
            capture_output=True,
            text=True,
            timeout=_MCP_LIST_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    return "linear" in res.stdout.lower()


def fetch_ticket_state(ticket_id: str, *, api_key: str | None = None) -> str | None:
    """Return the Linear workflow-state *name* for `ticket_id` (e.g. "Dev Done",
    "In Progress"), or None when it can't be determined.

    Returns None — never raises — when:
      * `LINEAR_API_KEY` is unset (and no `api_key` override given): the feature
        is simply off, so no network call is made.
      * `ticket_id` doesn't parse as a Linear id.
      * the GraphQL request fails, times out, or returns no matching issue.

    Callers treat None as "not in the dev-done state" → no pill. The raw key is
    sent in the `Authorization` header and never logged.
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

    body = json.dumps(
        {
            "query": _TICKET_STATE_QUERY,
            "variables": {"team": team.upper(), "number": number},
        }
    ).encode()
    req = urllib.request.Request(
        LINEAR_API_URL,
        data=body,
        headers={"Authorization": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TICKET_STATE_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None

    nodes = (((payload or {}).get("data") or {}).get("issues") or {}).get("nodes")
    if not nodes:
        return None
    state = (nodes[0].get("state") or {}).get("name")
    return state or None


def _post_graphql(query: str, variables: dict, *, api_key: str, timeout: float):
    """POST a GraphQL `query`/`variables` to Linear; return the `data` dict or
    None on any failure. Never raises. The raw key authenticates in the
    `Authorization` header (no `Bearer` prefix) and is never logged. Shared by
    the merge-transition helpers below; `fetch_ticket_state` predates it and
    keeps its own inlined request.
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
