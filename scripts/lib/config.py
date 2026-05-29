"""Cockpit config + state-dir paths.

Owns:
  - filesystem paths under $COCKPIT_HOME
  - config.json read
  - state-dir bootstrap (copies config.example.json on first run)
  - discover_repo(): resolve cwd to a registered repo entry
  - install_cship_statusline_if_configured(): declarative statusLine writer,
    gated on `use_cship`. Points Claude Code's statusLine at the `cship`
    binary directly; hard-errors when the flag is set but cship isn't on PATH.
    Invoked only by `cockpit.py --footer`, not by --once / --watch.
  - install_cship_default_config(): rewrite ~/.config/cship.toml from the
    bundled default. Invoked only by `cockpit.py --footer`, not by --once /
    --watch — so reconcile cycles never touch ~/.config/cship.toml. Local
    edits to ~/.config/cship.toml survive across daemon restarts; running
    `cockpit --footer` deliberately clobbers them back to the bundled default.
  - install_starship_default_config(): same contract for ~/.config/starship.toml.
    cship's $starship_prompt spawns starship with STARSHIP_CONFIG set to that
    path, so any [custom.*] rendering depends on this file existing.
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
CSHIP_DEFAULT_TOML = Path(__file__).resolve().parent.parent / "defaults" / "cship.toml"
STARSHIP_DEFAULT_TOML = (
    Path(__file__).resolve().parent.parent / "defaults" / "starship.toml"
)
STARSHIP_PY = Path(__file__).resolve().parent.parent / "starship.py"
STARSHIP_PLACEHOLDER = "__COCKPIT_STARSHIP__"
STARSHIP_THEME_PLACEHOLDER = "__COCKPIT_THEME__"
VALID_THEMES = ("dark", "light")


def resolve_theme(cfg: dict | None = None) -> str:
    """Return the validated `theme` from config ("dark" | "light").

    Anything missing or unrecognized falls back to "dark" — the palette tuned
    for dark terminal backgrounds (see scripts/defaults/starship.toml). `cfg`
    is accepted so callers that already hold a loaded config avoid a second read.
    """
    theme = (cfg if cfg is not None else load_config()).get("theme", "dark")
    return theme if theme in VALID_THEMES else "dark"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {
            "repos": [],
            "slow_poll_interval_seconds": 300,
            "fast_poll_interval_seconds": 30,
            "auto_cleanup_on_merge": True,
            "autoclose_age_days": 14,
            "ci_skip_checks": ["copilot-pull-request-reviewer"],
            "theme": "dark",
        }
    with CONFIG_PATH.open() as f:
        data: dict = json.load(f)
        return data


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
        repo: dict = r
        if Path(repo["path"]).expanduser().resolve() == main:
            return repo
    return None


def find_repo_by_name(name: str) -> dict | None:
    """Return the config entry whose `name` matches, else None."""
    for r in load_config().get("repos", []):
        repo: dict = r
        if repo.get("name") == name:
            return repo
    return None


def find_repos_by_linear_key(identifier: str) -> list[dict]:
    """Return configured repos whose `linear_keys` list contains the prefix
    of `identifier` (case-insensitive match on `<PREFIX>-<digits>`).

    Empty list when the identifier doesn't parse as a Linear id, no repo
    declares the prefix, or no repo has a `linear_keys` field. Callers
    handle the empty / single / multi cases explicitly — this function
    does not pick a winner when more than one repo matches.
    """
    from .linear import LINEAR_RE_CI

    if not LINEAR_RE_CI.fullmatch(identifier):
        return []
    prefix = identifier.split("-", 1)[0].upper()
    out: list[dict] = []
    for r in load_config().get("repos", []):
        keys = r.get("linear_keys") or []
        if any(str(k).upper() == prefix for k in keys):
            out.append(r)
    return out


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
            found: dict = r
            return found
    return None


def prompt_prefix() -> str:
    """Optional first line prepended to every claude prompt spawned by cockpit.

    Configured via `prompt_prefix` in config.json (default: ""). Useful for
    invoking a personal session-start skill on every new workspace's first turn.
    """
    return str(load_config().get("prompt_prefix", "")).strip()


def use_linear() -> bool:
    """Whether the "smart" Linear flow is enabled (default: False).

    When False (default), `/cockpit:new PE-1234` still classifies as Linear
    mode (so the statusline pill keeps working), but spawn skips the
    MCP-instructing prompt — the workspace starts on `<prefix>pe-1234` with
    the generic plan-only prompt, equivalent to `/cockpit:new --branch pe-1234`.
    Safer default for users without the Linear MCP configured.

    When True, spawn pre-flights `claude mcp list` to confirm the Linear MCP
    is connected and only then seeds the 3-step rename prompt. If the
    pre-flight definitively reports no Linear MCP, spawn warns once and
    falls back to the plain-branch path.
    """
    return bool(load_config().get("use_linear", False))


def _read_current_statusline(settings_path: Path) -> str | None:
    if not settings_path.exists():
        return ""
    try:
        data: dict = json.loads(settings_path.read_text())
        command: str = data.get("statusLine", {}).get("command", "")
        return command
    except (OSError, json.JSONDecodeError):
        return None


def _write_statusline(settings_path: Path, statusline_command: str) -> None:
    """Write `statusline_command` into Claude Code's statusLine, backing up first."""
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
    data["statusLine"] = {"type": "command", "command": statusline_command}
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote claude statusLine -> {statusline_command}")


