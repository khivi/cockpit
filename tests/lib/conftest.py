"""Shared fixtures for tests/lib/: tmpdir-scoped FLAT_CACHE_DIR + clean GIT_ env."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import scripts.lib.cache as cache_mod


@pytest.fixture
def cache_dir(tmp_path, monkeypatch) -> Iterator[Path]:
    """Redirect FLAT_CACHE_DIR to a tmpdir for the duration of one test."""
    cdir = tmp_path / "cockpit-cache"
    cdir.mkdir()
    monkeypatch.setattr(cache_mod, "FLAT_CACHE_DIR", cdir)
    yield cdir


@pytest.fixture
def _clean_git_env(monkeypatch) -> None:
    """Strip ambient GIT_* env vars so git commands target the tmpdir's
    repo (or no repo) instead of inheriting the caller's repo. Pre-commit
    in particular exports GIT_DIR pointing at the host repo, which makes
    `git -C tmp_path log` return the host's HEAD instead of failing
    cleanly when tmp_path is not a repo."""
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(var, raising=False)
