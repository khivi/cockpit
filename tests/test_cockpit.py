"""Tests for cockpit/cockpit.py CLI dispatch.

`--setup` seeds statusLine + starship/cship configs; `--watch` does NOT touch
those files. Pipeline tests live in tests/orchestrators/test_cycle.py.
"""

from __future__ import annotations

import importlib
import json as _json
import sys
import threading
import time
from pathlib import Path

from tests.asserts import expected_starship as _expected_starship
from tests.fixtures import (
    make_bin_on_path as _make_bin_on_path,
)
from tests.fixtures import (
    setup_cockpit_config as _setup_cockpit_config,
)


def test_cli_footer_flag_runs_only_footer_setup(tmp_path, monkeypatch):
    """`--setup` installs cship.toml + starship.toml + statusLine and exits."""
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cship", "starship")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit.cockpit as cockpit

    importlib.reload(cockpit)

    def _explode(*_a, **_kw):
        raise AssertionError("--setup must not trigger a reconcile cycle")

    monkeypatch.setattr(cockpit, "gh_self_user", _explode)
    monkeypatch.setattr(cockpit, "cycle_all", _explode)

    assert cockpit.main(["--setup"]) == 0

    cship_toml = tmp_path / "xdg" / "cship.toml"
    assert cship_toml.exists()
    assert cship_toml.read_text() == cockpit_config.CSHIP_DEFAULT_TOML.read_text()

    starship_toml = tmp_path / "xdg" / "starship.toml"
    assert starship_toml.exists()
    assert starship_toml.read_text() == _expected_starship(cockpit_config)

    settings = _json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["statusLine"]["type"] == "command"
    assert settings["statusLine"]["command"].endswith("-m cockpit.cli statusline")


def test_cli_watch_does_not_touch_footer_files(tmp_path, monkeypatch):
    """`--watch` never seeds either toml or writes the statusLine, and it does
    NOT force-install the Claude integration onto a user who never ran setup —
    with no cockpit hooks present it only hints, leaving settings.json alone."""
    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    _make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cship", "starship")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit.cockpit as cockpit

    importlib.reload(cockpit)
    monkeypatch.setattr(cockpit, "_build_state", lambda *_a: {})
    monkeypatch.setattr(cockpit, "_watch", lambda *_a, **_kw: 0)

    assert cockpit.main(["--watch"]) == 0
    assert not (tmp_path / "xdg" / "cship.toml").exists()
    assert not (tmp_path / "xdg" / "starship.toml").exists()
    assert not (
        tmp_path / ".claude" / "settings.json"
    ).exists()  # not set up → not touched


def test_cli_watch_reasserts_existing_claude_integration(tmp_path, monkeypatch):
    """When cockpit hooks ARE already present (user ran setup), watch re-asserts
    them (repairs drift) instead of hinting."""
    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    _make_bin_on_path(tmp_path, monkeypatch, "gh", "git")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit.cockpit as cockpit

    importlib.reload(cockpit)
    monkeypatch.setattr(cockpit, "_build_state", lambda *_a: {})
    monkeypatch.setattr(cockpit, "_watch", lambda *_a, **_kw: 0)
    reasserted = []
    monkeypatch.setattr(cockpit, "claude_integration_present", lambda: True)
    monkeypatch.setattr(
        cockpit, "install_claude_hooks", lambda: reasserted.append("hooks")
    )
    monkeypatch.setattr(
        cockpit, "install_claude_commands", lambda: reasserted.append("cmds")
    )

    assert cockpit.main(["--watch"]) == 0
    assert reasserted == ["hooks", "cmds"]


def test_watch_dry_flag_reaches_the_cycle(tmp_path, monkeypatch):
    """`--dry` is the whole safety gate `./dev.sh` leans on: it stops autoclose
    removing worktrees and `git branch -D`ing their refs, which `tool: none`
    does NOT cover (autoclose goes through git, not the workspace backend). A
    flag that parses but never reaches `cycle_all` would be silently unsafe, so
    assert the value all the way through rather than just on the parsed args."""
    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    _make_bin_on_path(tmp_path, monkeypatch, "gh", "git")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit.cockpit as cockpit

    importlib.reload(cockpit)
    built: list[dict] = []

    def _record(dry: bool = False) -> dict:
        built.append({"dry": dry})
        return built[-1]

    monkeypatch.setattr(cockpit, "_watch", lambda state, *_a, **_kw: 0)
    monkeypatch.setattr(cockpit, "_build_state", _record)

    assert cockpit.main(["--watch", "--dry"]) == 0
    assert built[-1]["dry"] is True