def _write_if_changed(dest: Path, payload: bytes, label: str, src: Path) -> bool:
    """Write `payload` to `dest` only if contents differ; print verbose status either way.

    On change (or first install): prints `installed default <label> config: <src> -> <dest>`.
    On no-op: prints `<label> config unchanged, default kept at <dest>`.
    Returns True iff the file was written.
    """
    if dest.exists() and not dest.is_symlink() and dest.read_bytes() == payload:
        print(f"{label} config unchanged, default kept at {dest}")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    print(f"installed default {label} config: {src} -> {dest}")
    return True


class CshipNotInstalledError(RuntimeError):
    """Raised when `use_cship: true` but the cship binary is not on PATH."""


def install_cship_statusline_if_configured(statusline_command: str) -> None:
    """Point Claude Code's statusLine at cockpit's statusline shim, gated on `use_cship`.

    `statusline_command` is the absolute invocation cockpit uses for its
    `scripts/footer.py` shim (which itself delegates to `cship`). When
    `use_cship: true` in config.json, cockpit verifies `cship` is on PATH and
    writes `~/.claude/settings.json` so Claude Code invokes the shim each
    render. Backs up any existing settings.json before overwriting. Raises
    `CshipNotInstalledError` if the flag is set but `cship` is missing —
    cockpit refuses to silently fall back since the user explicitly opted in.

    When the flag is unset or false, cockpit does not touch the statusLine.

    Called only from `cockpit.py --footer` — only --footer needs to mutate
    the statusLine. --once / --watch do not invoke this, but they still
    enforce the same `use_cship` → cship-on-PATH contract via
    `lib.preflight.preflight()`, which runs at the top of every cockpit
    invocation. The cship check here is defensive belt-and-suspenders for
    callers that bypass preflight.
    """
    cfg = load_config()
    if not cfg.get("use_cship"):
        return
    if shutil.which("cship") is None:
        raise CshipNotInstalledError(
            "use_cship=true but `cship` is not on PATH. "
            "Install cship (https://github.com/khivi/cship) or set "
            f"use_cship=false in {CONFIG_PATH}."
        )
    settings_path = Path.home() / ".claude" / "settings.json"
    current = _read_current_statusline(settings_path)
    if current == statusline_command:
        print("claude statusLine unchanged")
        return
    if current is None:
        return
    _write_statusline(settings_path, statusline_command)


