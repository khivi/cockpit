"""The one-time welcome hint's "already shown" marker.

An empty file at `$COCKPIT_HOME/state/welcomed`: present means the TUI has
pointed the user at the feature guide once and must not do it again.

**Under `state/`, not at the `$COCKPIT_HOME` root.** The root is what a user
opens — `config.json`, the logs, `hidden-repos.json` — while `state/` is the
daemon's own durable, non-derived bookkeeping (its only other resident is
`close-requests/`, the queued teardown markers). This is an internal breadcrumb
nobody will ever read, so it belongs with the latter. It emphatically does not
belong under `cache/`: that tree is derived and safe to wipe, and a wipe
re-showing a one-shot hint would make the marker meaningless.

Deliberately not a config field either: it is app state nobody sets by hand, and
a `config.json` key would cost the three-face schema sync (reader +
`config.example.json` + `docs/config.md`) for a boolean with no reader but this
one.

It fails **open** — an unwritable state dir means the hint shows again next
launch, which is a harmless repeat, where the inverse (swallowing a one-shot
hint forever on a transient error) is silent and unrecoverable.
"""

from __future__ import annotations

from .config import COCKPIT_HOME

WELCOME_MARKER = COCKPIT_HOME / "state" / "welcomed"


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
