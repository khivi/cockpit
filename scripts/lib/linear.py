"""Linear ticket helpers.

Branch-name heuristic only — no Linear API calls. The pattern accepts any
2–6 uppercase prefix joined to digits by `-` (e.g. `PRO-123`, `ENG-4012`).
Keep narrow: too permissive and unrelated identifiers (`HTTP-200`,
`UTF-8`) would slip in; the upper bound on prefix length is the main guard.
"""

from __future__ import annotations

import re

LINEAR_RE = re.compile(r"[A-Z]{2,6}-[0-9]+")


def extract_ticket(branch: str) -> str:
    """Return the first Linear ticket id in `branch`, or "" if none."""
    if not branch:
        return ""
    m = LINEAR_RE.search(branch)
    return m.group(0) if m else ""
