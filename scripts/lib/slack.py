"""Slack thread helpers.

Two surfaces:

  * `parse_url` — extracts `(channel, ts)` from a Slack archive URL of the form
    `https://<ws>.slack.com/archives/<channel>/p<ts>[?thread_ts=…]`. Returns
    `None` if the URL doesn't match. No network.
  * `resolve_thread` — single `conversations.replies` call via `urllib` using
    `SLACK_TOKEN`. Fail-soft: returns `None` (with a one-line stderr warning)
    on missing env, HTTP/network error, or `ok != true`. Never raises.
    Callers degrade to a branch-from-URL slug on `None`.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

SLACK_URL_RE = re.compile(
    r"^https?://[\w.-]+\.slack\.com/archives/([A-Z0-9]+)/p(\d+)(?:\?.*)?$"
)
_SLACK_API_URL = "https://slack.com/api/conversations.replies"
_HTTP_TIMEOUT_S = 10


@dataclass(frozen=True)
class ResolvedThread:
    """First-message snapshot of a Slack thread."""

    channel: str
    ts: str  # canonical "1234567890.123456"
    text: str  # may be empty if first message has no text (file-only post, etc.)
    permalink: str  # echoed back from the input URL (Slack doesn't return one here)
    reply_count: int


def parse_url(url: str) -> tuple[str, str] | None:
    """`(channel, ts)` from a Slack archives URL, or `None` if it doesn't match.

    Slack archive URLs encode the timestamp as `p1234567890123456` (no dot);
    the API expects `1234567890.123456`. This converts.
    """
    m = SLACK_URL_RE.match(url)
    if not m:
        return None
    channel = m.group(1)
    p_ts = m.group(2)
    if len(p_ts) < 7:
        return None
    ts = f"{p_ts[:-6]}.{p_ts[-6:]}"

    # `?thread_ts=…` in the query string overrides the path ts (some Slack
    # links to thread replies preserve the reply's ts in the path and put the
    # root ts in the query). Use the root for resolution.
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if "thread_ts" in qs and qs["thread_ts"]:
        ts = qs["thread_ts"][0]
    return channel, ts


def _warn(msg: str) -> None:
    print(f"cockpit: slack: {msg}", file=sys.stderr)


def resolve_thread(url: str) -> ResolvedThread | None:
    """Fetch the first message of the thread at `url` via Slack Web API.

    Returns `None` on:
      - URL doesn't parse
      - missing `SLACK_TOKEN`
      - HTTP/network/timeout error
      - `ok != true` or empty `messages`
      - malformed JSON

    Emits a single stderr warning per failure mode. Never raises.
    """
    parsed = parse_url(url)
    if parsed is None:
        _warn(f"unparsable URL {url!r}")
        return None
    channel, ts = parsed

    token = os.environ.get("SLACK_TOKEN", "").strip()
    if not token:
        _warn(
            "SLACK_TOKEN not set; " f"treating {url!r} as a plain branch slug from URL"
        )
        return None

    qs = urllib.parse.urlencode({"channel": channel, "ts": ts, "limit": "1"})
    req = urllib.request.Request(
        f"{_SLACK_API_URL}?{qs}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            raw = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _warn(f"lookup failed for {url!r}: {exc}")
        return None

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        _warn(f"malformed response for {url!r}: {exc}")
        return None

    if not payload.get("ok"):
        err = payload.get("error", "unknown")
        _warn(f"API error for {url!r}: {err}")
        return None

    messages = payload.get("messages") or []
    if not messages:
        _warn(f"no messages in thread {url!r}")
        return None

    first = messages[0]
    return ResolvedThread(
        channel=channel,
        ts=ts,
        text=first.get("text") or "",
        permalink=url,
        reply_count=int(first.get("reply_count") or 0),
    )
