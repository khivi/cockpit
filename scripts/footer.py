#!/usr/bin/env python3
"""Claude Code statusLine entry — orchestrates the footer render.

First byte in, last byte out: this script is what Claude Code's
statusLine.command points at, and its stdout is what Claude Code displays
as the footer. Two-step pipeline per render:

  1. `lib.claude.stash_from_stdin(blob)` — parse Claude Code's JSON,
     write the session-scoped caches (context / rate-limit / transcript),
     return the (possibly mutated) blob + session_id.
  2. `lib.cship.invoke_cship(blob, sid)` — pipe that blob into the cship
     binary; cship renders the line (delegating [custom.*] blocks to
     starship, which spawns scripts/starship.py × 8 to fill them); the
     resulting bytes are forwarded to this script's stdout, which Claude
     Code reads and displays.

Each module owns one concern: lib.claude handles Claude Code's input
format and session caches; lib.cship handles the cship binary; lib.cache
owns the flat cockpit-cache layout; lib.starship has the field printers.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.lib.claude import stash_from_stdin  # noqa: E402
from scripts.lib.cship import invoke_cship  # noqa: E402


def main() -> int:
    blob = b""
    if not sys.stdin.isatty():
        try:
            blob = sys.stdin.buffer.read()
        except OSError:
            blob = b""
    mutated, sid = stash_from_stdin(blob)
    return invoke_cship(mutated, sid)


if __name__ == "__main__":
    sys.exit(main())
