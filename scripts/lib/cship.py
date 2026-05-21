"""cship-binary exec — the bridge from cockpit's statusLine shim into cship.

`scripts/footer.py` calls `invoke_cship` after `lib.claude.stash_from_stdin`
has captured the session caches. cship then renders its line, delegating
`[custom.*]` modules to starship (which spawns `scripts/starship.py` per
field — see `lib.starship`).

This module's sole job is the binary invocation. The flat cockpit-cache
layout lives in `lib.cache`; starship-side readers live in `lib.starship`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

CSHIP_BIN = "cship"


def invoke_cship(blob: bytes, sid: str | None) -> int:
    """Exec the cship binary with `blob` piped to stdin; forward output.

    If cship isn't on PATH, returns 0 silently — the statusline must
    never crash Claude Code. The loud opt-in check is in
    `lib.config.install_cship_statusline_if_configured`.

    Exports `CSHIP_SESSION_ID=<sid>` so the field-printer subprocesses
    starship spawns under cship find the session-scoped cache entries.
    """
    if shutil.which(CSHIP_BIN) is None:
        return 0
    env = os.environ.copy()
    if sid:
        env["CSHIP_SESSION_ID"] = sid
    res = subprocess.run([CSHIP_BIN], input=blob, capture_output=True, env=env)
    if res.stdout:
        sys.stdout.buffer.write(res.stdout)
    if res.stderr:
        sys.stderr.buffer.write(res.stderr)
    return res.returncode
