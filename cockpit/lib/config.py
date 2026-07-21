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

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from .git import main_worktree_path


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text via a temp file + os.replace so a crash can't leave a
    truncated config. Same pattern save_config_value already uses."""
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


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
# Invoked the normal way (the brew-installed `cockpit` console script),
# `sys.executable` is the stable installed interpreter; re-run `cockpit setup`
# to re-pin if it ever points at a stale (e.g. removed worktree venv) interpreter.
STARSHIP_CMD = f"{sys.executable} -m cockpit.cli starship"
STARSHIP_PLACEHOLDER = "__COCKPIT_STARSHIP__"
STARSHIP_THEME_PLACEHOLDER = "__COCKPIT_THEME__"
# The line break between the format's two lines (session pills / PR pills).
# Substituted at install time: a real newline off-macOS (the two-line layout),
# but empty on macOS — Claude Code renders only the FIRST line of a multi-line
# statusLine there (anthropics/claude-code#35176, closed not-planned), so a
# second line would be silently dropped. Collapsing to one line keeps the PR
# pills visible on macOS.
STARSHIP_LINE_SEP_PLACEHOLDER = "__COCKPIT_LINE_SEP__"
VALID_THEMES = ("dark", "light")
# Every statusline field name — one per `[custom.*]` module in starship.toml and
# one per non-`warm` subcommand in `starship.py::main`. `statusline_hide` lists a
# subset to suppress; preflight validates entries against this set.
# `test_statusline_fields_match_toml_modules` asserts this stays in sync with the
# shipped starship.toml.
STATUSLINE_FIELDS = frozenset(
    {
        "model",
        "context",
        "rate-limit",
        "repo",
        "branch-identity",
        "worktree-status",
        "permission-mode",
        "cost",
        "session-time",
        "ticket",
        "pr-state",
        "pr-num",
        "pr-comments",
        "pr-checks",
        "pr-title",
        "pr-muted",
    }
)
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


def save_config_value(key: str, value: Any) -> None:
    """Atomically set a single top-level `config.json` key, preserving all other
    keys, and drop the per-process cache. Used by interactive `cockpit setup` to
    persist a feature toggle the user opts into (e.g. `use_cship`). No-op when
    the value is unchanged. Same read-modify-write contract as `save_tui_theme`.
    """
    try:
        data = _read_config()
    except OSError:
        data = {}
    except json.JSONDecodeError as exc:
        print(
            f"cockpit: config unreadable ({exc}); backing up and resetting",
            file=sys.stderr,
        )
        with contextlib.suppress(OSError):
            CONFIG_PATH.rename(CONFIG_PATH.with_name(CONFIG_PATH.name + ".corrupt"))
        data = {}
    if data.get(key) == value:
        return
    data[key] = value
    ensure_state_dirs()
    _atomic_write_text(CONFIG_PATH, json.dumps(data, indent=2) + "\n")
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


def _skills_block(src: dict | None) -> dict:
    """The `skills` object off a config source (global or a repo entry), or `{}`."""
    if src is None:
        return {}
    block = src.get("skills")
    return block if isinstance(block, dict) else {}


def _skills_field(cfg: dict | None, repo_entry: dict | None, field: str) -> str | None:
    """One `skills` field, resolved repo-block → global-block → None. A
    non-string/blank value falls through to the next level."""
    for src in (repo_entry, cfg if cfg is not None else load_config()):
        v = _skills_block(src).get(field)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def prompt_prefix() -> str:
    """Slash command seeded as its own first turn in every workspace cockpit
    spawns (e.g. `/session-coordination`), or "" when unset. Configured via
    `skills.session` (global — it applies to every spawn).
    """
    return _skills_field(load_config(), None, "session") or ""


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
# worktree. Defaults to Claude Code's built-in `/review`, which ships with every
# Claude Code install so it resolves in every spawned review workspace (unlike a
# personal global skill, which only resolves for its owner). Override per-repo
# (or globally) with `skills.review` — e.g. a personal `/pr-review`.
#
# Why this defaults to a command while `skills.plan`/`skills.actions` default to
# "" (built-in prose): the rule is the same for all three — "default to the
# built-in slash command if one exists, else ship bundled prose." Only `/review`
# has a universal Claude Code built-in to point at; there is no `/plan` or
# `/actions` built-in, so those fall back to `plan_only.txt`/`actions.txt` rather
# than dangle a command that resolves to nothing on an unconfigured install.
REVIEW_COMMAND_DEFAULT = "/review"


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

    Default ``"/review"`` — Claude Code's built-in review command, available in
    every spawned review workspace (a personal global skill would only resolve
    for its owner). Override with `skills.review` per-repo or globally — e.g. a
    personal ``"/pr-review"``. Resolved `skills.review` repo → global → default;
    a non-string/blank value falls through to the next level.
    """
    cfg = cfg if cfg is not None else load_config()
    return _skills_field(cfg, repo_entry, "review") or REVIEW_COMMAND_DEFAULT


