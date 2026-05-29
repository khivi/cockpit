#!/usr/bin/env python3
"""starship dispatcher: field printers + background refreshers + warm.

Invoked from `scripts/defaults/starship.toml`'s `[custom.*]` blocks for
each render (one subprocess per module per render), and self-spawned for
background refreshes when a PR-side cache is stale.

Subcommands:
  context              — Claude Code context window usage
  session-time         — current session duration
  rate-limit           — rolling 5h usage %
  model                — Claude model display name
  cost                 — running session spend in USD
  permission-mode      — current permission mode (hidden when default)
  branch-identity      — current branch + ahead-of-origin + ahead-of-base
  worktree-status      — staged/unstaged/untracked + behind-origin + base-staleness
  linear               — Linear ticket ID from branch name
  pr-state             — PR state (OPEN / DRAFT / APPROVED / ...)
  pr-num               — "#<n>" for the current branch's PR
  pr-comments          — 💬 N unaddressed review threads
  pr-checks            — CI glyph (✓ / • / ✗)
  pr-title             — PR title
  pr-muted             — 🔇 muted[: cats] when nudges are silenced
  warm                 — synchronous prewarm (PR data + checks + transcript seed)

Every subcommand exits 0 even on error and prints nothing on failure —
the statusline must never crash Claude Code.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.lib.cache import warm_all  # noqa: E402
from scripts.lib.starship import (  # noqa: E402
    print_branch_identity,
    print_context,
    print_cost,
    print_linear,
    print_model,
    print_permission_mode,
    print_pr_checks,
    print_pr_comments,
    print_pr_muted,
    print_pr_num,
    print_pr_state,
    print_pr_title,
    print_rate_limit,
    print_session_time,
    print_worktree_status,
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
        if cmd == "model":
            return _emit(print_model())
        if cmd == "cost":
            return _emit(print_cost())
        if cmd == "permission-mode":
            return _emit(print_permission_mode())
        if cmd == "branch-identity":
            return _emit(print_branch_identity())
        if cmd == "worktree-status":
            return _emit(print_worktree_status())
        if cmd == "linear":
            return _emit(print_linear())
        if cmd == "pr-state":
            return _emit(print_pr_state())
        if cmd == "pr-num":
            return _emit(print_pr_num())
        if cmd == "pr-comments":
            return _emit(print_pr_comments())
        if cmd == "pr-checks":
            return _emit(print_pr_checks())
        if cmd == "pr-title":
            return _emit(print_pr_title())
        if cmd == "pr-muted":
            return _emit(print_pr_muted())
        if cmd == "warm":
            warm_all()
            return 0
    except Exception:
        # statusline must never crash Claude Code
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
