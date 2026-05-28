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


def extract_ticket(branch: str) -> str:
    """Return the first Linear ticket id in `branch` (uppercased), or "" if none."""
    if not branch:
        return ""
    m = LINEAR_RE.search(branch.upper())
    return m.group(0) if m else ""


def linear_mcp_available() -> bool | None:
    """Return True/False if `claude mcp list` definitively says, else None.

    Runs `claude mcp list` with a short timeout. Returns:
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
            timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    return "linear" in res.stdout.lower()
