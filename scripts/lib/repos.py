"""Render and inspect configured cockpit repos."""

from __future__ import annotations

from .config import load_config


def repo_names() -> list[str]:
    """Return the names of all configured repos, in config order."""
    return [r.get("name", "") for r in load_config().get("repos", []) if r.get("name")]


def render_repos() -> int:
    repos = load_config().get("repos", [])
    if not repos:
        print(
            "no repos configured; run `/cockpit:new` from inside a git repo "
            "to auto-register, or edit ~/.config/cockpit/config.json"
        )
        return 0

    header = ("NAME", "PATH", "BRANCH_PREFIX", "DEFAULT_BASE")
    rows = [header]
    for r in repos:
        rows.append(
            (
                str(r.get("name", "-")),
                str(r.get("path", "-")),
                str(r.get("branch_prefix", "")),
                str(r.get("default_base", "main")),
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return 0
