"""Linear ticket helpers.

Two surfaces, both kept narrow:

  * `extract_ticket` / `LINEAR_RE` — branch-name heuristic, no network. Accepts
    any 2–6 uppercase prefix joined to digits by `-` (e.g. `PRO-123`,
    `ENG-4012`). Permissive enough catches unrelated ids (`HTTP-200`,
    `UTF-8`); the prefix-length cap is the main guard.
  * `resolve_issue` — single Linear GraphQL call using `LINEAR_API_KEY`.
    Strictly fail-soft: returns `None` (with a one-line stderr warning) on
    missing env var, network/HTTP error, or malformed/empty response.
    Never raises. Callers degrade to plain branch mode on `None`.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

LINEAR_RE = re.compile(r"[A-Z]{2,6}-[0-9]+")
LINEAR_RE_CI = re.compile(r"[A-Za-z]{2,6}-[0-9]+")

_LINEAR_API_URL = "https://api.linear.app/graphql"
_HTTP_TIMEOUT_S = 10


@dataclass(frozen=True)
class ResolvedIssue:
    """A Linear issue resolved via the GraphQL API."""

    identifier: str  # e.g. "PE-1234"
    title: str
    description: str  # may be empty
    url: str  # may be empty when the API omits it
    branch_name: str  # Linear's suggested branch slug, or "" if absent


def extract_ticket(branch: str) -> str:
    """Return the first uppercase Linear ticket id in `branch`, or "" if none."""
    if not branch:
        return ""
    m = LINEAR_RE.search(branch)
    return m.group(0) if m else ""


def _warn(msg: str) -> None:
    print(f"cockpit: linear: {msg}", file=sys.stderr)


def resolve_issue(identifier: str) -> ResolvedIssue | None:
    """Fetch issue title/description/branchName via Linear GraphQL.

    Returns `None` on:
      - missing `LINEAR_API_KEY`
      - HTTP/network/timeout error
      - GraphQL error or `issue == null`
      - malformed JSON

    Emits a single stderr warning per failure mode. Never raises.
    """
    api_key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not api_key:
        _warn(
            "LINEAR_API_KEY not set; " f"treating {identifier!r} as a plain branch name"
        )
        return None

    query = (
        "query IssueByIdentifier($id: String!) {"
        "  issue(id: $id) {"
        "    identifier title description url branchName"
        "  }"
        "}"
    )
    body = json.dumps({"query": query, "variables": {"id": identifier}}).encode()
    req = urllib.request.Request(
        _LINEAR_API_URL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            raw = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _warn(f"lookup failed for {identifier!r}: {exc}")
        return None

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        _warn(f"malformed response for {identifier!r}: {exc}")
        return None

    errors = payload.get("errors")
    if errors:
        first = errors[0].get("message", str(errors[0])) if errors else "unknown"
        _warn(f"GraphQL error for {identifier!r}: {first}")
        return None

    issue = (payload.get("data") or {}).get("issue")
    if not issue:
        _warn(f"no issue {identifier!r}")
        return None

    return ResolvedIssue(
        identifier=issue.get("identifier") or identifier,
        title=issue.get("title") or "",
        description=issue.get("description") or "",
        url=issue.get("url") or "",
        branch_name=issue.get("branchName") or "",
    )
