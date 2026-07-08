"""Cockpit config + state-dir paths.

Owns:
  - filesystem paths under $COCKPIT_HOME
  - config.json read
  - state-dir bootstrap (copies config.example.json on first run)
  - discover_repo(): resolve cwd to a registered repo entry
  - install_cship_statusline_if_configured(): declarative statusLine writer,
    gated on `use_cship`. Points Claude Code's statusLine at the `cship`
    binary directly; hard-errors when the flag is set but cship isn't on PATH.
    Invoked only by `cockpit setup`, not by --watch.
  - install_cship_default_config(): rewrite ~/.config/cship.toml from the
    bundled default. Invoked only by `cockpit setup`, not by --watch — so reconcile cycles never touch ~/.config/cship.toml. Local
    edits to ~/.config/cship.toml survive across daemon restarts; running
    `cockpit setup` deliberately clobbers them back to the bundled default.
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
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

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
# Render command substituted for __COCKPIT_STARSHIP__ in the seeded
# starship.toml. Uses the running interpreter + module dispatch so it resolves
# regardless of whether `cockpit` is on PATH in starship's render environment.
# Invoked the normal way (the installed `cockpit` console script), `sys.executable`
# is the stable uv-tool interpreter; `cockpit update` re-runs `cockpit setup` after
# the install so a stale pin (e.g. to a since-removed worktree venv) is re-pinned.
STARSHIP_CMD = f"{sys.executable} -m cockpit.cli starship"
STARSHIP_PLACEHOLDER = "__COCKPIT_STARSHIP__"
STARSHIP_THEME_PLACEHOLDER = "__COCKPIT_THEME__"
VALID_THEMES = ("dark", "light")
# Default Textual theme for the `cockpit watch` TUI when `tui_theme` is unset.
# Mirrors Textual's own default (constants.DEFAULT_THEME = $TEXTUAL_THEME or
# "textual-dark"), so an absent key changes nothing.
TUI_THEME_DEFAULT = "textual-dark"


def resolve_theme(cfg: dict | None = None) -> str:
    """Return the validated `theme` from config ("dark" | "light").

    Anything missing or unrecognized falls back to "dark" — the palette tuned
    for dark terminal backgrounds (see cockpit/defaults/starship.toml). `cfg`
    is accepted so callers that already hold a loaded config avoid a second read.
    """
    theme = (cfg if cfg is not None else load_config()).get("theme", "dark")
    return theme if theme in VALID_THEMES else "dark"


def resolve_tui_theme(cfg: dict | None = None) -> str:
    """Return the configured Textual theme name for the `cockpit watch` TUI.

    Distinct from `theme`: that is the dark|light palette tuning the cmux pills
    (`lib.colors`) and the starship/cship footer (`resolve_theme` → TOML), both
    rendered *outside* this process. `tui_theme` names a *Textual* theme (e.g.
    "textual-dark", "nord", "gruvbox") styling only the TUI's own chrome. The
    two are intentionally independent. The name is NOT validated here — the
    valid set is Textual's registered-theme registry, known only to the running
    App — so the caller (`CockpitApp.on_mount`) falls back to the App default
    when the name isn't registered.
    """
    name = (cfg if cfg is not None else load_config()).get("tui_theme")
    return name if isinstance(name, str) and name else TUI_THEME_DEFAULT


def save_tui_theme(name: str) -> None:
    """Persist the chosen Textual theme to config.json's `tui_theme` key.

    Textual holds the active theme in memory only (`App.theme` defaults to
    $TEXTUAL_THEME / "textual-dark") and never writes it to disk, so a theme
    picked from the Ctrl+P "Change theme" palette resets on the next launch
    unless we store it. This is the one sanctioned config write from the TUI:
    `tui_theme` is a TUI-only cosmetic — never a cache cell, never read by the
    daemon's reconcile — so it doesn't touch the daemon-is-sole-writer
    invariant. Read-modify-writes the on-disk file atomically (preserving every
    other key) and drops the per-process cache so a later `load_config()` in the
    same run sees it. A no-op when the value is unchanged.
    """
    try:
        data = _read_config()
    except (OSError, ValueError):
        data = {}
    if data.get("tui_theme") == name:
        return
    data["tui_theme"] = name
    ensure_state_dirs()
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, CONFIG_PATH)
    reset_config_cache()


_CONFIG_CACHE: dict | None = None


def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {
            "repos": [],
            "slow_poll_interval_seconds": 300,
            "fast_poll_interval_seconds": 30,
            "autoclose_age_days": 14,
            "theme": "dark",
            "tui_theme": TUI_THEME_DEFAULT,
        }
    with CONFIG_PATH.open() as f:
        data: dict = json.load(f)
        return data


def load_config() -> dict:
    """Return the cockpit config, read from disk once per process.

    The config file is parsed on the first call and the result is reused for
    the process lifetime — `resolve_tool`/`is_cmux` and the per-tick reconcile
    would otherwise re-read + re-parse `config.json` dozens of times per tick.
    The workspace backend and repo set are stable within a daemon run, so an
    edit to `config.json` is picked up on the next daemon start, not mid-run.

    Tests that vary config across cases call `reset_config_cache()` (an autouse
    fixture does this between tests) so each starts like a fresh process; the
    `COCKPIT_HOME`-reloading fixtures also reset it by re-importing the module.
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        _CONFIG_CACHE = _read_config()
    return _CONFIG_CACHE


