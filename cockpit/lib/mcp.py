"""Read Claude Code's MCP server list — the one probe cockpit makes, and it
gates nothing.

`claude mcp list` health-checks each server by connecting, and a claude.ai-
managed connector handshakes asynchronously, so it has reported a live server
absent. That false negative is why a probe may never *gate* a feature: an
earlier pre-flight did, and dropped the ticket fetch on precisely the setup the
feature targeted (`prompts/linear.txt`'s retry-then-STOP is what replaced it).

A diagnostic is the other case. `ticket_check` reports what the probe said to a
human who is reading the report, and a wrong line there costs a second look
rather than a silently disabled feature. The guard that keeps the two apart is
the return type: `None` means the probe could not be run at all and must never
be flattened into an empty listing, since "no servers" and "didn't ask" are the
two readings the paid-for bug confused.

Not a daemon call. Nothing on a tick, in a cell, or in a gate reads this.
"""

from __future__ import annotations

import re
import subprocess

_TIMEOUT_SECONDS = 20

#: `<name>: <transport> - <glyph> <health>`, the one line shape `claude mcp list`
#: prints per server. The health words are matched as a group rather than a
#: literal set so a wording change degrades to an unrecognised status rather
#: than to a missing server — absent is the reading that carries a caveat.
_LINE_RE = re.compile(r"^(?P<name>.+?): .* - (?P<health>.+)$")

#: Leading status glyphs (`✔`, `!`, `✗`) stripped off the health word.
_GLYPH_RE = re.compile(r"^[^\w]+")


def list_mcp_servers(*, repo_dir: str | None = None) -> dict[str, str] | None:
    """`{server name: lower-cased health}` for every server Claude Code lists,
    or None when the probe could not be run.

    `repo_dir` is the cwd, because MCP scope is partly per project: a server in
    the repo's own .mcp.json is invisible from anywhere else, and the daemon's
    cwd is its own.

    None — never raises — on a missing `claude`, a timeout, a non-zero exit or
    output in no recognisable shape. `{}` means it ran and listed nothing.
    """
    try:
        res = subprocess.run(
            ["claude", "mcp", "list"],
            capture_output=True,
            text=True,
            cwd=repo_dir,
            timeout=_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    out: dict[str, str] = {}
    for line in res.stdout.splitlines():
        match = _LINE_RE.match(line.strip())
        if match:
            out[match["name"].strip()] = (
                _GLYPH_RE.sub("", match["health"]).strip().casefold()
            )
    # Output in no recognisable shape is "couldn't ask", not "no servers": the
    # line format above is undocumented, and a reformat read as an empty config
    # would have the report accuse a working setup. The cost is that a genuinely
    # empty MCP config also reports as unchecked, which the two cases cannot be
    # told apart without matching a CLI message string.
    return out or (None if res.stdout.strip() else {})
