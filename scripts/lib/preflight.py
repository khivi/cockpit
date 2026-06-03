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

import os
import shutil
import sys

from .colors import yellow
from .linear import LINEAR_API_KEY_ENV
from .tool import resolve_tool

REQUIRED_BINARIES = ("gh", "git")
CSHIP_BINARIES = ("cship", "starship")


def _die(msg: str) -> None:
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


def _validate_review_prs(cfg: dict) -> None:
    """Hard-fail on a repo `review_prs` that isn't a bool.

    `review_prs: true` makes the daemon create a review worktree for every
    other-authored open PR in the repo — a non-bool (e.g. a stray string) would
    be silently truthy, so it's rejected at start like `sidebar_color`.
    """
    for repo in cfg.get("repos", []):
        if "review_prs" not in repo:
            continue
        if not isinstance(repo["review_prs"], bool):
            name = repo.get("name") or repo.get("path", "?")
            _die(
                f"repo {name!r}: review_prs must be true or false, "
                f"got {repo['review_prs']!r}."
            )


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

    has_linear_repo = any(r.get("linear_keys") for r in cfg.get("repos", []))
    if has_linear_repo and not os.environ.get(LINEAR_API_KEY_ENV):
        print(
            f"{yellow('cockpit:')} a repo sets linear_keys but "
            f"{LINEAR_API_KEY_ENV} is unset — the Linear dev-done pill stays "
            f"off. Export {LINEAR_API_KEY_ENV} to enable it.",
            file=sys.stderr,
            flush=True,
        )


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

    _validate_sidebar_colors(cfg)
    _validate_review_prs(cfg)
    _validate_linear_dev_done(cfg)

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
