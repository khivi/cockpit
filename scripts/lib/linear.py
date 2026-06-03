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
import subprocess

LINEAR_RE = re.compile(r"[A-Z]{2,6}-[0-9]+")
LINEAR_RE_CI = re.compile(r"[A-Za-z]{2,6}-[0-9]+")

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
    """Return the first Linear ticket id in `branch` (uppercased), or "" if none."""
    if not branch:
        return ""
    m = LINEAR_RE.search(branch.upper())
    return m.group(0) if m else ""


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
