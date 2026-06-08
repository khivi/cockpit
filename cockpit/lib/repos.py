"""Inspect configured cockpit repos.

Repo listing lives in the `cockpit watch` TUI now (the `r` key shows the
selected repo's config, and the Ctrl+P palette has "Show config: all repos"),
so there's no standalone `repos` subcommand — only this `repo_names` helper that
`spawn.py` uses to suggest valid repos.
"""

from __future__ import annotations

from .config import load_config


def repo_names() -> list[str]:
    """Return the names of all configured repos, in config order."""
    return [r.get("name", "") for r in load_config().get("repos", []) if r.get("name")]
