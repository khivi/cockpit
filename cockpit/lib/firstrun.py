"""The one-time welcome hint's "already shown" marker.

An empty file at `$COCKPIT_HOME/welcomed`: present means the TUI has pointed the
user at the feature guide once and must not do it again. Same shape as
`hidden.py` — a user-facing preference persisted *outside* `config.json`, so it
never rewrites a hand-edited config.

Deliberately not a config field: it is app state nobody sets by hand, and a
`config.json` key would cost the three-face schema sync (reader +
`config.example.json` + `docs/config.md`) for a boolean with no reader but this
one.

It fails **open** — an unwritable state dir means the hint shows again next
launch, which is a harmless repeat, where the inverse (swallowing a one-shot
hint forever on a transient error) is silent and unrecoverable.
"""

from __future__ import annotations

from .config import COCKPIT_HOME

WELCOME_MARKER = COCKPIT_HOME / "welcomed"


def welcome_pending() -> bool:
    """True when the welcome hint has not been shown yet."""
    return not WELCOME_MARKER.exists()


def mark_welcomed() -> None:
    """Record that the hint has been shown. Idempotent, and never raises — a
    failed write only costs one repeat of the hint."""
    try:
        WELCOME_MARKER.parent.mkdir(parents=True, exist_ok=True)
        WELCOME_MARKER.touch()
    except OSError:
        pass
