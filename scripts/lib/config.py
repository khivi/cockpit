"""Cockpit config + state-dir paths.

Owns:
  - filesystem paths under $COCKPIT_HOME
  - config.json read
  - state-dir bootstrap (copies config.example.json on first run)
  - discover_repo(): resolve cwd to a registered repo entry
  - prompt_statusline_setup(): first-time prompt wiring Claude Code's statusLine
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from .git import main_worktree_path

COCKPIT_HOME = Path(os.environ.get("COCKPIT_HOME", Path.home() / ".config" / "cockpit"))
CONFIG_PATH = COCKPIT_HOME / "config.json"
CACHE_DIR = COCKPIT_HOME / "cache"
PID_FILE = COCKPIT_HOME / "cockpit.pid"
CONFIG_EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.json"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {
            "repos": [],
            "poll_interval_seconds": 300,
            "auto_cleanup_on_merge": True,
        }
    with CONFIG_PATH.open() as f:
        return json.load(f)


def ensure_state_dirs() -> None:
    for p in (COCKPIT_HOME, CACHE_DIR):
        p.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists() and CONFIG_EXAMPLE.exists():
        shutil.copy(CONFIG_EXAMPLE, CONFIG_PATH)


def discover_repo() -> dict | None:
    """Return the config entry whose `path` matches the main repo of cwd, else None."""
    main = main_worktree_path()
    if main is None:
        return None
    cfg = load_config()
    for r in cfg.get("repos", []):
        if Path(r["path"]).expanduser().resolve() == main:
            return r
    return None


def find_repo_by_name(name: str) -> dict | None:
    """Return the config entry whose `name` matches, else None."""
    for r in load_config().get("repos", []):
        if r.get("name") == name:
            return r
    return None


def _read_current_statusline(settings_path: Path) -> str | None:
    if not settings_path.exists():
        return ""
    try:
        return (
            json.loads(settings_path.read_text())
            .get("statusLine", {})
            .get("command", "")
        )
    except (OSError, json.JSONDecodeError):
        return None


def _ask_and_write(settings_path: Path, footer_command: str, current: str) -> None:
    if current:
        print(f"Claude statusLine is currently: {current}")
        prompt = "replace with cockpit footer? [y/N] "
    else:
        prompt = f"wire Claude statusLine to cockpit footer ({footer_command})? [y/N] "
    try:
        reply = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    (COCKPIT_HOME / ".statusline-asked").touch()
    if reply != "y":
        print(
            f"skipped; set .statusLine.command to '{footer_command}' in {settings_path}"
        )
        return

    data: dict = {}
    if settings_path.exists():
        backup = settings_path.with_name(
            f"{settings_path.name}.bak.{datetime.now():%Y%m%d%H%M%S}"
        )
        backup.write_text(settings_path.read_text())
        try:
            data = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            data = {}
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    data["statusLine"] = {"type": "command", "command": footer_command}
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote Claude statusLine -> {footer_command}")


def prompt_statusline_setup(footer_command: str) -> None:
    """First-time prompt to wire Claude Code's statusLine to `scripts/footer.py`.

    No-ops when stdin isn't a TTY (e.g. invoked from a hook) or when the user
    has already been asked. Persists `~/.config/cockpit/.statusline-asked` to
    avoid re-prompting.
    """
    if not sys.stdin.isatty():
        return
    asked_flag = COCKPIT_HOME / ".statusline-asked"
    if asked_flag.exists():
        return
    settings_path = Path.home() / ".claude" / "settings.json"
    current = _read_current_statusline(settings_path)
    if current is None:
        return
    if current == footer_command:
        asked_flag.touch()
        return
    _ask_and_write(settings_path, footer_command, current)
