"""Parked-repo preference (`cockpit/lib/hidden.py`).

A leaf module over one JSON file — tested against real files on `tmp_path`
(`HIDDEN_PATH` is redirected there by the autouse `_isolate_hidden_repos`
fixture in `tests/conftest.py`).
"""

from __future__ import annotations

import json

import cockpit.lib.hidden as hidden_mod
from cockpit.lib.hidden import is_hidden, load_hidden, toggle_hidden

# NB: reach the path as `hidden_mod.HIDDEN_PATH`, never a module-level
# `from ... import HIDDEN_PATH` — that binds the real `~/.config/cockpit` path
# by value, so the conftest redirect can't reach it and the test writes to the
# developer's own file (which is exactly what it did until CI, where the parent
# dir doesn't exist, turned the silent write into a FileNotFoundError). The
# function imports are fine: they read the module global at call time.


def test_absent_file_hides_nothing():
    assert load_hidden() == set()
    assert not is_hidden("/some/repo")


def test_toggle_round_trips_and_persists(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert toggle_hidden(repo) is True
    assert is_hidden(repo)
    assert load_hidden() == {str(repo.resolve())}
    assert toggle_hidden(repo) is False
    assert not is_hidden(repo)
    assert load_hidden() == set()


def test_keyed_by_resolved_path_not_the_literal_string(tmp_path):
    # `~`-style and symlinked spellings of the same repo must not double-park it
    # (config paths carry `~`; the TUI passes an absolute path).
    repo = tmp_path / "repo"
    repo.mkdir()
    link = tmp_path / "link"
    link.symlink_to(repo)
    toggle_hidden(link)
    assert is_hidden(repo)
    assert toggle_hidden(repo) is False  # un-parks via the other spelling


def test_two_repos_are_independent(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    toggle_hidden(a)
    toggle_hidden(b)
    toggle_hidden(a)
    assert load_hidden() == {str(b.resolve())}


def test_garbage_file_fails_open(tmp_path):
    # Only test that writes the pref file directly — assert the conftest redirect
    # is live before doing so, or a regression writes to the real ~/.config.
    assert hidden_mod.HIDDEN_PATH.parent == tmp_path
    # A corrupt pref must never make repos silently vanish from the table.
    hidden_mod.HIDDEN_PATH.write_text("{not json")
    assert load_hidden() == set()
    # Right JSON, wrong shape.
    hidden_mod.HIDDEN_PATH.write_text(json.dumps({"repos": []}))
    assert load_hidden() == set()