def _xdg_config_path(filename: str) -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / filename


def _cship_user_config_path() -> Path:
    return _xdg_config_path("cship.toml")


def _starship_user_config_path() -> Path:
    return _xdg_config_path("starship.toml")


def _seed_default_toml(src: Path, dest: Path, label: str) -> None:
    """Copy `src` to `dest`, replacing a symlink at `dest` with a real file.

    If `dest` is a symlink, its current target is backed up (when the target
    exists) to `<target>.bak.<ts>` and the symlink itself is unlinked before
    writing — otherwise `shutil.copy` would follow the symlink and write
    through to whatever the user had it pointing at, which is exactly the
    scenario that broke this chain in the first place. Regular files are
    compared byte-for-byte against the bundled default; identical files are
    left in place and reported as `unchanged` rather than re-written.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        target = Path(os.readlink(dest))
        if not target.is_absolute():
            target = dest.parent / target
        if target.exists():
            backup = target.with_name(
                f"{target.name}.bak.{datetime.now():%Y%m%d%H%M%S}"
            )
            target.rename(backup)
            print(f"backed up {label} symlink target -> {backup}")
        dest.unlink()
    _write_if_changed(dest, src.read_bytes(), label, src)


def install_cship_default_config() -> None:
    """Rewrite ~/.config/cship.toml from the bundled default when `use_cship: true`.

    Called only from `cockpit.py --footer`. --once / --watch never touch this
    file, so reconcile cycles preserve local edits indefinitely. Running
    `cockpit --footer` deliberately copies `scripts/defaults/cship.toml` over
    the target — that command is the only thing that clobbers local edits.
    Honors `$XDG_CONFIG_HOME`. Soft-fails if the bundled file is missing.
    """
    if not load_config().get("use_cship"):
        return
    if not CSHIP_DEFAULT_TOML.exists():
        return
    _seed_default_toml(CSHIP_DEFAULT_TOML, _cship_user_config_path(), "cship")


def install_starship_default_config() -> None:
    """Rewrite ~/.config/starship.toml from the bundled default when `use_cship: true`.

    cship's `[cship]/lines = ["...$starship_prompt..."]` schema spawns
    starship with STARSHIP_CONFIG=~/.config/starship.toml whenever
    $starship_prompt expands, so the [time] and [custom.*] modules are
    rendered out of THIS file, not cship.toml. Same --footer-only contract
    as install_cship_default_config: reconcile cycles never touch it.

    Substitutes the literal `__COCKPIT_CSHIP__` token in the bundled toml
    with the resolved absolute path to `scripts/cship.py` before writing —
    starship spawns commands without changing cwd, so paths in the seeded
    file must be absolute. Re-running `cockpit --footer` after the plugin
    moves on disk re-substitutes with the new location.

    Also substitutes `__COCKPIT_THEME__` with the validated `theme` from
    config ("dark" | "light") so starship's `palette` selector picks the
    background-appropriate neutral greys. Because this is baked at seed time,
    changing `theme` takes effect on the next `cockpit --footer`.
    """
    if not load_config().get("use_cship"):
        return
    if not STARSHIP_DEFAULT_TOML.exists():
        return
    dest = _starship_user_config_path()
    payload = (
        STARSHIP_DEFAULT_TOML.read_text()
        .replace(STARSHIP_PLACEHOLDER, str(STARSHIP_PY))
        .replace(STARSHIP_THEME_PLACEHOLDER, resolve_theme())
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        target = Path(os.readlink(dest))
        if not target.is_absolute():
            target = dest.parent / target
        if target.exists():
            backup = target.with_name(
                f"{target.name}.bak.{datetime.now():%Y%m%d%H%M%S}"
            )
            target.rename(backup)
            print(f"backed up starship symlink target -> {backup}")
        dest.unlink()
    _write_if_changed(dest, payload.encode(), "starship", STARSHIP_DEFAULT_TOML)