def test_once_with_threads_dry_into_the_cycle(monkeypatch):
    """The other half of the gate: a flag that lands in `state` but never
    reaches `cycle_all` would be silently unsafe — autoclose would still remove
    worktrees and `git branch -D` their refs."""
    import cockpit.cockpit as cockpit

    seen: list[bool] = []
    monkeypatch.setattr(cockpit, "load_config", lambda: {"repos": []})
    monkeypatch.setattr(cockpit, "gh_self_user", lambda: "khivi")
    monkeypatch.setattr(cockpit, "cycle_all", lambda *_a, **kw: seen.append(kw["dry"]))

    cockpit._once_with(cockpit._build_state(True))
    cockpit._once_with(cockpit._build_state(False))
    assert seen == [True, False]


def test_watch_is_not_dry_by_default(tmp_path, monkeypatch):
    """The real daemon must keep acting — `--dry` is opt-in, never the default."""
    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    _make_bin_on_path(tmp_path, monkeypatch, "gh", "git")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit.cockpit as cockpit

    importlib.reload(cockpit)
    built: list[dict] = []

    def _record(dry: bool = False) -> dict:
        built.append({"dry": dry})
        return built[-1]

    monkeypatch.setattr(cockpit, "_watch", lambda state, *_a, **_kw: 0)
    monkeypatch.setattr(cockpit, "_build_state", _record)

    assert cockpit.main(["--watch"]) == 0
    assert built[-1]["dry"] is False


def test_cli_exits_when_use_cship_and_cship_missing(tmp_path, monkeypatch, capsys):
    """Preflight runs on every cockpit invocation: `use_cship: true` without
    cship on PATH must hard-fail before any dispatch — same contract as the
    standalone `lib.preflight.preflight()` test suite."""
    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    bin_dir = _make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "starship")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit.cockpit as cockpit

    importlib.reload(cockpit)

    import pytest

    with pytest.raises(SystemExit) as exc:
        cockpit.main(["--watch"])
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
    monkeypatch.setattr(
        cockpit, "worktrees", lambda _p, _prefix="", _name="", _tag="": [wt]
    )
    monkeypatch.setattr(cockpit, "write_git_state_cache", lambda _p, _name="": None)
    monkeypatch.setattr(cockpit, "write_worktree_cost_cache", lambda _p: None)
    monkeypatch.setattr(cockpit, "write_diff_comments_cache", lambda _p, _n: None)
    monkeypatch.setattr(cockpit.diff_comments, "pending_by_root", lambda _roots: {})
    monkeypatch.setattr(cockpit, "workspace_state", lambda: (names, cwds))
    monkeypatch.setattr(cockpit, "reassert_idle_pills", lambda _refs: [])
    monkeypatch.setattr(
        cockpit,
        "reconcile_workspace_names",
        lambda n, c, w: reconcile_calls.append((n, c, w)),
    )
    monkeypatch.setattr(cockpit, "republish_pr_caches_from_disk", lambda: None)

    cockpit._fast_tick({})

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
    monkeypatch.setattr(
        cockpit, "worktrees", lambda _p, _prefix="", _name="", _tag="": [wt]
    )
    monkeypatch.setattr(cockpit, "write_git_state_cache", lambda _p, _name="": None)
    monkeypatch.setattr(cockpit, "write_worktree_cost_cache", lambda _p: None)
    monkeypatch.setattr(cockpit, "write_diff_comments_cache", lambda _p, _n: None)
    monkeypatch.setattr(cockpit.diff_comments, "pending_by_root", lambda _roots: {})
    monkeypatch.setattr(cockpit, "workspace_state", _boom)
    monkeypatch.setattr(
        cockpit,
        "reconcile_workspace_names",
        lambda *a: reconcile_calls.append(a),
    )
    monkeypatch.setattr(
        cockpit, "republish_pr_caches_from_disk", lambda: republished.append(True)
    )

    cockpit._fast_tick({})

    assert reconcile_calls == []  # empty cwds → no reconcile attempted
    assert republished == [True]  # tick completed


