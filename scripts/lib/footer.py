"""statusLine renderer — delegates to the `cship` binary.

Cockpit owns the worktree↔workspace↔PR data path and the per-PR cache, but
the visible statusline is rendered by [cship](https://github.com/khivi/cship).
This module is the thin shim Claude Code's `statusLine.command` invokes: it
pipes Claude Code's stdin JSON through to `cship` and forwards its output.

Before exec'ing cship, the stdin JSON is passed through
`lib.cship.stash_from_stdin` so the per-session context / rate-limit /
transcript-path caches that the starship.toml [custom.*] modules read are
kept fresh on every render. The mutated JSON (display_name suffix
stripped) is what actually reaches cship. CSHIP_SESSION_ID is exported so
the field printers find session-scoped cache entries.

Keeping a Python shim (rather than pointing `statusLine.command` at `cship`
directly) lets cockpit pre-process the input or fail soft when cship is not
installed — the loud opt-in check lives in
`lib.config.install_cship_statusline_if_configured`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .cship import stash_from_stdin

CSHIP_COMMAND = "cship"


def render_footer() -> int:
    """Stash caches from stdin JSON, pipe mutated JSON to cship, forward output.

    If cship isn't on PATH, exit 0 with no output — the statusline must never
    crash Claude Code. The daemon's installer is the loud failure surface for
    a misconfigured opt-in.
    """
    if shutil.which(CSHIP_COMMAND) is None:
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
    res = subprocess.run([CSHIP_COMMAND], input=mutated, capture_output=True, env=env)
    if res.stdout:
        sys.stdout.buffer.write(res.stdout)
    if res.stderr:
        sys.stderr.buffer.write(res.stderr)
    return res.returncode