def plan_command(cfg: dict | None = None, repo_entry: dict | None = None) -> str:
    """The slash command seeded as the first turn of a plan-only spawn (a PR or
    branch worth studying before implementing).

    Default ``""`` — unset means cockpit seeds its own built-in plan-only prose
    (`cockpit/prompts/plan_only.txt`). Override with `skills.plan` per-repo or
    globally — e.g. a personal ``"/plan-pr"``. Resolved `skills.plan` repo →
    global → default, same shape as `review_command`.
    """
    cfg = cfg if cfg is not None else load_config()
    return _skills_field(cfg, repo_entry, "plan") or ""


def actions_command(cfg: dict | None = None, repo_entry: dict | None = None) -> str:
    """The slash command seeded as the first turn of a GitHub-Actions-run-URL
    spawn.

    Default ``""`` — unset means cockpit seeds its own built-in Actions
    investigation prose (`cockpit/prompts/actions.txt`). Override with
    `skills.actions` per-repo or globally — e.g. a personal ``"/actions-pr"``.
    Resolved `skills.actions` repo → global → default, same shape as
    `review_command`.
    """
    cfg = cfg if cfg is not None else load_config()
    return _skills_field(cfg, repo_entry, "actions") or ""


def base_remote(cfg: dict | None = None, repo_entry: dict | None = None) -> str:
    """The git remote the footer ahead/staleness count measures against.

    Default ``"origin"``. On a fork whose `origin/<default>` is a stale mirror of
    the real upstream, the `↗` ahead-of-base count is dominated by that drift
    rather than the branch's own commits. Point this at the remote that carries
    upstream history (e.g. `"upstream"`) so `<remote>/<base>..HEAD` reflects the
    true delta. The base *branch* is still the PR's base (or the repo default);
    this only swaps the remote half of `<remote>/<branch>`. Worktree creation and
    push stay on `origin` — this is a read-only measurement knob.

    Resolved repo-block → global-block → default; a non-string/blank value falls
    through to the next level.
    """
    if repo_entry is not None:
        rv = repo_entry.get("base_remote")
        if isinstance(rv, str) and rv.strip():
            return rv.strip()
    cfg = cfg if cfg is not None else load_config()
    gv = cfg.get("base_remote")
    if isinstance(gv, str) and gv.strip():
        return gv.strip()
    return "origin"


def review_external(repo_entry: dict) -> bool:
    """Whether `review_prs` also auto-spawns a review workspace for a PR
    authored by someone who isn't a repo collaborator (default: False).

    `list_open_pr_heads` reports each candidate's `authorAssociation`; the
    `_spawn_missing_workspaces` gate only spawns OWNER/MEMBER/COLLABORATOR by
    default — an external/fork PR's body and diff are untrusted content that
    would otherwise reach a Bash-capable auto-spawned agent (prompt-injection
    risk on a public repo). Opt in per-repo with `review_external: true`, same
    shape as the `dependabot` per-repo bool.
    """
    return bool(repo_entry.get("review_external"))


