#!/usr/bin/env python3
"""starship dispatcher: 8 field printers + background refreshers + warm.

Invoked from `scripts/defaults/starship.toml`'s `[custom.*]` blocks for
each render (8 subprocesses per render), and self-spawned for background
refreshes when a PR-side cache is stale.

Subcommands:
  context              — Claude Code context window usage
  session-time         — current session duration
  rate-limit           — rolling 5h usage %
  linear               — Linear ticket ID from branch name
  pr-state             — PR state (OPEN / DRAFT / APPROVED / ...)
  pr-num               — "#<n>" for the current branch's PR
  pr-checks            — CI glyph (✓ / • / ✗)
  pr-title             — PR title
  pr-state-refresh     — internal background refresh of pr-state/num/title
  pr-checks-refresh    — internal background refresh of pr-checks
  warm                 — synchronous prewarm (PR data + checks + transcript seed)

Every subcommand exits 0 even on error and prints nothing on failure —
the statusline must never crash Claude Code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.cache import refresh_pr_checks, refresh_pr_data, warm_all  # noqa: E402
from lib.git import current_branch  # noqa: E402
from lib.starship import (  # noqa: E402
    print_context,
    print_linear,
    print_pr_checks,
    print_pr_num,
    print_pr_state,
    print_pr_title,
    print_rate_limit,
    print_session_time,
)


def _emit(value: str) -> int:
    if value:
        sys.stdout.write(value)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 0
    cmd = argv[1]
    try:
        if cmd == "context":
            return _emit(print_context())
        if cmd == "session-time":
            return _emit(print_session_time())
        if cmd == "rate-limit":
            return _emit(print_rate_limit())
        if cmd == "linear":
            return _emit(print_linear())
        if cmd == "pr-state":
            return _emit(print_pr_state())
        if cmd == "pr-num":
            return _emit(print_pr_num())
        if cmd == "pr-checks":
            return _emit(print_pr_checks())
        if cmd == "pr-title":
            return _emit(print_pr_title())
        if cmd == "pr-state-refresh":
            refresh_pr_data(current_branch(os.getcwd()))
            return 0
        if cmd == "pr-checks-refresh":
            refresh_pr_checks(current_branch(os.getcwd()))
            return 0
        if cmd == "warm":
            warm_all()
            return 0
    except Exception:
        # statusline must never crash Claude Code
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
