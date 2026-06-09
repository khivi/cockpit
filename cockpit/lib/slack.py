"""Slack thread helpers — a *spawn source*, not a daemon state source.

Unlike Linear (which the daemon also queries directly for the `devdone=` pill),
Slack is consumed only at spawn time: `spawn.detect_source` classifies a Slack
permalink as `slack` mode, cockpit creates a worktree on a codename branch, and
the spawned Claude reads the thread itself via the Slack MCP on its first turn.
The daemon never reaches the Slack API and stores no Slack state — so there is
no cache cell, pill, or `docs/state-machine.md` node for it (same shape as the
`actions` mode).

This module is therefore pure config/classification:

  * `SLACK_URL_RE` — recognizes the two Slack permalink shapes for
    `detect_source`: the workspace `archives` link copied from the desktop/web
    "Copy link" action, and the `app.slack.com/client/...` deep link.
  * `slack_seed` — the thread's *stable identity* (channel id + message
    timestamp) extracted from a permalink, used to seed the deterministic
    codename branch so the same thread always maps to the same branch
    regardless of volatile query params or which link shape was copied.

There is deliberately no `claude mcp list` probe here (unlike Linear's
`linear_mcp_available`): that check has proven unreliable — `claude mcp list`
doesn't dependably report claude.ai-managed connectors, so it returns
None/False even when the connector is live, and a false-negative would silently
disable the feature. Spawn instead always seeds the fetch prompt under
`use_slack` and lets the prompt's own retry-then-STOP logic handle a genuinely
absent connector in-session.
"""

from __future__ import annotations

import re

# Two permalink shapes:
#   1. https://<workspace>.slack.com/archives/<CHANNEL>/p<TS>[?thread_ts=…&cid=…]
#      — the "Copy link to message" form. <CHANNEL> is a C…/G…/D… id; p<TS> is
#      the message timestamp with the dot stripped.
#   2. https://app.slack.com/client/<TEAM>/<CHANNEL>[/thread/…] — the deep link
#      the web client shows in the address bar.
# Anchored matching (not fullmatch) so a trailing query string / fragment is
# tolerated; a branch name never contains `slack.com/…`, so this can't swallow
# an ordinary positional.
SLACK_URL_RE = re.compile(
    r"https?://"
    r"(?:[a-z0-9][a-z0-9-]*\.slack\.com/archives/[A-Z0-9]+/p\d+"
    r"|app\.slack\.com/client/[A-Z0-9]+/[A-Z0-9]+)",
    re.IGNORECASE,
)

# Capture the stable identity out of each shape so the codename seed is
# invariant to query params / fragment and to which link form was copied.
_ARCHIVES_RE = re.compile(
    r"https?://[a-z0-9][a-z0-9-]*\.slack\.com/archives/([A-Z0-9]+)/p(\d+)",
    re.IGNORECASE,
)
_CLIENT_RE = re.compile(
    r"https?://app\.slack\.com/client/([A-Z0-9]+)/([A-Z0-9]+)",
    re.IGNORECASE,
)


def is_slack_url(value: str) -> bool:
    """True iff `value` begins with a recognized Slack permalink."""
    return SLACK_URL_RE.match(value) is not None


def slack_seed(url: str) -> str:
    """Return the stable identity of a Slack permalink, for the codename seed.

    Extracts only the parts that identify the thread and never vary between
    copies of the same link — so the deterministic codename is the same whether
    the URL carried `?thread_ts=…&cid=…`, a trailing slash, or was the web
    client's deep link:

      * `archives/<CH>/p<TS>` → `"<ch>/<ts>"` (lowercased; the message identity)
      * `app.slack.com/client/<TEAM>/<CH>` → `"<team>/<ch>"` (channel identity;
        this shape carries no message ts)

    Falls back to the URL with any query/fragment stripped when neither shape
    matches (defensive — `detect_source` only routes recognized URLs here).
    """
    m = _ARCHIVES_RE.match(url)
    if m:
        return f"{m.group(1).lower()}/{m.group(2)}"
    m = _CLIENT_RE.match(url)
    if m:
        return f"{m.group(1).lower()}/{m.group(2).lower()}"
    return url.split("?", 1)[0].split("#", 1)[0]
