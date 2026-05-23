"""`lib.run` must translate FileNotFoundError into a structured fatal exit.

Without this, a missing binary (e.g. `gh`, `git`) surfaces as a cryptic
Python traceback deep inside a daemon cycle. The wrapper is the single
chokepoint for every subprocess call routed through it, so every caller
benefits without per-tool guard scaffolding.
"""

from __future__ import annotations

import pytest

import scripts.lib as lib_pkg


def _raise_fnf(*_args, **_kwargs):
    raise FileNotFoundError


def test_run_exits_on_missing_gh_with_install_hint(capsys, monkeypatch):
    monkeypatch.setattr(lib_pkg.subprocess, "run", _raise_fnf)
    with pytest.raises(SystemExit) as excinfo:
        lib_pkg.run(["gh", "--version"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "'gh' not found on PATH" in err
    assert "https://cli.github.com" in err


def test_run_exits_on_missing_git_with_install_hint(capsys, monkeypatch):
    monkeypatch.setattr(lib_pkg.subprocess, "run", _raise_fnf)
    with pytest.raises(SystemExit) as excinfo:
        lib_pkg.run(["git", "status"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "'git' not found on PATH" in err
    assert "https://git-scm.com" in err


def test_run_exits_on_missing_cship_with_install_hint(capsys, monkeypatch):
    monkeypatch.setattr(lib_pkg.subprocess, "run", _raise_fnf)
    with pytest.raises(SystemExit) as excinfo:
        lib_pkg.run(["cship"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "'cship' not found on PATH" in err
    assert "https://github.com/khivi/cship" in err


def test_run_exits_on_missing_starship_with_install_hint(capsys, monkeypatch):
    monkeypatch.setattr(lib_pkg.subprocess, "run", _raise_fnf)
    with pytest.raises(SystemExit) as excinfo:
        lib_pkg.run(["starship"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "'starship' not found on PATH" in err
    assert "https://starship.rs" in err


def test_run_exits_on_missing_unknown_binary_without_hint(capsys, monkeypatch):
    monkeypatch.setattr(lib_pkg.subprocess, "run", _raise_fnf)
    with pytest.raises(SystemExit) as excinfo:
        lib_pkg.run(["nonexistent-binary"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "'nonexistent-binary' not found on PATH" in err
    assert "install from" not in err


def test_run_returns_stdout_on_success():
    out = lib_pkg.run(["printf", "hello"])
    assert out == "hello"


def test_run_raises_runtime_error_on_nonzero_exit():
    with pytest.raises(RuntimeError):
        lib_pkg.run(["sh", "-c", "exit 1"])


def test_run_check_false_swallows_nonzero():
    lib_pkg.run(["sh", "-c", "exit 1"], check=False)
