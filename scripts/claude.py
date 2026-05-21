#!/usr/bin/env python3
"""Claude Code statusLine entry point.

Two-step pipeline per render:
  1. `lib.claude.stash_from_stdin(blob)` — parse Claude Code's JSON,
     write the session-scoped caches (context / rate-limit / transcript),
     return the (possibly mutated) blob + session_id.
  2. `lib.cship.invoke_cship(blob, sid)` — pipe that blob into the cship
     binary, forward its output back to Claude Code.

Each module owns exactly one concern: lib.claude handles Claude Code's
input format and its caches; lib.cship handles the cship binary and its
PR-side caches.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.claude import stash_from_stdin  # noqa: E402
from lib.cship import invoke_cship  # noqa: E402


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
