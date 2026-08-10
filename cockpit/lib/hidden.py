"""Which managed repos the user has parked ("hidden") — a user preference, not
config.

One JSON file at `$COCKPIT_HOME/hidden-repos.json`, a list of resolved repo
paths. Same shape as `nudges.py`'s per-PR mute: a TUI toggle (`h`) the daemon
also reads, persisted outside `config.json` so parking a repo never rewrites the
user's hand-edited config (and so a repo stays registered — hiding is not
unregistering).

Keyed by *resolved path* rather than the config `name` label, which is arbitrary
and mutable (rename the label and the hide would silently forget).

A hidden repo is dormant, not just invisible: `cycle_all` skips it entirely, so
it costs no `gh` round-trip and gets no auto-spawn / nudge / ticket write. The
fast tick still refreshes its local git cells (network-free), so an un-hide
paints correctly and any workspace still open on it keeps a live statusline.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import COCKPIT_HOME

HIDDEN_PATH = COCKPIT_HOME / "hidden-repos.json"


def _key(repo_path: str | Path) -> str:
    return str(Path(os.path.expanduser(str(repo_path))).resolve())


def load_hidden() -> set[str]:
    """The set of hidden repo keys. Unreadable/garbage file → nothing hidden
    (fail open: a corrupt pref must never make repos vanish silently)."""
    try:
        data = json.loads(HIDDEN_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    return {str(p) for p in data} if isinstance(data, list) else set()


def is_hidden(repo_path: str | Path) -> bool:
    return _key(repo_path) in load_hidden()


def toggle_hidden(repo_path: str | Path) -> bool:
    """Flip a repo's hidden state and persist. Returns the new state."""
    key = _key(repo_path)
    hidden = load_hidden()
    now_hidden = key not in hidden
    hidden.symmetric_difference_update({key})
    HIDDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    HIDDEN_PATH.write_text(json.dumps(sorted(hidden), indent=2) + "\n")
    return now_hidden
