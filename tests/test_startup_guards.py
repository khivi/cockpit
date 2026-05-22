"""Startup-time tool presence guards.

`cockpit --watch` / `--once` must exit with a structured one-liner when
`gh` or `git` is missing from PATH, rather than crashing with a FileNotFoundError
deep inside a daemon cycle.
"""

from __future__ import annotations

import subprocess

import pytest

from lib.gh import require_gh
from lib.git import require_git


def _raise_fnf(*_args, **_kwargs):
    raise FileNotFoundError


def _ok_completed(*_args, **_kwargs):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")


def test_require_gh_exits_when_missing(monkeypatch, capsys):
    monkeypatch.setattr("lib.gh.subprocess.run", _raise_fnf)
    with pytest.raises(SystemExit) as excinfo:
        require_gh()
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "gh" in err
    assert "https://cli.github.com" in err


def test_require_gh_returns_when_present(monkeypatch):
    monkeypatch.setattr("lib.gh.subprocess.run", _ok_completed)
    require_gh()


def test_require_git_exits_when_missing(monkeypatch, capsys):
    monkeypatch.setattr("lib.git.subprocess.run", _raise_fnf)
    with pytest.raises(SystemExit) as excinfo:
        require_git()
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "git" in err
    assert "https://git-scm.com" in err


def test_require_git_returns_when_present(monkeypatch):
    monkeypatch.setattr("lib.git.subprocess.run", _ok_completed)
    require_git()