def test_fast_tick_tints_spawned_workspace(tmp_path, monkeypatch):
    """A repo with a `sidebar_color` gets its owned workspaces tinted on the
    fast tick and the tint recorded in the shared `pill_state`, so a freshly
    spawned workspace picks up the colour within ~30s (not the next slow tick)."""
    import cockpit.cockpit as cockpit
    from cockpit.lib.git import Worktree

    importlib.reload(cockpit)

    repo = tmp_path / "repo"
    repo.mkdir()
    wt = Worktree(path=repo / "feat", branch="khivi/feat")
    (repo / "feat").mkdir()
    cwds = {"workspace:1": repo / "feat"}

    tinted: list = []
    monkeypatch.setattr(
        cockpit,
        "load_config",
        lambda: {"repos": [{"path": str(repo), "sidebar_color": "Teal"}]},
    )
    monkeypatch.setattr(
        cockpit, "worktrees", lambda _p, _prefix="", _name="", _tag="": [wt]
    )
    monkeypatch.setattr(cockpit, "write_git_state_cache", lambda _p, _name="": None)
    monkeypatch.setattr(cockpit, "write_worktree_cost_cache", lambda _p: None)
    monkeypatch.setattr(cockpit, "write_diff_comments_cache", lambda _p, _n: None)
    monkeypatch.setattr(cockpit.diff_comments, "pending_by_root", lambda _roots: {})
    monkeypatch.setattr(cockpit, "workspace_state", lambda: ({}, cwds))
    monkeypatch.setattr(cockpit, "reassert_idle_pills", lambda _refs: [])
    monkeypatch.setattr(cockpit, "reconcile_workspace_names", lambda *a: None)
    monkeypatch.setattr(
        cockpit, "set_workspace_color", lambda ref, color: tinted.append((ref, color))
    )
    monkeypatch.setattr(cockpit, "republish_pr_caches_from_disk", lambda: None)

    state: dict = {"pill_state": {}}
    cockpit._fast_tick(state)

    assert tinted == [("workspace:1", "Teal")]
    assert state["pill_state"]["color:workspace:1"] == "Teal"

    # Second tick with the colour already recorded is a no-op (shared dedup).
    tinted.clear()
    cockpit._fast_tick(state)
    assert tinted == []


def test_fast_tick_skips_color_without_sidebar_color(tmp_path, monkeypatch):
    """A repo with no `sidebar_color` is never tinted on the fast tick."""
    import cockpit.cockpit as cockpit
    from cockpit.lib.git import Worktree

    importlib.reload(cockpit)

    repo = tmp_path / "repo"
    repo.mkdir()
    wt = Worktree(path=repo / "feat", branch="khivi/feat")

    tinted: list = []
    monkeypatch.setattr(
        cockpit, "load_config", lambda: {"repos": [{"path": str(repo)}]}
    )
    monkeypatch.setattr(
        cockpit, "worktrees", lambda _p, _prefix="", _name="", _tag="": [wt]
    )
    monkeypatch.setattr(cockpit, "write_git_state_cache", lambda _p, _name="": None)
    monkeypatch.setattr(cockpit, "write_worktree_cost_cache", lambda _p: None)
    monkeypatch.setattr(cockpit, "write_diff_comments_cache", lambda _p, _n: None)
    monkeypatch.setattr(cockpit.diff_comments, "pending_by_root", lambda _roots: {})
    monkeypatch.setattr(
        cockpit, "workspace_state", lambda: ({}, {"workspace:1": repo / "feat"})
    )
    monkeypatch.setattr(cockpit, "reassert_idle_pills", lambda _refs: [])
    monkeypatch.setattr(cockpit, "reconcile_workspace_names", lambda *a: None)
    monkeypatch.setattr(
        cockpit, "set_workspace_color", lambda ref, color: tinted.append((ref, color))
    )
    monkeypatch.setattr(cockpit, "republish_pr_caches_from_disk", lambda: None)

    cockpit._fast_tick({})

    assert tinted == []


def test_watch_requires_tty(capsys):
    """`cockpit watch` is TUI-only: with no TTY (as under pytest capture) it
    prints a clean message and exits 2 instead of launching Textual."""
    import cockpit.cockpit as cockpit

    rc = cockpit._watch({}, 300, 30)
    assert rc == 2
    assert "requires a terminal" in capsys.readouterr().err


