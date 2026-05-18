"""Register the cwd's main repo in cockpit's config.json.

Auto-detects repo root (resolves worktrees to main), gh user (branch prefix),
and default branch. Idempotent.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import run
from .config import CONFIG_PATH, ensure_state_dirs
from .git import main_worktree_path


def repo_root() -> Path:
    path = main_worktree_path()
    if path is None:
        raise RuntimeError("not in a git repo — cd into the main repo first")
    return path


def default_branch(repo: Path) -> str:
    res = subprocess.run(
        [
            "gh",
            "repo",
            "view",
            "--json",
            "defaultBranchRef",
            "--jq",
            ".defaultBranchRef.name",
        ],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    out = run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        check=False,
    ).strip()
    return out.removeprefix("origin/") if out else "main"


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


def register_cwd() -> dict:
    """Append cwd's repo to config.json if not already present. Returns the entry."""
    ensure_state_dirs()
    repo = repo_root().resolve()
    gh_user = run(["gh", "api", "user", "--jq", ".login"], check=False).strip()
    base = default_branch(repo)
    name = repo.name

    cfg: dict
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open() as f:
            cfg = json.load(f)
    else:
        cfg = {
            "repos": [],
            "poll_interval_seconds": 300,
            "auto_cleanup_on_merge": False,
        }

    repos = cfg.setdefault("repos", [])
    for r in repos:
        if Path(r["path"]).expanduser().resolve() == repo:
            print(f"already managed: {repo}")
            return r

    default_prefix = f"{gh_user}/" if gh_user else ""
    branch_prefix = _prompt_branch_prefix(default_prefix)

    entry = {
        "name": name,
        "path": str(repo),
        "branch_prefix": branch_prefix,
        "default_base": base,
    }
    repos.append(entry)
    with CONFIG_PATH.open("w") as f:
        json.dump(cfg, f, indent=2)
    print(
        f"added repo: {name} at {repo} (prefix={entry['branch_prefix']}, base={base})"
    )
    return entry
