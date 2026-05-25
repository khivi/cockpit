"""Linear ticket helpers.

Two regex surfaces, both pure:

  * `LINEAR_RE` — finds a Linear ticket id *inside* a string (branch name,
    typically). Uppercase-only. Used by the statusline pill.
  * `LINEAR_RE_CI` — case-insensitive *fullmatch* regex for classifying a
    raw positional argument as a Linear id. Used by `spawn.detect_source`.

Both accept any 2–6 letter prefix joined to digits by `-` (`PE-1234`,
`ENG-4012`). The upper bound on prefix length is the main guard against
unrelated ids (`HTTP-200`, `UTF-8`).

No API calls live here. The Linear ticket body (title, description) is
fetched by Claude itself via the Linear MCP on the first turn of a
spawned workspace.
"""

from __future__ import annotations

import re

LINEAR_RE = re.compile(r"[A-Z]{2,6}-[0-9]+")
LINEAR_RE_CI = re.compile(r"[A-Za-z]{2,6}-[0-9]+")


def extract_ticket(branch: str) -> str:
    """Return the first uppercase Linear ticket id in `branch`, or "" if none."""
    if not branch:
        return ""
    m = LINEAR_RE.search(branch)
    return m.group(0) if m else ""
