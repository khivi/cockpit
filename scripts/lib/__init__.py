"""Cockpit library package.

This `__init__` exposes only the shared subprocess wrapper. All other
helpers live in sibling modules:

  - lib.config    — paths, config.json IO, statusline setup, discover_repo
  - lib.cache     — JSON per-PR cache + flat cockpit-cache layout & writers
  - lib.claude    — parse Claude Code statusLine JSON, write session caches
  - lib.cship     — invoke_cship: exec the cship binary
  - lib.starship  — starship field-printers + background refresh fork
  - lib.colors    — ANSI terminal colors
  - lib.prompts   — Claude prompt builders + shell quoting
  - lib.registry  — register cwd's repo into config.json
  - lib.daemon    — pidfile + signals + sleep/wake loop
  - lib.git       — worktree dataclass, listing, slug + path helpers
  - lib.gh        — gh CLI/GraphQL, PR dataclass
  - lib.cmux      — cmux wrapper, workspace queries, status pills
"""

from __future__ import annotations

import subprocess
import sys

_INSTALL_HINTS = {
    "gh": "https://cli.github.com",
    "git": "https://git-scm.com",
    "cship": "https://github.com/khivi/cship",
    "starship": "https://starship.rs",
}


def run(cmd: list[str], check: bool = True, env: dict | None = None) -> str:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    except FileNotFoundError:
        binary = cmd[0] if cmd else ""
        hint = _INSTALL_HINTS.get(binary)
        suffix = f" — install from {hint}" if hint else ""
        print(
            f"cockpit: {binary!r} not found on PATH{suffix}",
            file=sys.stderr,
        )
        sys.exit(2)
    if check and res.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {res.stderr.strip()}")
    return res.stdout
