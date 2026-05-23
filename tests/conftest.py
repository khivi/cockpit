"""Shared fixtures: tmp git repo with origin remote + isolated COCKPIT_HOME."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


_GIT_ENV_LEAKS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
)


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    res = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return res.stdout.strip()


@dataclass
class RepoFixture:
    repo: Path  # local clone with origin remote set
    origin: Path  # bare repo serving as origin
    cockpit_home: Path
    repo_name: str = "testrepo"
    branch_prefix: str = "khivi/"
    default_base: str = "main"


@pytest.fixture
def cockpit_repo(tmp_path, monkeypatch) -> RepoFixture:
    """Tmp local git repo with `origin` set to a bare repo, plus a fake
    cockpit config.json pointing at it. `main` exists on both sides.

    Strips GIT_* env vars (GIT_INDEX_FILE etc.) so test subprocesses can't
    corrupt the outer repo's staged index when run under a pre-commit hook.
    """
    for var in _GIT_ENV_LEAKS:
        monkeypatch.delenv(var, raising=False)
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    cockpit_home = tmp_path / "cockpit-home"
    cockpit_home.mkdir()

    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "init", "-b", "main", str(repo))
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "seed")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")

    cfg = {
        "repos": [
            {
                "name": "testrepo",
                "path": str(repo),
                "branch_prefix": "khivi/",
                "default_base": "main",
            }
        ],
        "poll_interval_seconds": 300,
        "auto_cleanup_on_merge": True,
    }
    (cockpit_home / "config.json").write_text(json.dumps(cfg))
    monkeypatch.setenv("COCKPIT_HOME", str(cockpit_home))

    # COCKPIT_HOME is read at module-import time in lib.config; reload so the
    # env override actually takes effect.
    import importlib
    import scripts.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    # spawn imports find_repo_by_name from lib.config; refresh that binding.
    import scripts.spawn as spawn

    importlib.reload(spawn)

    return RepoFixture(repo=repo, origin=origin, cockpit_home=cockpit_home)


@pytest.fixture
def push_branch(cockpit_repo):
    """Push a fresh branch (off `main` by default) to origin and prune locally.

    Returns a callable: push_branch(name, base="main") -> None.
    """

    def _push(name: str, base: str = "main") -> None:
        _git(cockpit_repo.repo, "branch", name, base)
        _git(cockpit_repo.repo, "push", "origin", f"{name}:{name}")
        _git(cockpit_repo.repo, "branch", "-D", name)

    return _push
