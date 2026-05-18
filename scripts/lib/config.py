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
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import run

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
    res = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True
    )
    if res.returncode != 0:
        return None
    out = run(["git", "worktree", "list", "--porcelain"], check=False)
    main = next(
        (
            Path(line.split(" ", 1)[1]).resolve()
            for line in out.splitlines()
            if line.startswith("worktree ")
        ),
        None,
    )
    if main is None:
        return None
    cfg = load_config()
    for r in cfg.get("repos", []):
        if Path(r["path"]).expanduser().resolve() == main:
            return r
    return None


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
    current = ""
    if settings_path.exists():
        try:
            current = (
                json.loads(settings_path.read_text())
                .get("statusLine", {})
                .get("command", "")
            )
        except (OSError, json.JSONDecodeError):
            return
    if current == footer_command:
        asked_flag.touch()
        return

    if current:
        print(f"Claude statusLine is currently: {current}")
        prompt = "replace with cockpit footer? [y/N] "
    else:
        prompt = f"wire Claude statusLine to cockpit footer ({footer_command})? [y/N] "
    try:
        reply = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    asked_flag.touch()
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
