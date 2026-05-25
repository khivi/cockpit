"""Slack URL parsing.

Pure regex + URL-decoding. No network. The Slack thread body is fetched by
Claude itself via the Slack MCP on the first turn of a spawned workspace —
spawn.py only needs to recognise the URL shape and pull out `(channel, ts)`
to seed a deterministic branch slug + the MCP-instructing prompt.
"""

from __future__ import annotations

import re
import urllib.parse

SLACK_URL_RE = re.compile(
    r"^https?://[\w.-]+\.slack\.com/archives/([A-Z0-9]+)/p(\d+)(?:\?.*)?$"
)


def parse_url(url: str) -> tuple[str, str] | None:
    """`(channel, ts)` from a Slack archives URL, or `None` if it doesn't match.

    Slack archive URLs encode the timestamp as `p1234567890123456` (no dot);
    the API + canonical form uses `1234567890.123456`. This converts.

    `?thread_ts=…` in the query string (Slack-generated reply links keep the
    reply's ts in the path and the root ts in the query) overrides the path
    ts so the returned value always points at the thread root.
    """
    m = SLACK_URL_RE.match(url)
    if not m:
        return None
    channel = m.group(1)
    p_ts = m.group(2)
    if len(p_ts) < 7:
        return None
    ts = f"{p_ts[:-6]}.{p_ts[-6:]}"

    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if "thread_ts" in qs and qs["thread_ts"]:
        ts = qs["thread_ts"][0]
    return channel, ts
