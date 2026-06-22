"""Register the cwd's main repo in cockpit's config.json.

Auto-detects repo root (via git.main_worktree_path), gh user (branch prefix),
and default branch (via gh.default_branch). Idempotent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

from . import config  # reference config.CONFIG_PATH dynamically (reload-safe)
from .config import ensure_state_dirs
from .gh import default_branch, gh_self_user
from .git import main_worktree_path


def repo_root() -> Path:
    path = main_worktree_path()
    if path is None:
        raise RuntimeError("not in a git repo — cd into the main repo first")
    return path


def _prompt_branch_prefix(default: str) -> str:
    if not sys.stdin.isatty():
        return default
    hint = f"[{default}]" if default else "[]"
    try:
        resp = input(
            f"branch prefix {hint} (enter to accept, '-' for no prefix): "
        ).strip()
    except EOFError:
        return default
    if resp == "":
        return default
    if resp == "-":
        return ""
    return resp


def register_cwd(in_place: bool = False) -> dict:
    """Append cwd's repo to config.json if not already present. Returns the entry.

    `in_place=True` marks the entry `"in_place": true` and skips the interactive
    branch-prefix prompt (an in-place repo never has worktree branches spawned
    for it, so the prefix is irrelevant). It's the bare-`cockpit new` path: the
    daemon shows the repo's row but `_spawn_missing_workspaces` early-returns,
    never auto-creating worktrees. An already-registered repo is returned
    untouched — bare `cockpit new` in a normal managed repo does NOT flip it to
    in-place.
    """
    ensure_state_dirs()
    repo = repo_root().resolve()
    try:
        gh_user = gh_self_user()
    except RuntimeError:
        gh_user = ""
    base = default_branch(repo)
    name = repo.name

    cfg: dict
    if config.CONFIG_PATH.exists():
        with config.CONFIG_PATH.open() as f:
            cfg = json.load(f)
    else:
        cfg = {
            "repos": [],
            "slow_poll_interval_seconds": 300,
            "fast_poll_interval_seconds": 30,
        }

    repos = cfg.setdefault("repos", [])
    for r in repos:
        if Path(r["path"]).expanduser().resolve() == repo:
            print(f"already managed: {repo}")
            return cast(dict, r)

    default_prefix = f"{gh_user}/" if gh_user else ""
    branch_prefix = (
        default_prefix if in_place else _prompt_branch_prefix(default_prefix)
    )

    entry: dict = {
        "name": name,
        "path": str(repo),
        "branch_prefix": branch_prefix,
        "default_base": base,
    }
    if in_place:
        entry["in_place"] = True
    repos.append(entry)
    with config.CONFIG_PATH.open("w") as f:
        json.dump(cfg, f, indent=2)
    print(
        f"added repo: {name} at {repo} (prefix={entry['branch_prefix']}, base={base})"
    )
    return entry
