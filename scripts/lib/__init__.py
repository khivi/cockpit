"""Cockpit library package.

This `__init__` exposes only the shared subprocess wrapper. All other
helpers live in sibling modules:

  - lib.config    — paths, config.json IO, statusline setup, discover_repo
  - lib.cache     — PR snapshot read/write/delete under $COCKPIT_HOME/cache
  - lib.claude    — statusLine shim that delegates to the `cship` binary
  - lib.cship     — cship-cache writers + field-printer functions
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


def run(cmd: list[str], check: bool = True, env: dict | None = None) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if check and res.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {res.stderr.strip()}")
    return res.stdout
