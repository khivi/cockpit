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
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "git", "cmux")
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(SystemExit) as exc:
        preflight({})
    assert exc.value.code == 2
    assert "`gh` not found on PATH" in capsys.readouterr().err


def test_preflight_exits_when_git_missing(tmp_path, monkeypatch, capsys):
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "cmux")
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(SystemExit) as exc:
        preflight({})
    assert exc.value.code == 2
    assert "`git` not found on PATH" in capsys.readouterr().err


def test_preflight_exits_when_use_cship_and_cship_missing(
    tmp_path, monkeypatch, capsys
):
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cmux", "starship")
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(SystemExit) as exc:
        preflight({"use_cship": True})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "use_cship=true" in err
    assert "`cship`" in err


def test_preflight_exits_when_use_cship_and_starship_missing(
    tmp_path, monkeypatch, capsys
):
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cmux", "cship")
    monkeypatch.setenv("PATH", str(bin_dir))
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
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "limux")
    monkeypatch.setenv("PATH", str(bin_dir))
    preflight({"tool": "auto"})
    err = capsys.readouterr().err
    assert "cmux not found — using limux" in err


def test_preflight_warns_when_no_workspace_backend(tmp_path, monkeypatch, capsys):
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "git")
    monkeypatch.setenv("PATH", str(bin_dir))
    preflight({"tool": "auto"})
    err = capsys.readouterr().err
    assert "no workspace tool on PATH" in err


def test_preflight_silent_when_tool_explicitly_set(tmp_path, monkeypatch, capsys):
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "git")
    monkeypatch.setenv("PATH", str(bin_dir))
    preflight({"tool": "none"})
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_invalid_sidebar_color(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight(
            {"tool": "cmux", "repos": [{"name": "r", "sidebar_color": "Turquoise"}]}
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "sidebar_color" in err
    assert "Turquoise" in err
    assert "Teal" in err  # the valid set is listed


def test_preflight_passes_on_valid_sidebar_color(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "repos": [{"name": "r", "sidebar_color": "Teal"}]})
    assert capsys.readouterr().err == ""


def test_preflight_ignores_repo_without_sidebar_color(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "repos": [{"name": "r", "path": "/x"}]})
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_non_bool_review_prs(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "repos": [{"name": "r", "review_prs": "yes"}]})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "review_prs" in err
    assert "'yes'" in err


def test_preflight_passes_on_bool_review_prs(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "repos": [{"name": "r", "review_prs": True}]})
    assert capsys.readouterr().err == ""


def test_preflight_ignores_repo_without_review_prs(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "repos": [{"name": "r", "path": "/x"}]})
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_non_bool_check_update(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "check_update": "yes"})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "check_update" in err
    assert "'yes'" in err


def test_preflight_passes_on_bool_check_update(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "check_update": False})
    assert capsys.readouterr().err == ""


def test_preflight_ignores_absent_check_update(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux"})
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_non_string_dev_done_state(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "repos": [], "linear_dev_done_state": 5})
    assert exc.value.code == 2
    assert "linear_dev_done_state" in capsys.readouterr().err


def test_preflight_warns_when_linear_repo_but_no_api_key(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    preflight({"tool": "cmux", "repos": [{"name": "r", "linear_keys": ["PE"]}]})
    err = capsys.readouterr().err
    assert "LINEAR_API_KEY" in err
    assert "dev-done pill" in err


def test_preflight_silent_when_linear_repo_and_api_key_set(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.setenv("LINEAR_API_KEY", "lin_xxx")
    preflight({"tool": "cmux", "repos": [{"name": "r", "linear_keys": ["PE"]}]})
    assert capsys.readouterr().err == ""


def test_preflight_silent_when_no_linear_repo_even_without_key(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    preflight({"tool": "cmux", "repos": [{"name": "r", "path": "/x"}]})
    assert capsys.readouterr().err == ""
