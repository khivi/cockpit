"""Single dependency preflight, called from every `cockpit.py` invocation.

Hard-fails (sys.exit(2)) on missing required binaries:
  - `gh`, `git` — always
  - `cship`, `starship` — when `use_cship: true`

Soft-warns (stderr only) on missing optional backend:
  - `cmux` / `limux` — drops cockpit into cache-only mode

The `spawn.py` entry script still calls `require_workspace_binary()` from
`lib.cmux` for its own backend-mandatory gate; that's a stricter policy than
the daemon needs.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import NoReturn

from .colors import yellow
from .linear import LINEAR_API_KEY_ENV
from .tool import resolve_tool

REQUIRED_BINARIES = ("gh", "git")
CSHIP_BINARIES = ("cship", "starship")


def _die(msg: str) -> NoReturn:
    print(f"cockpit: {msg}", file=sys.stderr, flush=True)
    sys.exit(2)


def _validate_sidebar_colors(cfg: dict) -> None:
    """Hard-fail on a repo `sidebar_color` that isn't a cmux color name.

    The field is cosmetic, but a typo is caught here (like the use_cship gate)
    so it surfaces at daemon start with the valid set listed — rather than as a
    silent no-tint discovered cycles later. cmux is imported lazily to keep
    preflight's import graph to the stdlib + leaf colors/tool modules.
    """
    from .cmux import WORKSPACE_COLORS

    for repo in cfg.get("repos", []):
        color = repo.get("sidebar_color")
        if color is None:
            continue
        if color not in WORKSPACE_COLORS:
            name = repo.get("name") or repo.get("path", "?")
            _die(
                f"repo {name!r}: sidebar_color {color!r} is not a cmux color. "
                f"Choose one of: {', '.join(sorted(WORKSPACE_COLORS))}."
            )


def _validate_repo_bool(cfg: dict, key: str) -> None:
    """Hard-fail on a per-repo `key` that's present but isn't a bool.

    These per-repo switches (`review_prs` spawns review worktrees, `in_place`
    skips all auto-spawning, `dependabot`/`review_external` opt a `review_prs`
    repo into spawning for dependabot / non-collaborator PRs) gate daemon
    behavior, so a non-bool (e.g. a stray string) would be silently truthy —
    rejected at start like `sidebar_color`.
    """
    for repo in cfg.get("repos", []):
        if key in repo and not isinstance(repo[key], bool):
            name = repo.get("name") or repo.get("path", "?")
            _die(f"repo {name!r}: {key} must be true or false, got {repo[key]!r}.")


def _validate_global_bool(cfg: dict, key: str) -> None:
    """Hard-fail on a top-level `key` that's present but isn't a bool.

    `check_update` (gates the new-version log line) and `use_slack` (gates the
    Slack-MCP-fetch spawn prompt) both default true/false and gate daemon
    behavior, so a non-bool would be silently truthy — rejected like `review_prs`.
    """
    if key in cfg and not isinstance(cfg[key], bool):
        _die(f"{key} must be true or false, got {cfg[key]!r}.")


def _validate_review_command(cfg: dict) -> None:
    """Hard-fail on a `review_command` (global or per-repo) that isn't a slash
    command string.

    `review_command` overrides the `/review` first-turn seeded into a
    `review_prs` worktree (e.g. `/pr-review`). It is delivered verbatim as the
    workspace's opening prompt, so a non-string or a value missing the leading
    `/` would silently seed a non-command — rejected at start like `review_prs`.
    """

    def _check(val: object, where: str) -> None:
        if not isinstance(val, str) or not val.startswith("/"):
            _die(
                f"{where}: review_command must be a slash command string "
                f"(e.g. '/review'), got {val!r}."
            )

    if "review_command" in cfg:
        _check(cfg["review_command"], "review_command")
    for repo in cfg.get("repos", []):
        if "review_command" not in repo:
            continue
        name = repo.get("name") or repo.get("path", "?")
        _check(repo["review_command"], f"repo {name!r}: review_command")


def _validate_tickets(cfg: dict) -> None:
    """Validate the `tickets` config (top-level *and* per-repo).

    `tickets` is the single provider selector that replaced the old boolean
    `use_linear`. It is either the bare string ``none|linear|github`` (shorthand)
    or an object whose accepted fields are owned by each provider — the schema
    lives in `linear.py` / `github_issues.py` (`CONFIG_FIELDS`) and is composed +
    type-checked by `tickets.tickets_field_errors`, which also rejects a field
    that doesn't belong to the chosen provider (a silent typo would otherwise
    disable that setting). Validated here so it surfaces at daemon start.
    """
    from .config import VALID_TICKETS
    from .tickets import tickets_field_errors

    def _check_block(val: object, where: str) -> None:
        if isinstance(val, str):
            provider: object = val
            block: dict = {}
        elif isinstance(val, dict):
            provider = val.get("provider", "none")
            block = val
        else:
            _die(
                f"{where}: tickets must be one of {', '.join(VALID_TICKETS)} "
                f"(or an object with a `provider`), got {val!r}."
            )
        if provider not in VALID_TICKETS:
            _die(
                f"{where}: tickets provider must be one of "
                f"{', '.join(VALID_TICKETS)}, got {provider!r}."
            )
        for err in tickets_field_errors(block, str(provider)):
            _die(f"{where}: {err}")

    if "tickets" in cfg:
        _check_block(cfg["tickets"], "tickets")
    for repo in cfg.get("repos", []):
        if "tickets" not in repo:
            continue
        name = repo.get("name") or repo.get("path", "?")
        _check_block(repo["tickets"], f"repo {name!r}")

    if "use_linear" in cfg:
        _die(
            "use_linear was replaced by the `tickets` config "
            "(set `tickets: linear`, or `tickets: {provider: linear, ...}`)."
        )


def _validate_orphan_nudge_grace(cfg: dict) -> None:
    """Hard-fail on an `orphan_nudge_grace_hours` (top-level *or* per-repo) that
    isn't a non-negative number.

    It sets how long a no-open-PR worktree is spared the "push or close" nudge
    after creation (`config.orphan_nudge_grace_seconds`). A non-numeric value
    would be silently clamped to the default, and a negative one is nonsensical
    (it'd never grace), so both are rejected at start like `review_prs`. `0`
    (disable grace) is allowed.
    """

    def _check(val: object, where: str) -> None:
        if isinstance(val, bool) or not isinstance(val, int | float):
            _die(f"{where}: orphan_nudge_grace_hours must be a number, got {val!r}.")
        if val < 0:
            _die(f"{where}: orphan_nudge_grace_hours must be >= 0, got {val!r}.")

    if "orphan_nudge_grace_hours" in cfg:
        _check(cfg["orphan_nudge_grace_hours"], "orphan_nudge_grace_hours")
    for repo in cfg.get("repos", []):
        if "orphan_nudge_grace_hours" not in repo:
            continue
        name = repo.get("name") or repo.get("path", "?")
        _check(repo["orphan_nudge_grace_hours"], f"repo {name!r}")


def _validate_linear_dev_done(cfg: dict) -> None:
    """Validate the dev-done pill config and warn on a missing API key.

    `linear_dev_done_state`, when present, must be a string (a non-string would
    silently never match a Linear state name) — rejected like `sidebar_color`.

    Then, if any repo is Linear-configured (`linear_keys`) but `LINEAR_API_KEY`
    is unset, the daemon can't query Linear, so the `devdone=` pill silently
    stays off. That's a soft degrade, not a config error — warn once at start so
    it isn't a mystery cycles later.
    """
    state = cfg.get("linear_dev_done_state")
    if state is not None and not isinstance(state, str):
        _die(f"linear_dev_done_state must be a string, got {state!r}.")

    from .config import linear_team_keys

    has_linear_repo = any(linear_team_keys(cfg, r) for r in cfg.get("repos", []))
    if has_linear_repo and not os.environ.get(LINEAR_API_KEY_ENV):
        print(
            f"{yellow('cockpit:')} a repo sets Linear team keys but "
            f"{LINEAR_API_KEY_ENV} is unset — the Linear dev-done pill stays "
            f"off. Export {LINEAR_API_KEY_ENV} to enable it.",
            file=sys.stderr,
            flush=True,
        )


def _validate_linear_done_on_merge(cfg: dict) -> None:
    """Validate the merge-transition config and warn on a missing API key.

    `linear_done_on_merge` (top-level *and* per-repo) must be a bool — a stray
    truthy string would silently enable a Linear *write*, so it's rejected like
    `review_prs`. `linear_merge_done_state`, when present, must be a string.

    Then, if the feature is enabled anywhere (global or any repo) but
    `LINEAR_API_KEY` is unset, the daemon can't perform the transition — warn
    once (soft degrade, not an error), matching `_validate_linear_dev_done`.
    """
    top = cfg.get("linear_done_on_merge")
    if top is not None and not isinstance(top, bool):
        _die(f"linear_done_on_merge must be true or false, got {top!r}.")

    state = cfg.get("linear_merge_done_state")
    if state is not None and not isinstance(state, str):
        _die(f"linear_merge_done_state must be a string, got {state!r}.")

    enabled = bool(top)
    for repo in cfg.get("repos", []):
        val = repo.get("linear_done_on_merge")
        if val is None:
            continue
        if not isinstance(val, bool):
            name = repo.get("name") or repo.get("path", "?")
            _die(
                f"repo {name!r}: linear_done_on_merge must be true or false, "
                f"got {val!r}."
            )
        enabled = enabled or val

    if enabled and not os.environ.get(LINEAR_API_KEY_ENV):
        print(
            f"{yellow('cockpit:')} linear_done_on_merge is enabled but "
            f"{LINEAR_API_KEY_ENV} is unset — linked tickets won't transition "
            f"on merge. Export {LINEAR_API_KEY_ENV} to enable it.",
            file=sys.stderr,
            flush=True,
        )


def _warn_cockpit_not_on_path() -> None:
    """Soft-warn when the `cockpit` console script isn't on PATH.

    The daemon itself runs fine via `python -m cockpit.cli`, and the seeded
    statusline/starship commands use the interpreter + module dispatch — but the
    `/cockpit:*` slash-commands and the Stop-hook statusline invoke the bare
    `cockpit` console script, which needs it on PATH. Warn once at start so a
    missing install surfaces here, not as an opaque command-not-found later.
    """
    if shutil.which("cockpit") is None:
        print(
            f"{yellow('cockpit:')} the `cockpit` command is not on PATH. The "
            "daemon runs, but the /cockpit:* slash-commands and the statusline "
            "hook invoke it directly. Install with `uv tool install cockpit` "
            "(or run via `uvx cockpit`).",
            file=sys.stderr,
            flush=True,
        )


def validate_config(cfg: dict) -> None:
    """Run every config-shape validator (no binary/PATH checks).

    Split out of `preflight` so the shipped `config.example.json` — which is
    both the documented schema *and* the file copied as a new user's config on
    first run (`config.py`) — can be asserted valid in CI without a real
    toolchain on PATH. Add a new `_validate_*` here and the example-config test
    covers it automatically.
    """
    _validate_sidebar_colors(cfg)
    _validate_repo_bool(cfg, "review_prs")
    _validate_repo_bool(cfg, "in_place")
    _validate_repo_bool(cfg, "dependabot")
    _validate_repo_bool(cfg, "review_external")
    _validate_review_command(cfg)
    _validate_global_bool(cfg, "check_update")
    _validate_global_bool(cfg, "use_slack")
    _validate_tickets(cfg)
    _validate_orphan_nudge_grace(cfg)
    _validate_linear_dev_done(cfg)
    _validate_linear_done_on_merge(cfg)


def preflight(cfg: dict) -> None:
    for binary in REQUIRED_BINARIES:
        if shutil.which(binary) is None:
            _die(f"`{binary}` not found on PATH (required)")

    _warn_cockpit_not_on_path()

    if cfg.get("use_cship"):
        for binary in CSHIP_BINARIES:
            if shutil.which(binary) is None:
                _die(
                    f"use_cship=true but `{binary}` is not on PATH. "
                    f"Install {binary} or set use_cship=false in your config."
                )

    validate_config(cfg)

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
