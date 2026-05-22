"""Tests for scripts/cockpit.py.

Two sections:
  - _maybe_autoclose: ordering + dry/error guards (delegates to lib.teardown).
  - _reap_workspace_orphans: gating logic for orphan-workspace cleanup
    (ownership derived from cwd vs registered repos).
"""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

import cockpit
from lib import teardown as teardown_mod
from lib.git import Worktree


# ────────────────────────────────────────────────────────────────────────────
# _maybe_autoclose: cmux workspace MUST close before worktree removal,
# otherwise the cwd is yanked out from under a live Claude Code session and
# every Stop/PreToolUse hook fails with ENOENT.
# ────────────────────────────────────────────────────────────────────────────


def test_cmux_close_runs_before_remove_worktree(tmp_path):
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    calls: list[str] = []

    def fake_cmux_close(ref):
        calls.append("cmux_close")
        return True

    def fake_remove(repo_path, path, **kwargs):
        calls.append("remove_worktree")
        return True, ""

    with (
        patch.object(
            teardown_mod,
            "cmux_close_workspace_best_effort",
            side_effect=fake_cmux_close,
        ),
        patch.object(teardown_mod, "remove_worktree", side_effect=fake_remove),
        patch.object(cockpit, "count_commits_since", return_value=0),
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(teardown_mod, "ff_default_branch_worktrees", return_value=[]),
    ):
        cockpit._maybe_autoclose(
            cfg={"auto_cleanup_on_merge": True},
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            dry=False,
        )

    assert calls == [
        "cmux_close",
        "remove_worktree",
    ], f"cmux workspace must close before worktree removal; got {calls}"


def test_autoclose_dry_run_calls_neither(tmp_path):
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as remove_mock,
        patch.object(cockpit, "count_commits_since", return_value=0),
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
    ):
        cockpit._maybe_autoclose(
            cfg={"auto_cleanup_on_merge": True},
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            dry=True,
        )

    close_mock.assert_not_called()
    remove_mock.assert_not_called()


def test_autoclose_remove_failure_still_closes_cmux_and_skips_cache_delete(tmp_path):
    """If remove_worktree fails, cmux close has already run (correct), and
    we skip delete_pr_caches_for_branch (preserves prior gating behavior)."""
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree", return_value=(False, "boom")),
        patch.object(cockpit, "count_commits_since", return_value=0),
        patch.object(teardown_mod, "delete_pr_caches_for_branch") as cache_mock,
    ):
        cockpit._maybe_autoclose(
            cfg={"auto_cleanup_on_merge": True},
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            dry=False,
        )

    close_mock.assert_called_once()
    cache_mock.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# _reap_workspace_orphans: a workspace is reap-eligible iff its cwd resolves
# under a registered repo (main path or live worktree) AND no live worktree
# matches by cwd or name.
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def reap_isolated(tmp_path, monkeypatch):
    """Isolated COCKPIT_HOME and reloaded modules so each test starts fresh."""
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    import lib.config as cfg

    importlib.reload(cfg)
    import lib.close_requests as cr

    importlib.reload(cr)
    import cockpit as cockpit_mod

    importlib.reload(cockpit_mod)
    return cockpit_mod, cr


def _wt_stub(path: Path, branch: str):
    return Worktree(path=path, branch=branch, dirty_count=0, unpushed=0)


def test_reap_skips_tracked_workspace(reap_isolated, tmp_path):
    cockpit_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt_path = tmp_path / "wt-tracked"
    wt_path.mkdir()
    wt = _wt_stub(wt_path, "khivi/feat")

    repos = [{"path": str(repo_path), "name": "repo"}]

    with (
        patch.object(cockpit_mod, "worktrees", return_value=[wt]),
        patch.object(
            cockpit_mod,
            "workspace_state",
            return_value=({"workspace:1": "feat-x"}, {"workspace:1": wt_path}),
        ),
    ):
        cockpit_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []


def test_reap_enqueues_stranded_workspace_in_registered_repo(reap_isolated, tmp_path):
    cockpit_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]

    ghost_cwd = repo_path / "removed-worktree"
    ghost_cwd.mkdir()

    with (
        patch.object(cockpit_mod, "worktrees", return_value=[wt]),
        patch.object(
            cockpit_mod,
            "workspace_state",
            return_value=(
                {"workspace:99": "khivi/ghost"},
                {"workspace:99": ghost_cwd},
            ),
        ),
        patch.object(cockpit_mod, "workspace_is_idle", return_value=True),
    ):
        cockpit_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    pending = cr.iter_pending()
    assert len(pending) == 1
    _, req = pending[0]
    assert req.ref == "workspace:99"
    assert req.worktree_path is None
    assert req.forced is True
    assert req.repo_name == "repo"


def test_reap_defers_when_workspace_not_idle(reap_isolated, tmp_path, capsys):
    """A stranded workspace whose Claude is mid-turn is left for next cycle."""
    cockpit_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]
    ghost_cwd = repo_path / "removed-worktree"
    ghost_cwd.mkdir()

    with (
        patch.object(cockpit_mod, "worktrees", return_value=[wt]),
        patch.object(
            cockpit_mod,
            "workspace_state",
            return_value=(
                {"workspace:99": "khivi/ghost"},
                {"workspace:99": ghost_cwd},
            ),
        ),
        patch.object(cockpit_mod, "workspace_is_idle", return_value=False),
    ):
        cockpit_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []
    out = capsys.readouterr().out
    assert "defer" in out
    assert "reap" in out
    assert "not idle" in out
    assert "workspace:99" in out


