"""Welcome-hint marker (`cockpit/lib/firstrun.py`).

Every test runs against a `tmp_path` marker, never the developer's own
(`WELCOME_MARKER` is redirected there by the autouse `_isolate_welcome_marker`
fixture in `tests/conftest.py`).
"""

from __future__ import annotations

import cockpit.lib.firstrun as firstrun_mod
from cockpit.lib.firstrun import mark_welcomed, welcome_pending

# NB: reach the path as `firstrun_mod.WELCOME_MARKER`, never a module-level
# `from ... import WELCOME_MARKER` — that binds the real `~/.config/cockpit`
# path before the fixture can redirect it.


def test_pending_on_a_fresh_install():
    assert welcome_pending()


def test_marking_clears_pending():
    mark_welcomed()
    assert not welcome_pending()


def test_marking_is_idempotent():
    mark_welcomed()
    mark_welcomed()
    assert not welcome_pending()


def test_marking_creates_a_missing_state_dir():
    """`$COCKPIT_HOME` may not exist yet on a first run — the marker write has to
    make it rather than raising (the same `mkdir(parents=True)` `hidden.py`
    does)."""
    firstrun_mod.WELCOME_MARKER = (
        firstrun_mod.WELCOME_MARKER.parent / "nested" / "deeper" / "welcomed"
    )
    mark_welcomed()
    assert not welcome_pending()


def test_an_unwritable_home_fails_open_and_never_raises(tmp_path):
    """A failed write must cost one repeat of the hint, not a crashed startup:
    `_maybe_welcome` calls this before the toast, so a raise would abort
    `on_mount`."""
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("")
    firstrun_mod.WELCOME_MARKER = blocked / "welcomed"

    mark_welcomed()  # must not raise

    assert welcome_pending()
