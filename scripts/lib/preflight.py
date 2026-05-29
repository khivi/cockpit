"""Single dependency preflight, called from every `cockpit.py` invocation.

Hard-fails (sys.exit(2)) on missing required binaries:
  - `gh`, `git` — always
  - `cship`, `starship` — when `use_cship: true`

Soft-warns (stderr only) on missing optional backend:
  - `cmux` / `limux` — drops cockpit into cache-only mode

Slash-command entry scripts (`close.py`, `focus.py`, `spawn.py`) still call
`require_workspace_binary()` from `lib.cmux` for their own backend-mandatory
gate; that's a stricter policy than the daemon needs.
"""

from __future__ import annotations

import shutil
import sys

from .colors import yellow
from .tool import resolve_tool

REQUIRED_BINARIES = ("gh", "git")
CSHIP_BINARIES = ("cship", "starship")


def _die(msg: str) -> None:
    print(f"cockpit: {msg}", file=sys.stderr, flush=True)
    sys.exit(2)


def preflight(cfg: dict) -> None:
    for binary in REQUIRED_BINARIES:
        if shutil.which(binary) is None:
            _die(f"`{binary}` not found on PATH (required)")

    if cfg.get("use_cship"):
        for binary in CSHIP_BINARIES:
            if shutil.which(binary) is None:
                _die(
                    f"use_cship=true but `{binary}` is not on PATH. "
                    f"Install {binary} or set use_cship=false in your config."
                )

    if cfg.get("tool", "auto") == "auto":
        resolved = resolve_tool()
        if resolved == "limux":
            print(
                f"{yellow('cockpit:')} cmux not found — using limux. "
                "Side panel disabled (limux lacks pill support); "
                "footer/statusline and slash commands work. "
                "Set 'tool': 'cmux' in config to require cmux instead.",
                file=sys.stderr,
                flush=True,
            )
        elif resolved == "none":
            print(
                f"{yellow('cockpit:')} no workspace tool on PATH (cmux/limux) — "
                "running cache-only mode. Footer/statusline works; "
                "side panel and slash commands disabled. "
                "Set 'tool': 'none' in config to suppress this warning.",
                file=sys.stderr,
                flush=True,
            )