def reset_config_cache() -> None:
    """Drop the cached config so the next `load_config()` re-reads from disk."""
    global _CONFIG_CACHE
    _CONFIG_CACHE = None


def ensure_state_dirs() -> None:
    for p in (COCKPIT_HOME, CACHE_DIR):
        p.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        # Seed an empty, valid config rather than copying config.example.json:
        # the example's placeholder repos (fake /absolute/path/to/... paths)
        # used to land verbatim in a fresh install, erroring every daemon tick
        # forever since registry.register_cwd only appends. config.example.json
        # itself stays untouched as documentation of the schema.
        CONFIG_PATH.write_text(json.dumps({"repos": []}, indent=2) + "\n")


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
    """Return configured repos whose Linear team keys contain the prefix
    of `identifier` (case-insensitive match on `<PREFIX>-<digits>`).

    Keys come from `tickets.keys` (or the legacy flat `linear_keys`) via
    `linear_team_keys`. Empty list when the identifier doesn't parse as a Linear
    id or no repo declares the prefix. Callers handle the empty / single / multi
    cases explicitly — this function does not pick a winner on a multi match.
    """
    from .linear import LINEAR_RE_CI

    if not LINEAR_RE_CI.fullmatch(identifier):
        return []
    prefix = identifier.split("-", 1)[0].upper()
    out: list[dict] = []
    for r in load_config().get("repos", []):
        keys = linear_team_keys(repo_entry=r)
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


VALID_TICKETS = ("none", "linear", "github", "jira", "trello")

# The single label that lights the `devdone=` pill for a GitHub issue when
# `tickets.dev_done_label` is unset — GitHub has no named workflow states, so a
# label stands in for Linear's dev-done state. "ready for review" is the common
# "development complete, awaiting review" label. (NB: a label like "accepted"
# usually means *work started*, not done — that's `tickets.start_label`.)
GITHUB_DEV_DONE_DEFAULT = "ready for review"

# The Jira status name that lights the `devdone=` pill when `tickets
# .dev_done_status` is unset. Jira boards vary, so this is just a sensible
# convention — a dedicated "dev complete, awaiting review" lane distinct from the
# terminal "Done" (the Jira analog of Linear's "Dev Done" default).
JIRA_DEV_DONE_DEFAULT = "Dev Done"

