"""Cockpit config + state-dir paths.

Owns:
  - filesystem paths under $COCKPIT_HOME
  - config.json read
  - state-dir bootstrap (copies config.example.json on first run)
  - discover_repo(): resolve cwd to a registered repo entry
  - install_statusline_if_configured(): declarative statusLine writer, gated
    on the `install_statusline` config flag (default false).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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


def find_repo_by_nwo(nwo: str) -> dict | None:
    """Return the config entry whose `origin` remote matches `nwo` (owner/name).

    Reads `remote.origin.url` for each configured repo and parses the
    GitHub `owner/name` out of it. Accepts both SSH (`git@github.com:o/n.git`)
    and HTTPS (`https://github.com/o/n[.git]`) forms.
    """
    target = nwo.lower().removesuffix(".git")
    pat = re.compile(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$")
    for r in load_config().get("repos", []):
        path = Path(r["path"]).expanduser()
        if not path.exists():
            continue
        res = subprocess.run(
            ["git", "-C", str(path), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            continue
        m = pat.search(res.stdout.strip())
        if m and m.group(1).lower() == target:
            return r
    return None


def prompt_prefix() -> str:
    """Optional first line prepended to every claude prompt spawned by cockpit.

    Configured via `prompt_prefix` in config.json (default: ""). Useful for
    invoking a personal session-start skill on every new workspace's first turn.
    """
    return str(load_config().get("prompt_prefix", "")).strip()


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


def _write_statusline(settings_path: Path, footer_command: str) -> None:
    """Write `footer_command` into Claude Code's statusLine, backing up first."""
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


def install_statusline_if_configured(footer_command: str) -> None:
    """Declarative statusLine installer, gated on `install_statusline` config.

    When `install_statusline: true` in config.json, ensure Claude Code's
    `~/.claude/settings.json` has `statusLine.command == footer_command`.
    Backs up any existing settings.json before overwriting.

    When the flag is unset or false, cockpit does not touch the statusLine —
    users keep whatever they had. No interactive prompt; the config flag is
    the entire opt-in surface.
    """
    cfg = load_config()
    if not cfg.get("install_statusline"):
        return
    settings_path = Path.home() / ".claude" / "settings.json"
    current = _read_current_statusline(settings_path)
    if current is None or current == footer_command:
        return
    _write_statusline(settings_path, footer_command)
