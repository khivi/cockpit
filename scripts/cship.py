#!/usr/bin/env python3
"""cship dispatcher: replaces the retired `~/bin/cship/cship-*.sh` helpers.

Invoked from `scripts/defaults/starship.toml` for the 8 `[custom.*]`
modules, from `lib/footer.py` for the statusLine wrapper, and from
`cockpit.py` (daemon tick) for the synchronous prewarm.

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
  wrapper              — statusLine entry-point: stash caches, exec cship

Every subcommand exits 0 even on error and prints nothing on failure —
the statusline must never crash Claude Code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
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
    stash_from_stdin,
    warm_all,
)

CSHIP_BIN = "cship"


def _emit(value: str) -> int:
    if value:
        sys.stdout.write(value)
    return 0


def _wrapper() -> int:
    """Read Claude Code statusLine JSON, stash caches, exec cship.

    If cship isn't on PATH, exit 0 silently — the statusline must never
    crash Claude Code. The loud opt-in check lives in
    `lib.config.install_cship_statusline_if_configured`.
    """
    if shutil.which(CSHIP_BIN) is None:
        return 0
    blob = b""
    if not sys.stdin.isatty():
        try:
            blob = sys.stdin.buffer.read()
        except OSError:
            blob = b""
    mutated, sid = stash_from_stdin(blob)
    env = os.environ.copy()
    if sid:
        env["CSHIP_SESSION_ID"] = sid
    res = subprocess.run([CSHIP_BIN], input=mutated, capture_output=True, env=env)
    if res.stdout:
        sys.stdout.buffer.write(res.stdout)
    if res.stderr:
        sys.stderr.buffer.write(res.stderr)
    return res.returncode


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
        if cmd == "wrapper":
            return _wrapper()
    except Exception:
        # statusline must never crash Claude Code
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