# Slash command seeded as the first turn of an auto-spawned `review_prs`
# worktree. Defaults to cockpit's own `/cockpit:review` plugin command — it
# ships with the plugin, so every cockpit user has it in every spawned review
# workspace (unlike a personal global skill, which only resolves for its owner),
# and it reviews against the *target repo's* documented conventions. Override
# per-repo (or globally) with `review_command` (e.g. the built-in `/review`, or
# a personal `/pr-review`).
REVIEW_COMMAND_DEFAULT = "/cockpit:review"


def _tickets_block(src: dict | None) -> dict:
    """Normalize a config source's `tickets` value to a dict: a bare string
    (shorthand) becomes ``{"provider": <str>}``, an object passes through,
    anything else (or missing) → ``{}``."""
    if src is None:
        return {}
    val = src.get("tickets")
    if isinstance(val, str):
        return {"provider": val}
    if isinstance(val, dict):
        return val
    return {}


def _tickets_field(
    cfg: dict | None, repo_entry: dict | None, field: str
) -> object | None:
    """One `tickets` field, resolved **per-field** repo-block → global-block →
    None. Per-field (not whole-block) so a global default for one setting — e.g.
    `close_on_merge` — still applies to a repo whose own `tickets` block omits
    that field but sets others.
    """
    rb = _tickets_block(repo_entry)
    if field in rb:
        repo_val: object = rb[field]
        return repo_val
    gb = _tickets_block(cfg if cfg is not None else load_config())
    if field in gb:
        global_val: object = gb[field]
        return global_val
    return None


def tickets(cfg: dict | None = None) -> str:
    """Return the *global* ticket provider: ``"none" | "linear" | "github"``.

    The single selector that replaced the old boolean ``use_linear``. Reads the
    global ``tickets`` block's ``provider`` (or the bare-string shorthand). Names
    which tracker cockpit integrates: the spawn fetch/rename prompt, the
    `devdone=` pill, and the done-on-merge writer. Unset/unrecognized →
    ``"none"``. Use `repo_tickets` to resolve the provider for a specific repo.
    """
    val = _tickets_block(cfg if cfg is not None else load_config()).get("provider")
    return str(val) if val in VALID_TICKETS else "none"


def repo_tickets(cfg: dict | None = None, repo_entry: dict | None = None) -> str:
    """Resolve the ticket provider for one repo: ``"none" | "linear" | "github"``.

    Resolution order, first match wins:
      1. the `tickets` block's ``provider`` (per-field repo → global),
      2. ``"linear"`` when the repo declares the legacy flat ``linear_keys`` but
         no provider is set anywhere — back-compat so existing Linear repos keep
         their pill without a `tickets` block,
      3. ``"none"``.

    The slow tick (devdone pill, done-on-merge) and the TUI ticket columns all
    gate on this per-repo value rather than the global one.
    """
    provider = _tickets_field(cfg, repo_entry, "provider")
    if provider in VALID_TICKETS:
        return str(provider)
    if repo_entry is not None and repo_entry.get("linear_keys"):
        return "linear"
    return "none"


def github_dev_done_label(
    cfg: dict | None = None, repo_entry: dict | None = None
) -> str:
    """The single issue label that lights the `devdone=` pill under
    ``tickets: github`` — an issue carrying it counts as dev-done.

    Default ``"ready for review"``. Override with the `tickets` block's
    ``dev_done_label`` (a string). Matched case-insensitively against each
    delivered issue's label names — the GitHub analog of Linear's
    ``dev_done_state``.
    """
    val = _tickets_field(cfg, repo_entry, "dev_done_label")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return GITHUB_DEV_DONE_DEFAULT


def linear_team_keys(
    cfg: dict | None = None, repo_entry: dict | None = None
) -> list[str]:
    """The repo's Linear team-key prefixes (e.g. ``["PE"]``) — used to route a
    `PE-1234` spawn to this repo and to gate the Linear slow-tick reads/writes.

    Read from the `tickets` block's ``keys`` (per-field repo → global), falling
    back to the legacy flat `linear_keys` field so existing configs keep working.
    Empty when neither is set (the repo isn't Linear-configured).
    """
    keys = _tickets_field(cfg, repo_entry, "keys")
    if isinstance(keys, list):
        return [str(k) for k in keys]
    if repo_entry is not None and isinstance(repo_entry.get("linear_keys"), list):
        return [str(k) for k in repo_entry["linear_keys"]]
    return []


