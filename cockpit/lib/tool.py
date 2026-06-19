"""Workspace-backend policy — which backend (cmux vs limux) is in effect.

cmux and limux share most CLI verbs, but a handful diverge:
  - `list-workspaces` JSON layout (`current_directory` vs `cwd`)
  - `--json` flag position (global on limux, per-command on cmux)
  - `new-workspace` flags (`--name` / `--focus` are cmux-only)
  - `focus` verb (cmux-only; limux has no equivalent)

This module owns only the *policy* decision: it resolves the configured
backend and answers `is_cmux()` / `is_limux()`. It has no dependency on the
cmux CLI wrapper. The per-backend *actions* that encode those divergences
(`workspace_cwds`, `spawn_workspace`) live in `cockpit.lib.cmux` alongside the
`cmux()` wrapper they drive — keeping this a leaf with no import cycle.
"""

from __future__ import annotations

import shutil
import sys

_VALID_TOOLS = frozenset({"cmux", "limux", "none", "auto"})


def resolve_tool() -> str:
    """Pick the workspace backend: 'cmux', 'limux', or 'none'.

    Reads cfg['tool'] (cmux|limux|none|auto, default auto). 'auto' detects:
    prefers cmux, falls back to limux, else 'none'. Resolved fresh each call
    so tests can vary PATH / config across cases without cache leakage.
    """
    from .config import load_config

    explicit: str = str(load_config().get("tool", "auto"))
    if explicit not in _VALID_TOOLS:
        print(
            f"cockpit: invalid 'tool' value {explicit!r} "
            f"(expected one of {sorted(_VALID_TOOLS)}); falling back to 'auto'",
            file=sys.stderr,
        )
        explicit = "auto"
    if explicit in {"cmux", "limux", "none"}:
        return explicit
    if shutil.which("cmux"):
        return "cmux"
    if shutil.which("limux"):
        return "limux"
    return "none"


def is_cmux() -> bool:
    return resolve_tool() == "cmux"


def is_limux() -> bool:
    return resolve_tool() == "limux"


def has_workspace_backend() -> bool:
    """True when a workspace tool (cmux or limux) is resolved — i.e. not 'none'.

    Workspace + worktree lifecycle (spawn, close, reap, autoclose's best-effort
    workspace close) works on both backends; only pills/focus/color additionally
    require cmux (see `is_cmux`). Resolved fresh each call, like `resolve_tool`.
    """
    return resolve_tool() != "none"
