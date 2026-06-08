"""Tests for cockpit/cockpit.py CLI dispatch.

`--footer` seeds statusLine + starship/cship configs; `--once` / `--watch` do
NOT touch those files. Pipeline tests live in tests/orchestrators/test_cycle.py.
"""

from __future__ import annotations

import importlib
import json as _json

from tests.asserts import expected_starship as _expected_starship
from tests.fixtures import (
    make_bin_on_path as _make_bin_on_path,
)
from tests.fixtures import (
    setup_cockpit_config as _setup_cockpit_config,
)


def test_cli_footer_flag_runs_only_footer_setup(tmp_path, monkeypatch):
    """`--footer` installs cship.toml + starship.toml + statusLine and exits."""
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cship", "starship")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit.cockpit as cockpit

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
    assert settings["statusLine"]["command"].endswith("-m cockpit.cli statusline")


def test_cli_once_does_not_touch_footer_files(tmp_path, monkeypatch):
    """`--once` is pure reconcile — never seeds either toml or writes statusLine."""
    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    _make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cship", "starship")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit.cockpit as cockpit

    importlib.reload(cockpit)
    monkeypatch.setattr(cockpit, "_build_state", lambda _a: {"dry": True})
    monkeypatch.setattr(cockpit, "_once_with", lambda _s: None)

    assert cockpit.main(["--once"]) == 0
    assert not (tmp_path / "xdg" / "cship.toml").exists()
    assert not (tmp_path / "xdg" / "starship.toml").exists()
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_cli_watch_does_not_touch_footer_files(tmp_path, monkeypatch):
    """`--watch` is pure reconcile — never seeds either toml or writes statusLine."""
    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    _make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cship", "starship")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit.cockpit as cockpit

    importlib.reload(cockpit)
    monkeypatch.setattr(cockpit, "_build_state", lambda _a: {"dry": True})
    monkeypatch.setattr(cockpit, "_watch", lambda *_a, **_kw: 0)

    assert cockpit.main(["--watch"]) == 0
    assert not (tmp_path / "xdg" / "cship.toml").exists()
    assert not (tmp_path / "xdg" / "starship.toml").exists()
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_cli_once_exits_when_use_cship_and_cship_missing(tmp_path, monkeypatch, capsys):
    """Preflight runs on every cockpit invocation: `use_cship: true` without
    cship on PATH must hard-fail `--once` (and `--watch`, `--footer`) — same
    contract as the standalone `lib.preflight.preflight()` test suite."""
    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    bin_dir = _make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "starship")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit.cockpit as cockpit

    importlib.reload(cockpit)
    monkeypatch.setattr(cockpit, "_build_state", lambda _a: {"dry": True})
    monkeypatch.setattr(cockpit, "_once_with", lambda _s: None)

    import pytest

    with pytest.raises(SystemExit) as exc:
        cockpit.main(["--once"])
    assert exc.value.code == 2
    assert "`cship`" in capsys.readouterr().err


def test_fast_tick_reconciles_workspace_names(tmp_path, monkeypatch):
    """The fast tick fetches names/cwds once and reconciles workspace names
    against each repo's worktrees."""
    import cockpit.cockpit as cockpit
    from cockpit.lib.git import Worktree

    importlib.reload(cockpit)

    repo = tmp_path / "repo"
    repo.mkdir()
    wt = Worktree(path=repo / "feat", branch="khivi/feat")
    names = {"workspace:1": "stale"}
    cwds = {"workspace:1": repo / "feat"}

    reconcile_calls: list = []
    monkeypatch.setattr(
        cockpit, "load_config", lambda: {"repos": [{"path": str(repo)}]}
    )
    monkeypatch.setattr(cockpit, "worktrees", lambda _p, _prefix="": [wt])
    monkeypatch.setattr(cockpit, "write_git_state_cache", lambda _p: None)
    monkeypatch.setattr(cockpit, "workspace_state", lambda: (names, cwds))
    monkeypatch.setattr(
        cockpit,
        "reconcile_workspace_names",
        lambda n, c, w: reconcile_calls.append((n, c, w)),
    )
    monkeypatch.setattr(cockpit, "republish_pr_caches_from_disk", lambda: None)

    cockpit._fast_tick({"dry": False})

    assert reconcile_calls == [(names, cwds, [wt])]


def test_fast_tick_degrades_when_cmux_unavailable(tmp_path, monkeypatch):
    """A cmux backend hiccup degrades to no rename, never a crash, and the rest
    of the fast tick still runs."""
    import cockpit.cockpit as cockpit
    from cockpit.lib.git import Worktree

    importlib.reload(cockpit)

    repo = tmp_path / "repo"
    repo.mkdir()
    wt = Worktree(path=repo / "feat", branch="khivi/feat")

    def _boom():
        raise cockpit.CmuxUnavailable("list-workspaces failed")

    reconcile_calls: list = []
    republished: list = []
    monkeypatch.setattr(
        cockpit, "load_config", lambda: {"repos": [{"path": str(repo)}]}
    )
    monkeypatch.setattr(cockpit, "worktrees", lambda _p, _prefix="": [wt])
    monkeypatch.setattr(cockpit, "write_git_state_cache", lambda _p: None)
    monkeypatch.setattr(cockpit, "workspace_state", _boom)
    monkeypatch.setattr(
        cockpit,
        "reconcile_workspace_names",
        lambda *a: reconcile_calls.append(a),
    )
    monkeypatch.setattr(
        cockpit, "republish_pr_caches_from_disk", lambda: republished.append(True)
    )

    cockpit._fast_tick({"dry": False})

    assert reconcile_calls == []  # empty cwds → no reconcile attempted
    assert republished == [True]  # tick completed


def test_watch_requires_tty(capsys):
    """`cockpit watch` is TUI-only: with no TTY (as under pytest capture) it
    prints a clean message and exits 2 instead of launching Textual."""
    import cockpit.cockpit as cockpit

    rc = cockpit._watch({"dry": False}, 300, 30)
    assert rc == 2
    assert "requires a terminal" in capsys.readouterr().err