def github_start_label(
    cfg: dict | None = None, repo_entry: dict | None = None
) -> str | None:
    """Issue label cockpit applies when it spawns a worktree on a GitHub issue
    (`tickets.start_label`, e.g. "accepted" = work started). Opt-in: None when
    unset, so cockpit performs no GitHub write. The one spawn-time tracker write.
    """
    val = _tickets_field(cfg, repo_entry, "start_label")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def ticket_close_on_merge(
    cfg: dict | None = None, repo_entry: dict | None = None
) -> bool:
    """Whether the daemon performs its terminal tracker write when a PR merges
    (default: False) — `gh issue close` for GitHub, the "Done" state transition
    for Linear. Reads the `tickets` block's ``close_on_merge`` **per-field** (a
    repo without its own value inherits a global ``tickets.close_on_merge``).
    Opt-in because it makes the daemon a tracker *writer*.

    Back-compat: falls back to the legacy flat `linear_done_on_merge` key
    (per-repo over global) when no `tickets.close_on_merge` is set anywhere, so
    existing Linear configs keep working without migrating to the object form.
    """
    val = _tickets_field(cfg, repo_entry, "close_on_merge")
    if val is not None:
        return bool(val)
    if repo_entry is not None and "linear_done_on_merge" in repo_entry:
        return bool(repo_entry["linear_done_on_merge"])
    cfg = cfg if cfg is not None else load_config()
    return bool(cfg.get("linear_done_on_merge", False))


def review_command(cfg: dict | None = None, repo_entry: dict | None = None) -> str:
    """The slash command seeded as the first turn of an auto-spawned review
    worktree (per-repo `review_prs`).

    Default ``"/cockpit:review"`` — cockpit's own plugin command, available in
    every spawned review workspace because the plugin is installed alongside the
    daemon (a personal global skill would only resolve for its owner). It reviews
    against the *target repo's* documented conventions, so it stays portable
    across watched repos. Override per-repo (or globally) with a `review_command`
    string — e.g. the built-in ``"/review"`` or a personal ``"/pr-review"``.
    Resolved repo-block → global-block → default; a non-string/blank value falls
    through to the next level.
    """
    if repo_entry is not None:
        rv = repo_entry.get("review_command")
        if isinstance(rv, str) and rv.strip():
            return rv.strip()
    cfg = cfg if cfg is not None else load_config()
    gv = cfg.get("review_command")
    if isinstance(gv, str) and gv.strip():
        return gv.strip()
    return REVIEW_COMMAND_DEFAULT


def use_slack() -> bool:
    """Whether Slack-thread spawn sources are enabled (default: False).

    When False (default), a Slack permalink passed to `/cockpit:new` still
    classifies as `slack` mode in `spawn.detect_source` (so the worktree lands
    on a codename branch instead of a garbage branch named after the URL), but
    spawn skips the Slack-MCP-fetch prompt — the workspace starts on the
    codename branch with the generic plan-only prompt carrying the URL as
    context, and the user can fetch the thread manually. Safer default for users
    without the Slack MCP configured.

    When True, spawn seeds the Slack-MCP fetch + branch/workspace rename prompt.
    Unlike `use_linear`, there is deliberately no `claude mcp list` pre-flight —
    that probe is unreliable for claude.ai-managed connectors, so the fetch
    prompt's own retry-then-STOP logic handles a genuinely absent connector
    in-session (see `cockpit.lib.slack`).
    """
    return bool(load_config().get("use_slack", False))