def test_reap_ignores_workspace_outside_registered_repos(reap_isolated, tmp_path):
    cockpit_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    with (
        patch.object(cockpit_mod, "worktrees", return_value=[wt]),
        patch.object(
            cockpit_mod,
            "workspace_state",
            return_value=(
                {"workspace:42": "research"},
                {"workspace:42": elsewhere},
            ),
        ),
    ):
        cockpit_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []


def test_reap_dry_run_does_not_enqueue(reap_isolated, tmp_path):
    cockpit_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]
    ghost_cwd = repo_path / "ghost"
    ghost_cwd.mkdir()

    with (
        patch.object(cockpit_mod, "worktrees", return_value=[wt]),
        patch.object(
            cockpit_mod,
            "workspace_state",
            return_value=(
                {"workspace:99": "khivi/ghost"},
                {"workspace:99": ghost_cwd},
            ),
        ),
        patch.object(cockpit_mod, "workspace_is_idle", return_value=True),
    ):
        cockpit_mod._reap_workspace_orphans(repos, "khivi", dry=True)

    assert cr.iter_pending() == []


def test_reap_skips_workspace_matched_by_name(reap_isolated, tmp_path):
    """Even with a missing cwd, name-match to an existing wt.short keeps it alive."""
    cockpit_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt_path = tmp_path / "feat-named"
    wt_path.mkdir()
    wt = _wt_stub(wt_path, "khivi/feat-named")

    repos = [{"path": str(repo_path), "name": "repo"}]

    with (
        patch.object(cockpit_mod, "worktrees", return_value=[wt]),
        patch.object(
            cockpit_mod,
            "workspace_state",
            return_value=({"workspace:5": "feat-named"}, {}),
        ),
    ):
        cockpit_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []


# ────────────────────────────────────────────────────────────────────────────
# CLI dispatch: --footer seeds statusLine + starship/cship configs; --once /
# --watch do NOT touch those files. Verifies cockpit.main() routes the right
# helpers per subcommand. Moved here from tests/lib/test_config.py.
# ────────────────────────────────────────────────────────────────────────────


from cockpit_helpers import (  # noqa: E402
    expected_starship as _expected_starship,
    setup_cockpit_config as _setup_cockpit_config,
    stub_cship_on_path as _stub_cship_on_path,
)


def test_cli_footer_flag_runs_only_footer_setup(tmp_path, monkeypatch):
    """`--footer` installs cship.toml + starship.toml + statusLine and exits."""
    import importlib
    import json as _json

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _stub_cship_on_path(monkeypatch, present=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit

    importlib.reload(cockpit)

    def _explode(*_a, **_kw):
        raise AssertionError("--footer must not trigger a reconcile cycle")

    monkeypatch.setattr(cockpit, "gh_self_user", _explode)
    monkeypatch.setattr(cockpit, "cycle_all", _explode)

    assert cockpit.main(["--footer"]) == 0

    cship_toml = tmp_path / "xdg" / "cship.toml"
    assert cship_toml.exists()
    assert cship_toml.read_text() == cockpit_config.CSHIP_DEFAULT_TOML.read_text()

    starship_toml = tmp_path / "xdg" / "starship.toml"
    assert starship_toml.exists()
    assert starship_toml.read_text() == _expected_starship(cockpit_config)

    settings = _json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["statusLine"]["type"] == "command"
    assert settings["statusLine"]["command"].endswith("/footer.py")


def test_cli_once_does_not_touch_footer_files(tmp_path, monkeypatch):
    """`--once` is pure reconcile — never seeds either toml or writes statusLine."""
    import importlib

    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    _stub_cship_on_path(monkeypatch, present=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit

    importlib.reload(cockpit)
    monkeypatch.setattr(cockpit, "_build_state", lambda _a: {"dry": True})
    monkeypatch.setattr(cockpit, "_once_with", lambda _s: None)

    assert cockpit.main(["--once"]) == 0
    assert not (tmp_path / "xdg" / "cship.toml").exists()
    assert not (tmp_path / "xdg" / "starship.toml").exists()
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_cli_watch_does_not_touch_footer_files(tmp_path, monkeypatch):
    """`--watch` is pure reconcile — never seeds either toml or writes statusLine."""
    import importlib

    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    _stub_cship_on_path(monkeypatch, present=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit

    importlib.reload(cockpit)
    monkeypatch.setattr(cockpit, "_build_state", lambda _a: {"dry": True})
    monkeypatch.setattr(cockpit, "_watch", lambda _s, _secs: None)

    assert cockpit.main(["--watch", "60"]) == 0
    assert not (tmp_path / "xdg" / "cship.toml").exists()
    assert not (tmp_path / "xdg" / "starship.toml").exists()
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_cli_once_does_not_raise_when_cship_missing(tmp_path, monkeypatch):
    """`--once` must not invoke the cship-on-PATH check; missing cship is a `--footer` concern."""
    import importlib

    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    _stub_cship_on_path(monkeypatch, present=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit

    importlib.reload(cockpit)
    monkeypatch.setattr(cockpit, "_build_state", lambda _a: {"dry": True})
    monkeypatch.setattr(cockpit, "_once_with", lambda _s: None)

    assert cockpit.main(["--once"]) == 0
