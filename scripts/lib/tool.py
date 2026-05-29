"""Workspace-backend dispatch — cmux vs limux.

cmux and limux share most CLI verbs, but a handful diverge:
  - `list-workspaces` JSON layout (`current_directory` vs `cwd`)
  - `--json` flag position (global on limux, per-command on cmux)
  - `new-workspace` flags (`--name` / `--focus` are cmux-only)
  - `focus` verb (cmux-only; limux has no equivalent)

This module owns the dispatch. Callers can ask `is_cmux()` / `is_limux()`
for branching decisions, or call the action wrappers (`workspace_cwds()`,
`spawn_workspace()`) which encapsulate the per-backend differences.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from . import run

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


def workspace_cwds() -> dict[str, Path]:
    """{ref: current_directory} via `cmux rpc workspace.list` (cmux) or `limux --json list-workspaces` (limux).

    Raises `CmuxUnavailable` on nonzero rc or unparsable output, so a backend
    hiccup is not misread as an empty workspace set.

    limux uses `--json` as a global flag (before the command), so the limux
    path bypasses the `cmux()` wrapper — `cmux("--json", ...)` would still
    work, but the global flag is clearer as a direct `run([...])` invocation.
    """
    # Lazy import to avoid a cmux ↔ tool circular: cmux.py imports
    # resolve_tool/is_cmux/is_limux from this module at top level.
    from .cmux import CmuxUnavailable, cmux

    if is_limux():
        cwd_key = "cwd"
        label = "limux --json list-workspaces"
        try:
            out = run(["limux", "--json", "list-workspaces"], check=True)
        except RuntimeError as e:
            raise CmuxUnavailable(f"{label} failed: {e}") from e
    else:
        cwd_key = "current_directory"
        label = "rpc workspace.list"
        try:
            out = cmux("rpc", "workspace.list", "{}", check=True)
        except RuntimeError as e:
            raise CmuxUnavailable(f"{label} failed: {e}") from e

    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise CmuxUnavailable(f"{label} returned non-JSON: {e}") from e
    cwds: dict[str, Path] = {}
    for ws in data.get("workspaces", []):
        ref = ws.get("ref")
        cwd = ws.get(cwd_key)
        if ref and cwd:
            cwds[ref] = Path(cwd)
    return cwds


def spawn_workspace(name: str, cwd: Path, command: str) -> str | None:
    """Spawn a new workspace and return its ref, or None on failure.

    cmux: passes --name/--focus, polls list-workspaces for the new ref since
    `cmux new-workspace` does not echo it on stdout.

    limux: passes --cwd/--command only (limux's new-workspace lacks --name
    and --focus). Parses the ref from stdout ("OK workspace:<uuid>") and
    follows up with `rename-workspace` so cockpit's name conventions match.
    """
    from .cmux import cmux, list_workspaces, wait_for_new_workspace_ref

    if is_limux():
        out = cmux(
            "new-workspace",
            "--cwd",
            str(cwd),
            "--command",
            command,
            check=False,
        )
        m = re.search(r"(workspace:[\w-]+)", out)
        if m is None:
            return None
        ref = m.group(1)
        cmux("rename-workspace", "--workspace", ref, name, check=False)
        return ref

    before = set(list_workspaces())
    cmux(
        "new-workspace",
        "--name",
        name,
        "--cwd",
        str(cwd),
        "--command",
        command,
        "--focus",
        "false",
    )
    return wait_for_new_workspace_ref(before)