def _run_watch_with_return_code(monkeypatch, return_code):
    """Drive _watch past app.run() with a stub app carrying `return_code`,
    faking a TTY and stubbing the pidfile so no real Textual/daemon starts."""
    from unittest.mock import MagicMock

    import cockpit.cockpit as cockpit
    import cockpit.lib.daemon as daemon
    import cockpit.tui.app as tui_app

    monkeypatch.setattr(cockpit.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(daemon, "claim_pidfile", lambda: None)
    fake_app = MagicMock()
    fake_app.return_code = return_code
    monkeypatch.setattr(tui_app, "CockpitApp", lambda **_kw: fake_app)
    exits: list[int] = []
    monkeypatch.setattr(cockpit.os, "_exit", lambda code: exits.append(code))
    rc = cockpit._watch({}, 300, 30)
    return rc, exits, fake_app


def test_watch_hard_exits_on_normal_quit(monkeypatch):
    """A clean `q` quit os._exit()s instead of returning — a normal return would
    hang at interpreter exit joining a slow-tick thread still blocked in `gh`."""
    _rc, exits, _app = _run_watch_with_return_code(monkeypatch, 0)
    assert exits == [0]


def test_watch_runs_app_on_own_loop(monkeypatch):
    """`_watch` must drive the app on a loop it owns, not Textual's default
    `asyncio.run()`. The default joins the thread-worker executor with a 300s
    timeout at shutdown, so `q` mid-tick hangs *inside* `app.run()` (before the
    os._exit below can fire). Passing `loop=` makes Textual use
    `run_until_complete`, which skips that join and returns immediately."""
    import asyncio

    _rc, _exits, app = _run_watch_with_return_code(monkeypatch, 0)
    app.run.assert_called_once()
    loop = app.run.call_args.kwargs.get("loop")
    assert isinstance(loop, asyncio.AbstractEventLoop)


# ---- interactive setup: statusline opt-in / --install-deps / --reset --------


def _reload_cockpit():
    import cockpit.cockpit as cockpit

    importlib.reload(cockpit)
    return cockpit


def _force_tty(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)


def test_prompt_yes_non_tty_returns_default(monkeypatch):
    cockpit = _reload_cockpit()
    # pytest's captured stdin isn't a TTY, so the default is returned unprompted.
    assert cockpit._prompt_yes("q?", default=True) is True
    assert cockpit._prompt_yes("q?", default=False) is False


def test_maybe_enable_statusline_noop_when_already_enabled(monkeypatch):
    cockpit = _reload_cockpit()
    monkeypatch.setattr(cockpit, "load_config", lambda: {"use_cship": True})
    saved = []
    monkeypatch.setattr(cockpit, "save_config_value", lambda *a: saved.append(a))
    cockpit._maybe_enable_statusline(install_deps=False)
    assert saved == []


def test_maybe_enable_statusline_noop_non_tty(monkeypatch):
    cockpit = _reload_cockpit()
    monkeypatch.setattr(cockpit, "load_config", lambda: {})
    saved = []
    monkeypatch.setattr(cockpit, "save_config_value", lambda *a: saved.append(a))
    cockpit._maybe_enable_statusline(install_deps=False)  # non-TTY under pytest
    assert saved == []


def test_maybe_enable_statusline_accept_deps_present_enables(monkeypatch):
    cockpit = _reload_cockpit()
    monkeypatch.setattr(cockpit, "load_config", lambda: {})
    _force_tty(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _p: "y")
    monkeypatch.setattr(cockpit.shutil, "which", lambda b: "/usr/bin/" + b)

    def _no_install(*_a, **_k):
        raise AssertionError("must not install when deps already present")

    monkeypatch.setattr(cockpit.subprocess, "run", _no_install)
    saved: dict = {}
    monkeypatch.setattr(
        cockpit, "save_config_value", lambda k, v: saved.__setitem__(k, v)
    )
    cockpit._maybe_enable_statusline(install_deps=False)
    assert saved == {"use_cship": True}


def test_maybe_enable_statusline_missing_no_flag_prints_skips(monkeypatch, capsys):
    cockpit = _reload_cockpit()
    monkeypatch.setattr(cockpit, "load_config", lambda: {})
    _force_tty(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _p: "y")
    monkeypatch.setattr(cockpit.shutil, "which", lambda _b: None)
    saved: dict = {}
    monkeypatch.setattr(
        cockpit, "save_config_value", lambda k, v: saved.__setitem__(k, v)
    )
    cockpit._maybe_enable_statusline(install_deps=False)
    assert saved == {}  # not enabled until cship present
    assert "cship.dev" in capsys.readouterr().out


def test_maybe_enable_statusline_starship_missing_skips(monkeypatch, capsys):
    cockpit = _reload_cockpit()
    monkeypatch.setattr(cockpit, "load_config", lambda: {})
    _force_tty(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _p: "y")
    # cship resolves, starship doesn't — the post-install re-check must catch this too.
    monkeypatch.setattr(
        cockpit.shutil, "which", lambda b: "/usr/bin/cship" if b == "cship" else None
    )
    saved: dict = {}
    monkeypatch.setattr(
        cockpit, "save_config_value", lambda k, v: saved.__setitem__(k, v)
    )
    cockpit._maybe_enable_statusline(install_deps=False)
    assert saved == {}  # not enabled while starship is still missing
    assert "starship" in capsys.readouterr().out


def test_maybe_enable_statusline_install_deps_runs_installer(monkeypatch):
    cockpit = _reload_cockpit()
    monkeypatch.setattr(cockpit, "load_config", lambda: {})
    _force_tty(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _p: "y")
    state = {"installed": False}
    monkeypatch.setattr(
        cockpit.shutil, "which", lambda b: ("/x/" + b) if state["installed"] else None
    )
    monkeypatch.setattr(
        cockpit.subprocess, "run", lambda *a, **k: state.__setitem__("installed", True)
    )
    saved: dict = {}
    monkeypatch.setattr(
        cockpit, "save_config_value", lambda k, v: saved.__setitem__(k, v)
    )
    cockpit._maybe_enable_statusline(install_deps=True)
    assert state["installed"] is True
    assert saved == {"use_cship": True}


def test_maybe_enable_statusline_decline(monkeypatch):
    cockpit = _reload_cockpit()
    monkeypatch.setattr(cockpit, "load_config", lambda: {})
    _force_tty(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _p: "n")
    saved: dict = {}
    monkeypatch.setattr(
        cockpit, "save_config_value", lambda k, v: saved.__setitem__(k, v)
    )
    cockpit._maybe_enable_statusline(install_deps=False)
    assert saved == {}


def test_setup_reset_tears_down_and_resets_use_cship(tmp_path, monkeypatch):
    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    _make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cship", "starship")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cockpit = _reload_cockpit()
    calls = []
    monkeypatch.setattr(
        cockpit, "teardown_claude_integration", lambda: calls.append("teardown")
    )
    assert cockpit.main(["--setup", "--reset"]) == 0
    assert calls == ["teardown"]
    assert cockpit.load_config().get("use_cship") is False


def test_fast_tick_writes_a_cost_cell_per_worktree(tmp_path, monkeypatch):
    """`wt-cost` rides the fast tick, not the slow one: it only moves while a
    session is live, so the 300s cadence would lag visibly behind the work."""
    import cockpit.cockpit as cockpit
    from cockpit.lib.git import Worktree

    importlib.reload(cockpit)

    repo = tmp_path / "repo"
    repo.mkdir()
    wts = [
        Worktree(path=repo / "feat", branch="khivi/feat"),
        Worktree(path=repo / "other", branch="khivi/other"),
    ]
    costed: list = []
    monkeypatch.setattr(
        cockpit, "load_config", lambda: {"repos": [{"path": str(repo)}]}
    )
    monkeypatch.setattr(
        cockpit, "worktrees", lambda _p, _prefix="", _name="", _tag="": wts
    )
    monkeypatch.setattr(cockpit, "write_git_state_cache", lambda _p, _name="": None)
    monkeypatch.setattr(cockpit, "write_worktree_cost_cache", costed.append)
    monkeypatch.setattr(cockpit, "workspace_state", lambda: ({}, {}))
    monkeypatch.setattr(cockpit, "republish_pr_caches_from_disk", lambda: None)
    monkeypatch.setattr(cockpit.diff_comments, "pending_by_root", lambda _roots: {})

    cockpit._fast_tick({})

    # Set, not list: the cell writes fan out across a thread pool, so the order
    # they complete in carries no meaning and asserting one would be a flake.
    assert set(costed) == {wt.path for wt in wts}


def test_fast_tick_writes_cells_for_every_repos_worktrees(tmp_path, monkeypatch):
    """The cell writes are accumulated across repos and drained once, so a
    fleet of many single-worktree repos still fans out. Pins that every repo's
    worktrees survive the hoist out of the per-repo loop."""
    import cockpit.cockpit as cockpit
    from cockpit.lib.git import Worktree

    importlib.reload(cockpit)

    repos = [tmp_path / f"r{i}" for i in range(3)]
    for r in repos:
        r.mkdir()
    by_repo = {r: [Worktree(path=r / "wt", branch=f"khivi/{r.name}")] for r in repos}
    written: list = []
    monkeypatch.setattr(
        cockpit, "load_config", lambda: {"repos": [{"path": str(r)} for r in repos]}
    )
    monkeypatch.setattr(
        cockpit, "worktrees", lambda p, _prefix="", _name="", _tag="": by_repo[p]
    )
    monkeypatch.setattr(
        cockpit, "write_git_state_cache", lambda p, _name="": written.append(p)
    )
    monkeypatch.setattr(cockpit, "write_worktree_cost_cache", lambda _p: None)
    monkeypatch.setattr(cockpit, "workspace_state", lambda: ({}, {}))
    monkeypatch.setattr(cockpit, "republish_pr_caches_from_disk", lambda: None)
    monkeypatch.setattr(cockpit.diff_comments, "pending_by_root", lambda _roots: {})

    cockpit._fast_tick({})

    assert set(written) == {r / "wt" for r in repos}


def test_fast_tick_writes_a_diff_comments_cell_per_worktree(tmp_path, monkeypatch):
    """`diff-comments` rides the fast tick too — entirely local, no `gh` needed,
    so there's no reason to make it wait for the slow tick's 300s cadence."""
    import cockpit.cockpit as cockpit
    from cockpit.lib.git import Worktree

    importlib.reload(cockpit)

    repo = tmp_path / "repo"
    repo.mkdir()
    wts = [
        Worktree(path=repo / "feat", branch="khivi/feat"),
        Worktree(path=repo / "other", branch="khivi/other"),
    ]
    monkeypatch.setattr(
        cockpit, "load_config", lambda: {"repos": [{"path": str(repo)}]}
    )
    monkeypatch.setattr(
        cockpit, "worktrees", lambda _p, _prefix="", _name="", _tag="": wts
    )
    monkeypatch.setattr(cockpit, "write_git_state_cache", lambda _p, _name="": None)
    monkeypatch.setattr(cockpit, "write_worktree_cost_cache", lambda _p: None)
    monkeypatch.setattr(cockpit, "workspace_state", lambda: ({}, {}))
    monkeypatch.setattr(cockpit, "republish_pr_caches_from_disk", lambda: None)

    feat_root = str((repo / "feat").resolve())
    monkeypatch.setattr(
        cockpit.diff_comments,
        "pending_by_root",
        lambda _roots: {feat_root: ["c1", "c2"]},
    )
    written: dict = {}
    monkeypatch.setattr(
        cockpit,
        "write_diff_comments_cache",
        lambda p, count: written.setdefault(p, count),
    )

    cockpit._fast_tick({})

    assert written == {repo / "feat": 2, repo / "other": 0}


def test_fast_tick_folds_a_root_shared_with_the_repo_checkout_into_every_worktree(
    tmp_path, monkeypatch
):
    """Which root cmux records for a worktree is undocumented (its own toplevel
    or the main checkout it was cut from), so a comment filed against the repo's
    own main-checkout root is genuinely ambiguous — it must count for every
    worktree of that repo, exactly matching what `_send_ask`'s own per-row
    `pending([wt.path, repo.path])` lookup would independently find for each of
    them. The primary-checkout worktree (whose own path IS the repo root) must
    not double it by adding the same bucket to itself twice."""
    import cockpit.cockpit as cockpit
    from cockpit.lib.git import Worktree

    importlib.reload(cockpit)

    repo = tmp_path / "repo"
    repo.mkdir()
    wts = [
        Worktree(path=repo, branch="main"),
        Worktree(path=repo / "other", branch="x"),
    ]
    monkeypatch.setattr(
        cockpit, "load_config", lambda: {"repos": [{"path": str(repo)}]}
    )
    monkeypatch.setattr(
        cockpit, "worktrees", lambda _p, _prefix="", _name="", _tag="": wts
    )
    monkeypatch.setattr(cockpit, "write_git_state_cache", lambda _p, _name="": None)
    monkeypatch.setattr(cockpit, "write_worktree_cost_cache", lambda _p: None)
    monkeypatch.setattr(cockpit, "workspace_state", lambda: ({}, {}))
    monkeypatch.setattr(cockpit, "republish_pr_caches_from_disk", lambda: None)

    repo_root = str(repo.resolve())
    monkeypatch.setattr(
        cockpit.diff_comments, "pending_by_root", lambda _roots: {repo_root: ["c1"]}
    )
    written: dict = {}
    monkeypatch.setattr(
        cockpit,
        "write_diff_comments_cache",
        lambda p, count: written.setdefault(p, count),
    )

    cockpit._fast_tick({})

    assert written == {repo: 1, repo / "other": 1}


def test_fast_tick_cell_writes_stay_bounded(monkeypatch):
    """The fan-out is capped well below the worktree count on purpose: a fleet
    spawning its whole census of `git` subprocesses at once is the process-table
    churn this exists to avoid, not merely slow. Pins the bound so removing it
    fails here rather than on a 30-worktree machine."""
    import cockpit.cockpit as cockpit
    from cockpit.lib.git import Worktree

    importlib.reload(cockpit)

    wts = [Worktree(path=Path(f"/tmp/wt{i}"), branch=f"b{i}") for i in range(40)]
    lock = threading.Lock()
    live = 0
    peak = 0

    def _slow_write(_p, _name=""):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.01)  # hold the slot so overlap is observable
        with lock:
            live -= 1

    monkeypatch.setattr(cockpit, "write_git_state_cache", _slow_write)
    monkeypatch.setattr(cockpit, "write_worktree_cost_cache", lambda _p: None)

    cockpit._write_worktree_cells(wts)

    assert peak > 1, "cell writes must actually run concurrently"
    assert peak <= cockpit.CELL_WRITE_WORKERS


