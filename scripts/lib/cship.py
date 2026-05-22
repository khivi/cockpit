"""cship-binary exec — the bridge from cockpit's statusLine shim into cship.

`scripts/footer.py` calls `invoke_cship` after `lib.claude.stash_from_stdin`
has captured the session caches. cship then renders its line, delegating
`[custom.*]` modules to starship (which spawns `scripts/starship.py` per
field — see `lib.starship`).

A bundled `scripts/bin/starship` shim is prepended to PATH on the cship
subprocess only — it rewrites `STARSHIP_SHELL=unknown` (which cship 1.7.1
sets to force plain ANSI) to `sh`, so starship's [custom.*] modules
actually render. Host PATH is never touched.

This module's sole job is the binary invocation. The flat cockpit-cache
layout lives in `lib.cache`; starship-side readers live in `lib.starship`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

CSHIP_BIN = "cship"
STARSHIP_BIN = "starship"
BIN_DIR = Path(__file__).resolve().parent.parent / "bin"


def invoke_cship(blob: bytes, sid: str | None) -> int:
    """Exec the cship binary with `blob` piped to stdin; forward output.

    If cship or starship is missing on PATH, write the diagnostic to
    stderr and return a non-zero exit code. The footer is wired up via
    `use_cship=true`, which implies both binaries are installed — a
    missing one is misconfiguration, not a transient state to paper
    over, so we surface it loudly instead of returning 0.

    Exports `CSHIP_SESSION_ID=<sid>` so the field-printer subprocesses
    starship spawns under cship find the session-scoped cache entries.
    Prepends `scripts/bin/` to PATH on the subprocess env so cship's
    inner starship invocation hits the STARSHIP_SHELL shim.
    """
    if shutil.which(CSHIP_BIN) is None:
        sys.stderr.write(
            "cockpit footer: `cship` binary not on PATH — install cship "
            "(https://github.com/khivi/cship) or set use_cship=false\n"
        )
        return 127
    if shutil.which(STARSHIP_BIN) is None:
        sys.stderr.write(
            "cockpit footer: `starship` binary not on PATH — install starship "
            "(https://starship.rs) to render footer pills\n"
        )
        return 127
    env = os.environ.copy()
    if sid:
        env["CSHIP_SESSION_ID"] = sid
    env["PATH"] = f"{BIN_DIR}{os.pathsep}{env.get('PATH', '')}"
    res = subprocess.run([CSHIP_BIN], input=blob, capture_output=True, env=env)
    if res.stdout:
        sys.stdout.buffer.write(res.stdout)
    if res.stderr:
        sys.stderr.buffer.write(res.stderr)
    return res.returncode
