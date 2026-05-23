"""Tests for scripts/lib/preflight.preflight().

Verifies the unified dependency check that runs at the top of every
`cockpit.py` invocation: hard-fails on missing required binaries, soft-warns
on missing workspace backend.
"""

from __future__ import annotations

import pytest

from scripts.lib.preflight import preflight
from tests.fixtures import make_bin_on_path


def _all_required(tmp_path, monkeypatch) -> None:
    make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cmux")


def test_preflight_passes_when_required_bins_present(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux"})
    assert capsys.readouterr().err == ""


def test_preflight_exits_when_gh_missing(tmp_path, monkeypatch, capsys):
    make_bin_on_path(tmp_path, monkeypatch, "git", "cmux")
    with pytest.raises(SystemExit) as exc:
        preflight({})
    assert exc.value.code == 2
    assert "`gh` not found on PATH" in capsys.readouterr().err


def test_preflight_exits_when_git_missing(tmp_path, monkeypatch, capsys):
    make_bin_on_path(tmp_path, monkeypatch, "gh", "cmux")
    with pytest.raises(SystemExit) as exc:
        preflight({})
    assert exc.value.code == 2
    assert "`git` not found on PATH" in capsys.readouterr().err


def test_preflight_exits_when_use_cship_and_cship_missing(
    tmp_path, monkeypatch, capsys
):
    make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cmux", "starship")
    with pytest.raises(SystemExit) as exc:
        preflight({"use_cship": True})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "use_cship=true" in err
    assert "`cship`" in err


def test_preflight_exits_when_use_cship_and_starship_missing(
    tmp_path, monkeypatch, capsys
):
    make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cmux", "cship")
    with pytest.raises(SystemExit) as exc:
        preflight({"use_cship": True})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "use_cship=true" in err
    assert "`starship`" in err


def test_preflight_skips_cship_check_when_use_cship_false(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    preflight({"use_cship": False})
    assert capsys.readouterr().err == ""


def test_preflight_warns_when_only_limux_present(tmp_path, monkeypatch, capsys):
    make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "limux")
    preflight({"tool": "auto"})
    err = capsys.readouterr().err
    assert "cmux not found — using limux" in err


def test_preflight_warns_when_no_workspace_backend(tmp_path, monkeypatch, capsys):
    make_bin_on_path(tmp_path, monkeypatch, "gh", "git")
    preflight({"tool": "auto"})
    err = capsys.readouterr().err
    assert "no workspace tool on PATH" in err


def test_preflight_silent_when_tool_explicitly_set(tmp_path, monkeypatch, capsys):
    make_bin_on_path(tmp_path, monkeypatch, "gh", "git")
    preflight({"tool": "none"})
    assert capsys.readouterr().err == ""