def test_fast_tick_does_not_touch_cmux_under_dry(tmp_path, monkeypatch):
    """`--dry` promises never to act, but the fast tick is a second path to the
    same effects: it renames and recolours the user's LIVE cmux workspaces every
    30s, independent of the reconcile cycle's `ctx.dry`. The disk-cache
    republish beside it is local and deliberately still runs."""
    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})

    import cockpit.cockpit as cockpit

    importlib.reload(cockpit)
    touched: list[str] = []
    monkeypatch.setattr(
        cockpit,
        "workspace_state",
        lambda: ({"workspace:1": "n"}, {"workspace:1": tmp_path}),
    )
    monkeypatch.setattr(
        cockpit, "reconcile_workspace_names", lambda *_a: touched.append("rename")
    )
    monkeypatch.setattr(
        cockpit, "_tint_repo_workspaces", lambda *_a: touched.append("tint")
    )
    republished: list[bool] = []
    monkeypatch.setattr(
        cockpit, "republish_pr_caches_from_disk", lambda: republished.append(True)
    )
    monkeypatch.setattr(
        cockpit,
        "load_config",
        lambda: {"repos": [{"path": str(tmp_path), "name": "r"}]},
    )
    monkeypatch.setattr(cockpit, "worktrees", lambda *_a: [])
    monkeypatch.setattr(cockpit.diff_comments, "pending_by_root", lambda _roots: {})

    cockpit._fast_tick(cockpit._build_state(True))
    assert touched == []
    assert republished == [True]  # local republish still runs

    cockpit._fast_tick(cockpit._build_state(False))
    assert touched == ["rename", "tint"]


