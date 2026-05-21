#!/usr/bin/env python3
"""cship dispatcher: replaces the retired `~/bin/cship/cship-*.sh` helpers.

Invoked from `scripts/defaults/starship.toml` for the 8 `[custom.*]`
modules, and from hooks / one-shot CLI for the `warm` prewarm.

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

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.cship import (  # noqa: E402
    _current_branch,
    print_context,
    print_linear,
    print_pr_checks,
    print_pr_num,
    print_pr_state,
    print_pr_title,
    print_rate_limit,
    print_session_time,
    refresh_pr_checks,
    refresh_pr_data,
    warm_all,
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
            refresh_pr_data(_current_branch())
            return 0
        if cmd == "pr-checks-refresh":
            refresh_pr_checks(_current_branch())
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