def linear_dev_done_state(
    cfg: dict | None = None, repo_entry: dict | None = None
) -> str:
    """Name of the Linear workflow state that the `devdone=` pill keys off
    (default: "Dev Done"). Matched case-insensitively against the ticket's live
    `state.name`. Set via the `tickets` block's ``dev_done_state`` (a string),
    falling back to the legacy flat `linear_dev_done_state` key, then "Dev Done".
    """
    val = _tickets_field(cfg, repo_entry, "dev_done_state")
    if isinstance(val, str) and val.strip():
        return val.strip()
    cfg = cfg if cfg is not None else load_config()
    return str(cfg.get("linear_dev_done_state") or "Dev Done").strip() or "Dev Done"


def linear_merge_done_state(
    cfg: dict | None = None, repo_entry: dict | None = None
) -> str:
    """Name of the Linear workflow state a delivered ticket is moved to when its
    PR merges (default: "Done"). Distinct from `linear_dev_done_state` ("Dev
    Done") — that's where the passive `devdone=` pill lights up while the PR is
    *open*; this is the terminal state the `ticket_close_on_merge` writer
    transitions to on merge. Set via the `tickets` block's ``merge_done_state``,
    falling back to the legacy flat `linear_merge_done_state` key, then "Done".
    """
    val = _tickets_field(cfg, repo_entry, "merge_done_state")
    if isinstance(val, str) and val.strip():
        return val.strip()
    cfg = cfg if cfg is not None else load_config()
    return str(cfg.get("linear_merge_done_state") or "Done").strip() or "Done"


def jira_site_url(cfg: dict | None = None, repo_entry: dict | None = None) -> str:
    """The Jira Cloud site base URL (e.g. ``https://acme.atlassian.net``) used by
    the `tickets: jira` provider's REST calls. Read from the `tickets` block's
    ``site_url`` (per-field repo → global), trailing slash stripped so the daemon
    never builds a ``//rest`` 404. Empty when unset — the provider then makes no
    REST call (feature off), same as Linear with no `LINEAR_API_KEY`.
    """
    val = _tickets_field(cfg, repo_entry, "site_url")
    if isinstance(val, str) and val.strip():
        return val.strip().rstrip("/")
    return ""


def jira_email(cfg: dict | None = None, repo_entry: dict | None = None) -> str:
    """The Jira account email paired with ``$JIRA_API_TOKEN`` for HTTP Basic auth.
    Read from the `tickets` block's ``email`` (per-field repo → global). Empty
    when unset (the provider then makes no REST call). Not a secret — the token
    is the credential — so it lives in config, not the env.
    """
    val = _tickets_field(cfg, repo_entry, "email")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return ""


def jira_dev_done_status(
    cfg: dict | None = None, repo_entry: dict | None = None
) -> str:
    """Name of the Jira status that the `devdone=` pill keys off (default
    "Dev Done"). Matched case-insensitively against the issue's live status name
    — the Jira analog of `linear_dev_done_state`. Set via the `tickets` block's
    ``dev_done_status`` (a string).
    """
    val = _tickets_field(cfg, repo_entry, "dev_done_status")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return JIRA_DEV_DONE_DEFAULT


def jira_merge_done_status(
    cfg: dict | None = None, repo_entry: dict | None = None
) -> str:
    """Name of the Jira status a delivered issue is transitioned to when its PR
    merges (default "Done"). Distinct from `jira_dev_done_status` ("Dev Done") —
    that's the passive pill while the PR is *open*; this is the terminal status
    the opt-in `ticket_close_on_merge` writer moves to on merge. Set via the
    `tickets` block's ``merge_done_status`` (a string).
    """
    val = _tickets_field(cfg, repo_entry, "merge_done_status")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return "Done"


def trello_dev_done_list(
    cfg: dict | None = None, repo_entry: dict | None = None
) -> str:
    """Name of the Trello list (board column) that lights the `devdone=` pill —
    the Trello analog of `jira_dev_done_status`, matched case-insensitively
    against the card's current list name. Set via the `tickets` block's
    ``dev_done_list``. **No default** — Trello boards name their lists
    arbitrarily, so an unset value (empty string) means the pill never lights
    (feature off), never a guessed list name.
    """
    val = _tickets_field(cfg, repo_entry, "dev_done_list")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return ""