def test_fast_tick_restores_a_lost_trailing_fold(tmp_path, monkeypatch):
    """A trailing fold lost mid-interval is otherwise rebuilt only by the
    cross-repo pass at the end of a full slow cycle, so the sidebar reads flat
    for up to `slow_poll_interval_seconds`. The fast tick replays the slow
    pass's own record instead, healing it in ~30s."""
    import cockpit.cockpit as cockpit

    importlib.reload(cockpit)

    repo = tmp_path / "repo"
    repo.mkdir()
    seen: list = []
    state = {"pill_state": {"fold:moon.zzz:Env": {"name": "x", "refs": ["ws:1"]}}}

    monkeypatch.setattr(
        cockpit, "load_config", lambda: {"repos": [{"path": str(repo)}]}
    )
    monkeypatch.setattr(
        cockpit, "worktrees", lambda _p, _prefix="", _name="", _tag="": []
    )
    monkeypatch.setattr(cockpit, "workspace_state", lambda: ({}, {}))
    monkeypatch.setattr(cockpit, "republish_pr_caches_from_disk", lambda: None)
    monkeypatch.setattr(cockpit, "restore_trailing_folds", lambda ps: seen.append(ps))
    monkeypatch.setattr(cockpit.diff_comments, "pending_by_root", lambda _roots: {})

    cockpit._fast_tick(state)

    assert seen == [state["pill_state"]]