def statusline_hidden(cfg: dict | None = None) -> set[str]:
    """Statusline field names the user has hidden (global, default none).

    Each entry is a `cockpit starship <name>` field (see `STATUSLINE_FIELDS`);
    the dispatcher skips a listed field so its footer segment renders empty.
    Lets a user drop pills they don't care about (e.g. `cost`, `session-time`,
    `rate-limit`). Non-string / unknown entries are ignored here — preflight
    hard-fails on them so a typo surfaces at daemon start rather than silently.
    """
    cfg = cfg if cfg is not None else load_config()
    raw = cfg.get("statusline_hide")
    if not isinstance(raw, list):
        return set()
    return {x.strip() for x in raw if isinstance(x, str) and x.strip()}


def use_slack() -> bool:
    """Whether Slack-thread spawn sources are enabled (default: False).

    When False (default), a Slack permalink passed to `cockpit new` still
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


# Claude Code hooks cockpit owns. Commands resolve off PATH (the installed
# `cockpit` console script) — there is no plugin root to interpolate. This is
# what the plugin's hooks.json used to provide, minus the retired SessionStart
# self-update hook. `cockpit statusline` stashes the statusLine stdin caches on
# Stop; `cockpit idle-pill <phase>` drives the cmux idle pill the nudge gate
# reads.
_COCKPIT_HOOKS: dict[str, list[dict]] = {
    "Stop": [
        {
            "matcher": "",
            "hooks": [
                {"type": "command", "command": "cockpit statusline || true"},
                {"type": "command", "command": "cockpit idle-pill stop || true"},
            ],
        }
    ],
    "UserPromptSubmit": [
        {
            "matcher": "",
            "hooks": [
                {"type": "command", "command": "cockpit idle-pill prompt || true"}
            ],
        }
    ],
    "PreToolUse": [
        {
            "matcher": "ScheduleWakeup|CronCreate|CronUpdate",
            "hooks": [
                {"type": "command", "command": "cockpit idle-pill loop-set || true"}
            ],
        },
        {
            "matcher": "CronDelete",
            "hooks": [
                {"type": "command", "command": "cockpit idle-pill loop-clear || true"}
            ],
        },
    ],
    "SessionEnd": [
        {
            "matcher": "",
            "hooks": [
                {"type": "command", "command": "cockpit idle-pill loop-clear || true"}
            ],
        }
    ],
}


# Every command `_COCKPIT_HOOKS` writes starts with one of these two tokens
# (e.g. "cockpit statusline || true", "cockpit idle-pill stop || true") — never
# a bare substring match, so an unrelated hook that merely mentions "cockpit
# statusline" (e.g. in an echo) doesn't get swept up as cockpit-owned.
_COCKPIT_HOOK_CMD_RE = re.compile(r"^cockpit (statusline|idle-pill)(\s|$)")


def _is_cockpit_hook_group(group: dict) -> bool:
    """True if a settings.json hook group is one cockpit owns — any of its
    commands invoke `cockpit statusline` / `cockpit idle-pill` as the command
    itself."""
    for h in group.get("hooks", []):
        cmd = str(h.get("command", "")).strip()
        if _COCKPIT_HOOK_CMD_RE.match(cmd):
            return True
    return False


def claude_integration_present(settings_path: Path | None = None) -> bool:
    """True iff cockpit's hooks are already wired into ~/.claude/settings.json —
    i.e. the user has run `cockpit setup`. `cockpit watch` uses this to *re-assert*
    an existing integration (repair drift) without force-installing it onto
    someone who never opted in."""
    settings_path = settings_path or (Path.home() / ".claude" / "settings.json")
    if not settings_path.exists():
        return False
    try:
        data = json.loads(settings_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    hooks = data.get("hooks") or {}
    return any(_is_cockpit_hook_group(g) for groups in hooks.values() for g in groups)


def install_claude_hooks(settings_path: Path | None = None) -> None:
    """Merge cockpit's Claude Code hooks into ~/.claude/settings.json.

    Idempotent: cockpit-owned hook groups are dropped and rewritten each run, so
    a re-`setup` never duplicates them; unrelated user hooks are preserved.
    Backs up an existing settings.json before rewriting, and no-ops (no backup)
    when the merged result is byte-identical to what's already there. Called
    from `cockpit setup`; replaces what the plugin's hooks.json used to install.
    """
    settings_path = settings_path or (Path.home() / ".claude" / "settings.json")
    original = settings_path.read_text() if settings_path.exists() else None
    try:
        data: dict = json.loads(original) if original else {}
    except json.JSONDecodeError:
        data = {}
    hooks: dict = data.get("hooks") or {}
    for event, groups in _COCKPIT_HOOKS.items():
        kept = [g for g in hooks.get(event, []) if not _is_cockpit_hook_group(g)]
        # Deep-copy the owned groups so the module-level template stays pristine.
        hooks[event] = kept + json.loads(json.dumps(groups))
    data["hooks"] = hooks
    new_text = json.dumps(data, indent=2) + "\n"
    if original == new_text:
        print("claude hooks unchanged")
        return
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if original is not None:
        settings_path.with_name(
            f"{settings_path.name}.bak.{datetime.now():%Y%m%d%H%M%S}"
        ).write_text(original)
    _atomic_write_text(settings_path, new_text)
    print(f"wrote claude hooks -> {settings_path}")


# Package holding the bundled `/cockpit-new` + `/cockpit-close` command
# templates (`cockpit/claude_commands/*.md`). Flat filenames, not a `cockpit/`
# subdirectory: Claude Code's `.claude/commands/` convention names a command
# from its filename only (`deploy.md` -> `/deploy`) — there is no documented
# subdirectory-namespace scheme for user commands (that colon-namespacing is
# a plugin-only feature, keyed off the plugin name, which cockpit no longer
# is). So the files ship as `cockpit-new.md` / `cockpit-close.md`, invoked as
# `/cockpit-new` / `/cockpit-close`.
_CLAUDE_COMMANDS_PACKAGE = "cockpit.claude_commands"


def _bundled_claude_commands() -> list[Any]:
    return sorted(
        (
            p
            for p in files(_CLAUDE_COMMANDS_PACKAGE).iterdir()
            if p.name.endswith(".md")
        ),
        key=lambda p: p.name,
    )


def install_claude_commands(commands_dir: Path | None = None) -> None:
    """Install cockpit's bundled slash commands into `~/.claude/commands/`.

    Mirrors `install_claude_hooks`'s contract: idempotent (a byte-identical
    file is left alone, reported "unchanged"), backs up an existing file
    before overwriting it with different content, and never touches unrelated
    user command files. Templates are read via `importlib.resources` so this
    works from the installed wheel, not just a source checkout (same
    mechanism as `cockpit.lib.templates`). Called from `cockpit setup`.
    """
    commands_dir = commands_dir or (Path.home() / ".claude" / "commands")
    commands_dir.mkdir(parents=True, exist_ok=True)
    for resource in _bundled_claude_commands():
        target = commands_dir / resource.name
        new_text = resource.read_text(encoding="utf-8")
        original = target.read_text() if target.exists() else None
        if original == new_text:
            print(f"claude command unchanged -> {target}")
            continue
        if original is not None:
            _backup_settings(target, original)
        target.write_text(new_text)
        print(f"wrote claude command -> {target}")


def uninstall_claude_commands(commands_dir: Path | None = None) -> bool:
    """Inverse of `install_claude_commands`: remove cockpit's bundled command
    files from `~/.claude/commands/`. Leaves unrelated user commands alone.
    Returns True iff it removed at least one file."""
    commands_dir = commands_dir or (Path.home() / ".claude" / "commands")
    removed = False
    for resource in _bundled_claude_commands():
        target = commands_dir / resource.name
        if target.exists():
            target.unlink()
            removed = True
    if removed:
        print(f"removed cockpit slash commands -> {commands_dir}")
    return removed


def _backup_settings(settings_path: Path, original: str) -> None:
    settings_path.with_name(
        f"{settings_path.name}.bak.{datetime.now():%Y%m%d%H%M%S}"
    ).write_text(original)


def uninstall_claude_hooks(settings_path: Path | None = None) -> bool:
    """Inverse of `install_claude_hooks`: drop cockpit-owned hook groups.

    Removes every hook group `_is_cockpit_hook_group` matches, prunes any event
    list (and the whole `hooks` block) left empty, and preserves unrelated user
    hooks. Backs up before rewriting. Returns True iff it changed the file.
    `brew uninstall` leaves these hooks pointing at a now-missing `cockpit`
    binary, so `cockpit teardown` calls this before uninstalling.
    """
    settings_path = settings_path or (Path.home() / ".claude" / "settings.json")
    if not settings_path.exists():
        return False
    original = settings_path.read_text()
    try:
        data: dict = json.loads(original)
    except json.JSONDecodeError:
        return False
    hooks: dict = data.get("hooks") or {}
    removed = False
    for event in list(hooks):
        kept = [g for g in hooks[event] if not _is_cockpit_hook_group(g)]
        if len(kept) != len(hooks[event]):
            removed = True
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not removed:
        return False
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    new_text = json.dumps(data, indent=2) + "\n"
    _backup_settings(settings_path, original)
    _atomic_write_text(settings_path, new_text)
    print(f"removed cockpit claude hooks -> {settings_path}")
    return True


def clear_cockpit_statusline(settings_path: Path | None = None) -> bool:
    """Inverse of `install_cship_statusline_if_configured`: drop the statusLine.

    Removes Claude Code's `statusLine` only when it points at cockpit's shim
    (`cockpit.cli statusline` / `cockpit statusline`) — a user's own statusLine
    is left untouched. Backs up before rewriting. Returns True iff it changed
    the file.
    """
    settings_path = settings_path or (Path.home() / ".claude" / "settings.json")
    if not settings_path.exists():
        return False
    original = settings_path.read_text()
    try:
        data: dict = json.loads(original)
    except json.JSONDecodeError:
        return False
    cmd = str((data.get("statusLine") or {}).get("command", ""))
    if "cockpit.cli statusline" not in cmd and "cockpit statusline" not in cmd:
        return False
    data.pop("statusLine", None)
    _backup_settings(settings_path, original)
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"cleared cockpit claude statusLine -> {settings_path}")
    return True


# Matches the interpreter token immediately preceding ` -m cockpit.cli` in a
# baked invocation. `[^\s"']+` stops at the surrounding quote in starship.toml
# (`command = "<interp> -m cockpit.cli starship model"`) so only the path is
# captured, never the quote. The two quoted alternatives come first so an
# interpreter path containing spaces (itself wrapped in its own quotes, distinct
# from starship.toml's outer `command = "..."` quoting) still matches whole.
_COCKPIT_PIN_RE = re.compile(
    r"(?P<interp>\"[^\"]+\"|'[^']+'|[^\s\"']+)(?= -m cockpit\.cli)"
)


def _repin_text(text: str) -> str:
    """Rewrite every `<interp> -m cockpit.cli` interpreter to the running one."""
    return _COCKPIT_PIN_RE.sub(lambda _m: sys.executable, text)


def _repin_starship_config(path: Path) -> bool:
    """Surgically re-pin the interpreter in ~/.config/starship.toml. Returns True
    iff it rewrote the file. Skips a symlink (user-managed) and no-ops when no
    stale pin is present, so unrelated user edits to the toml are untouched."""
    if not path.exists() or path.is_symlink():
        return False
    original = path.read_text()
    if "-m cockpit.cli" not in original:
        return False
    new = _repin_text(original)
    if new == original:
        return False
    _atomic_write_text(path, new)
    print(f"re-pinned starship interpreter -> {path}")
    return True


def _repin_statusline(settings_path: Path) -> bool:
    """Re-pin the interpreter in Claude Code's statusLine, but only when it points
    at cockpit's shim (`-m cockpit.cli statusline`). Returns True iff it rewrote."""
    if not settings_path.exists():
        return False
    try:
        data: dict = json.loads(settings_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    cmd = str((data.get("statusLine") or {}).get("command", ""))
    if "-m cockpit.cli statusline" not in cmd:
        return False
    new_cmd = _repin_text(cmd)
    if new_cmd == cmd:
        return False
    data["statusLine"]["command"] = new_cmd
    _atomic_write_text(settings_path, json.dumps(data, indent=2) + "\n")
    print(f"re-pinned statusLine interpreter -> {settings_path}")
    return True


def repin_interpreter_if_stale() -> None:
    """Heal a stale `{python}` pin after a `brew upgrade`, called on `cockpit
    watch` startup.

    `cockpit setup` bakes `sys.executable` — a *versioned* brew Cellar libexec
    python — into `~/.config/starship.toml` and (under `use_cship`) Claude
    Code's statusLine. `brew upgrade` bumps the Cellar version and deletes the
    old path, so those on-disk pins dangle until the next `setup`. The daemon
    runs this once at startup so the footer heals itself in a single restart
    instead of a manual re-run. Startup-only on purpose: a running daemon's
    `sys.executable` is still the *old* removed path until it restarts, so a
    per-tick re-pin could never see the new interpreter. Surgical — only the
    interpreter prefix of a `... -m cockpit.cli ...` invocation is rewritten, so
    user colour/format edits in starship.toml survive (unlike `install_starship_
    default_config`, which re-seeds the whole file). No-op unless `use_cship`
    and a pin actually differs from the running interpreter.
    """
    if not load_config().get("use_cship"):
        return
    try:
        _repin_starship_config(_starship_user_config_path())
    except Exception as exc:
        print(f"cockpit: starship repin failed: {exc}", file=sys.stderr)
    try:
        _repin_statusline(Path.home() / ".claude" / "settings.json")
    except Exception as exc:
        print(f"cockpit: statusline repin failed: {exc}", file=sys.stderr)


def teardown_claude_integration() -> None:
    """Reverse the Claude-Code-facing writes `cockpit setup` made.

    `brew uninstall cockpit` removes only the Cellar binary; the `~/.claude`
    entries setup wrote live outside the brew prefix and would otherwise dangle
    (hooks/statusLine invoking a missing `cockpit`). This drops those, plus the
    bundled slash commands. It deliberately does **not** touch the
    `~/.config/{cship,starship}.toml` seeds (user-editable, inert without the
    binary) or `~/.config/cockpit` state — those are reported for manual removal.
    """
    changed = uninstall_claude_hooks()
    changed = clear_cockpit_statusline() or changed
    changed = uninstall_claude_commands() or changed
    if not changed:
        print("no cockpit claude integration found — nothing to remove")
    print(
        "left in place (remove by hand if wanted): "
        "~/.config/cship.toml, ~/.config/starship.toml, ~/.config/cockpit"
    )


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
            "Install it with `curl -fsSL https://cship.dev/install.sh | bash` "
            "(macOS + Linux), or set "
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


def _seed_default_toml(
    src: Path, dest: Path, label: str, payload: bytes | None = None
) -> None:
    """Copy `src` to `dest`, replacing a symlink at `dest` with a real file.

    If `dest` is a symlink, its current target is backed up (when the target
    exists) to `<target>.bak.<ts>` and the symlink itself is unlinked before
    writing — otherwise `shutil.copy` would follow the symlink and write
    through to whatever the user had it pointing at, which is exactly the
    scenario that broke this chain in the first place. Regular files are
    compared byte-for-byte against the bundled default; identical files are
    left in place and reported as `unchanged` rather than re-written.

    `payload`, if given, is written instead of `src`'s raw bytes (e.g. after
    token substitution, as `install_starship_default_config` does) — `src`
    still names the source file in the installed/unchanged status line.
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
    _write_if_changed(
        dest, payload if payload is not None else src.read_bytes(), label, src
    )


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
    line_sep = "" if sys.platform == "darwin" else "\n"
    payload = (
        STARSHIP_DEFAULT_TOML.read_text()
        .replace(STARSHIP_PLACEHOLDER, STARSHIP_CMD)
        .replace(STARSHIP_THEME_PLACEHOLDER, resolve_theme())
        .replace(STARSHIP_LINE_SEP_PLACEHOLDER, line_sep)
    ).encode()
    _seed_default_toml(
        STARSHIP_DEFAULT_TOML, _starship_user_config_path(), "starship", payload
    )