def trello_merge_done_list(
    cfg: dict | None = None, repo_entry: dict | None = None
) -> str:
    """Name of the Trello list a delivered card is moved to when its PR merges —
    the Trello analog of `jira_merge_done_status`. Set via the `tickets` block's
    ``merge_done_list``. **No default** — an unset value (empty string) leaves
    the opt-in merge-move off, never guessing a list name.
    """
    val = _tickets_field(cfg, repo_entry, "merge_done_list")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return ""


def orphan_nudge_grace_seconds(
    cfg: dict | None = None, repo_entry: dict | None = None
) -> float:
    """Seconds a no-open-PR ("orphan") worktree is left un-nudged after creation
    (default: 4 hours).

    A freshly-spawned worktree (e.g. from `start-linear-ticket` / `cockpit new`)
    has the exact shape the orphan nudge targets — branch created, no commits, no
    PR — so without a grace window it draws the "push commits and open a PR, or
    close the worktree if abandoned" nudge on the very next slow tick, every tick.
    The grace measures *worktree age* (`git.worktree_age_seconds`), not branch or
    commit age, so it answers "how long since I made this worktree" — the thing a
    user means by "I just started it."

    Resolved per-repo over global, matching `linear_done_on_merge`: an
    `orphan_nudge_grace_hours` on the repo entry wins, otherwise the top-level
    key, otherwise 4. `0` disables the grace entirely (immediate nudging — the
    pre-grace behaviour). Negative / out-of-range values are clamped to 0.
    """
    default_hours = 4.0
    hours: Any = default_hours
    if repo_entry is not None and "orphan_nudge_grace_hours" in repo_entry:
        hours = repo_entry["orphan_nudge_grace_hours"]
    else:
        cfg = cfg if cfg is not None else load_config()
        if "orphan_nudge_grace_hours" in cfg:
            hours = cfg["orphan_nudge_grace_hours"]
    try:
        return max(0.0, float(hours) * 3600.0)
    except (TypeError, ValueError):
        return default_hours * 3600.0


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
    `cockpit/statusline.py` shim (which itself delegates to `cship`). When
    `use_cship: true` in config.json, cockpit verifies `cship` is on PATH and
    writes `~/.claude/settings.json` so Claude Code invokes the shim each
    render. Backs up any existing settings.json before overwriting. Raises
    `CshipNotInstalledError` if the flag is set but `cship` is missing —
    cockpit refuses to silently fall back since the user explicitly opted in.

    When the flag is unset or false, cockpit does not touch the statusLine.

    Called only from `cockpit setup` — only --setup needs to mutate
    the statusLine. --watch do not invoke this, but they still
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

    Called only from `cockpit setup`. --watch never touch this
    file, so reconcile cycles preserve local edits indefinitely. Running
    `cockpit setup` deliberately copies `cockpit/defaults/cship.toml` over
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
    rendered out of THIS file, not cship.toml. Same --setup-only contract
    as install_cship_default_config: reconcile cycles never touch it.

    Substitutes the literal `__COCKPIT_CSHIP__` token in the bundled toml
    with the resolved absolute path to `cockpit/cship.py` before writing —
    starship spawns commands without changing cwd, so paths in the seeded
    file must be absolute. Re-running `cockpit setup` after the plugin
    moves on disk re-substitutes with the new location.

    Also substitutes `__COCKPIT_THEME__` with the validated `theme` from
    config ("dark" | "light") so starship's `palette` selector picks the
    background-appropriate neutral greys. Because this is baked at seed time,
    changing `theme` takes effect on the next `cockpit setup`.
    """
    if not load_config().get("use_cship"):
        return
    if not STARSHIP_DEFAULT_TOML.exists():
        return
    dest = _starship_user_config_path()
    payload = (
        STARSHIP_DEFAULT_TOML.read_text()
        .replace(STARSHIP_PLACEHOLDER, STARSHIP_CMD)
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
