"""Tests for cockpit/lib/preflight.preflight().

Verifies the unified dependency check that runs at the top of every
`cockpit.py` invocation: hard-fails on missing required binaries, soft-warns
on missing workspace backend.
"""

from __future__ import annotations

import pytest

from cockpit.lib.preflight import preflight
from tests.fixtures import make_bin_on_path


def _all_required(tmp_path, monkeypatch) -> None:
    # `cockpit` too — preflight soft-warns when its own console script is absent,
    # so a healthy (silent) preflight needs it on PATH alongside gh/git/cmux.
    make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cmux", "cockpit")


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
    # `cockpit` on PATH too, else the soft install-hint warning fires.
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cockpit")
    monkeypatch.setenv("PATH", str(bin_dir))
    preflight({"tool": "none"})
    assert capsys.readouterr().err == ""


def test_preflight_warns_when_cockpit_not_on_path(tmp_path, monkeypatch, capsys):
    # gh + git present (so no hard-fail) but `cockpit` absent → soft warning,
    # not an exit: the daemon runs, but the slash-commands need it installed.
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "git")
    monkeypatch.setenv("PATH", str(bin_dir))
    preflight({"tool": "none"})
    err = capsys.readouterr().err
    assert "cockpit" in err
    assert "PATH" in err
    assert "uv tool install" in err


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


def test_preflight_exits_on_non_bool_use_slack(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "use_slack": "yes"})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "use_slack" in err
    assert "'yes'" in err


def test_preflight_passes_on_bool_use_slack(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "use_slack": True})
    assert capsys.readouterr().err == ""


def test_preflight_ignores_absent_use_slack(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux"})
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_non_numeric_orphan_nudge_grace(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "orphan_nudge_grace_hours": "soon"})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "orphan_nudge_grace_hours" in err
    assert "'soon'" in err


def test_preflight_exits_on_negative_orphan_nudge_grace(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "orphan_nudge_grace_hours": -1})
    assert exc.value.code == 2
    assert "orphan_nudge_grace_hours" in capsys.readouterr().err


def test_preflight_exits_on_bool_orphan_nudge_grace(tmp_path, monkeypatch, capsys):
    """`True` is an int in Python — reject it so a stray bool isn't read as 1h."""
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "orphan_nudge_grace_hours": True})
    assert exc.value.code == 2
    assert "orphan_nudge_grace_hours" in capsys.readouterr().err


def test_preflight_exits_on_non_numeric_repo_orphan_nudge_grace(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight(
            {
                "tool": "cmux",
                "repos": [{"name": "r", "orphan_nudge_grace_hours": "soon"}],
            }
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "r" in err and "orphan_nudge_grace_hours" in err


def test_preflight_passes_on_numeric_orphan_nudge_grace(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight(
        {
            "tool": "cmux",
            "orphan_nudge_grace_hours": 0,
            "repos": [{"name": "r", "orphan_nudge_grace_hours": 2.5}],
        }
    )
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


# ── linear_done_on_merge (the opt-in Linear write) ──────────────────────────


def test_preflight_exits_on_non_bool_global_done_on_merge(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "linear_done_on_merge": "yes"})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "linear_done_on_merge" in err
    assert "'yes'" in err


def test_preflight_exits_on_non_bool_repo_done_on_merge(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "repos": [{"name": "r", "linear_done_on_merge": 1}]})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "linear_done_on_merge" in err
    assert "'r'" in err


def test_preflight_exits_on_non_string_merge_done_state(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "linear_merge_done_state": 5})
    assert exc.value.code == 2
    assert "linear_merge_done_state" in capsys.readouterr().err


def test_preflight_warns_when_done_on_merge_enabled_but_no_api_key(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    preflight({"tool": "cmux", "linear_done_on_merge": True, "repos": []})
    err = capsys.readouterr().err
    assert "linear_done_on_merge is enabled" in err
    assert "LINEAR_API_KEY" in err


def test_preflight_warns_when_repo_done_on_merge_enabled_but_no_api_key(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    preflight({"tool": "cmux", "repos": [{"name": "r", "linear_done_on_merge": True}]})
    assert "linear_done_on_merge is enabled" in capsys.readouterr().err


def test_preflight_silent_when_done_on_merge_enabled_and_api_key_set(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.setenv("LINEAR_API_KEY", "lin_xxx")
    preflight({"tool": "cmux", "linear_done_on_merge": True, "repos": []})
    assert capsys.readouterr().err == ""


def test_preflight_silent_when_done_on_merge_disabled_without_key(
    tmp_path, monkeypatch, capsys
):
    # Default-off: a missing key is irrelevant, so no warning.
    _all_required(tmp_path, monkeypatch)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    preflight({"tool": "cmux", "linear_done_on_merge": False, "repos": []})
    assert capsys.readouterr().err == ""
