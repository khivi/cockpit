from __future__ import annotations

import importlib
import json
import stat
import subprocess
from pathlib import Path


def make_bin_on_path(tmp_path: Path, monkeypatch, *names: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        f = bin_dir / name
        f.write_text("#!/bin/sh\nexit 0\n")
        f.chmod(f.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def make_git_repo(
    tmp_path: Path,
    *,
    branch: str = "feature",
    ahead: int = 0,
    behind: int = 0,
    status: tuple[int, int, int] = (0, 0, 0),
) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", branch)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f").write_text("x")
    _git(repo, "add", "f")
    _git(repo, "commit", "-q", "-m", "init")

    staged, unstaged, untracked = status
    if unstaged > 0:
        for i in range(unstaged):
            (repo / f"m{i}").write_text("orig")
            _git(repo, "add", f"m{i}")
        _git(repo, "commit", "-q", "-m", "tracked")

    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-q", "-b", branch, str(origin))
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", branch)

    if behind > 0:
        scratch = tmp_path / "scratch"
        _git(tmp_path, "clone", "-q", str(origin), str(scratch))
        _git(scratch, "config", "user.email", "t@t")
        _git(scratch, "config", "user.name", "t")
        for i in range(behind):
            (scratch / f"b{i}").write_text(str(i))
            _git(scratch, "add", f"b{i}")
            _git(scratch, "commit", "-q", "-m", f"b{i}")
        _git(scratch, "push", "-q", "origin", branch)
        _git(repo, "fetch", "-q", "origin")

    for i in range(ahead):
        (repo / f"a{i}").write_text(str(i))
        _git(repo, "add", f"a{i}")
        _git(repo, "commit", "-q", "-m", f"a{i}")

    if unstaged > 0:
        for i in range(unstaged):
            (repo / f"m{i}").write_text("dirty")
    for i in range(staged):
        (repo / f"s{i}").write_text(str(i))
        _git(repo, "add", f"s{i}")
    for i in range(untracked):
        (repo / f"u{i}").write_text(str(i))

    return repo


def setup_cockpit_config(tmp_path: Path, monkeypatch, cfg: dict):
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(cfg))

    import scripts.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    return cockpit_config
