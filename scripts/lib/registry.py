"""Register the cwd's main repo in cockpit's config.json.

Auto-detects repo root (resolves worktrees to main), gh user (branch prefix),
and default branch. Idempotent.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import run
from .config import CONFIG_PATH, ensure_state_dirs


def repo_root() -> Path:
    out = run(["git", "worktree", "list", "--porcelain"], check=False)
    for line in out.splitlines():
        if line.startswith("worktree "):
            return Path(line.split(" ", 1)[1])
    raise RuntimeError("not in a git repo — cd into the main repo first")


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

    entry = {
        "name": name,
        "path": str(repo),
        "branch_prefix": f"{gh_user}/" if gh_user else "",
        "default_base": base,
    }
    repos.append(entry)
    with CONFIG_PATH.open("w") as f:
        json.dump(cfg, f, indent=2)
    print(
        f"added repo: {name} at {repo} (prefix={entry['branch_prefix']}, base={base})"
    )
    return entry