def test_fast_tick_does_not_restore_folds_under_dry(tmp_path, monkeypatch):
    """`--dry` promises not to touch the user's live cmux workspaces, and
    rebuilding a fold spawns an anchor workspace."""
    import cockpit.cockpit as cockpit

    importlib.reload(cockpit)

    repo = tmp_path / "repo"
    repo.mkdir()
    seen: list = []

    monkeypatch.setattr(
        cockpit, "load_config", lambda: {"repos": [{"path": str(repo)}]}
    )
    monkeypatch.setattr(
        cockpit, "worktrees", lambda _p, _prefix="", _name="", _tag="": []
    )
    monkeypatch.setattr(cockpit, "workspace_state", lambda: ({}, {}))
    monkeypatch.setattr(cockpit, "republish_pr_caches_from_disk", lambda: None)
    monkeypatch.setattr(cockpit, "restore_trailing_folds", lambda ps: seen.append(ps))
    monkeypatch.setattr(cockpit.diff_comments, "pending_by_root", lambda _roots: {})

    cockpit._fast_tick({"dry": True})

    assert seen == []


def _fast_tick_env(tmp_path, monkeypatch):
    """Minimal fast-tick wiring with one live workspace, returning the module
    and the refs `reassert_idle_pills` was handed."""
    import cockpit.cockpit as cockpit
    from cockpit.lib.git import Worktree

    importlib.reload(cockpit)
    repo = tmp_path / "repo"
    repo.mkdir()
    wt = Worktree(path=repo / "feat", branch="khivi/feat")
    seen: list = []
    monkeypatch.setattr(
        cockpit, "load_config", lambda: {"repos": [{"path": str(repo)}]}
    )
    monkeypatch.setattr(
        cockpit, "worktrees", lambda _p, _prefix="", _name="", _tag="": [wt]
    )
    monkeypatch.setattr(cockpit, "write_git_state_cache", lambda _p, _name="": None)
    monkeypatch.setattr(cockpit, "write_worktree_cost_cache", lambda _p: None)
    monkeypatch.setattr(cockpit, "write_diff_comments_cache", lambda _p, _n: None)
    monkeypatch.setattr(cockpit.diff_comments, "pending_by_root", lambda _roots: {})
    monkeypatch.setattr(
        cockpit, "workspace_state", lambda: ({}, {"workspace:1": repo / "feat"})
    )
    monkeypatch.setattr(cockpit, "reconcile_workspace_names", lambda n, c, w: None)
    monkeypatch.setattr(cockpit, "republish_pr_caches_from_disk", lambda: None)
    monkeypatch.setattr(cockpit, "restore_trailing_folds", lambda _s: None)

    def _record(refs) -> list:
        seen.append(list(refs))
        return []

    monkeypatch.setattr(cockpit, "reassert_idle_pills", _record)
    return cockpit, seen


def test_fast_tick_reasserts_idle_pills_for_live_workspaces(tmp_path, monkeypatch):
    cockpit, seen = _fast_tick_env(tmp_path, monkeypatch)
    cockpit._fast_tick({})
    assert seen == [["workspace:1"]]


def test_fast_tick_does_not_reassert_idle_pills_under_dry(tmp_path, monkeypatch):
    """`--dry` promises not to touch live cmux, and the re-assert writes a pill
    into the user's real workspace — same gate as the rename and the tint."""
    cockpit, seen = _fast_tick_env(tmp_path, monkeypatch)
    cockpit._fast_tick({"dry": True})
    assert seen == []
