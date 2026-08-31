"""Shared fixtures: tmp git repo with origin remote + isolated COCKPIT_HOME."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_GIT_ENV_LEAKS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
)


@pytest.fixture(autouse=True)
def _hermetic_git_env():
    """Run every test against git's *defaults*, not the developer's machine.

    Two independent leaks, both of which have produced a green local run and a
    red CI one (or worse):

      * **`GIT_DIR` / `GIT_INDEX_FILE` / … ** are exported by git while it runs
        a hook, so the pre-commit- and pre-push-stage suites inherit them. A
        test that builds its own repo under `tmp_path` then shells out to `git
        add` stages into the *outer* repo's index instead — observed as a
        staged wholesale deletion of every tracked file in this repo. Stripping
        them was previously `cockpit_repo`'s job, which only protected tests
        that happened to use that fixture.
      * **`GIT_CONFIG_GLOBAL` / `GIT_CONFIG_SYSTEM` → `/dev/null`** neutralizes
        `~/.gitconfig` and the system config. A developer has `user.email` set;
        a CI runner does not, so a test that commits without supplying its own
        identity passes locally and fails in CI with a bare exit 128. The same
        goes for anything else personal (`commit.gpgsign`, `pull.ff`,
        `init.defaultBranch`, aliases). Tests that need to commit set their own
        identity — `tests/conftest._git` and `tests/lib/test_git._committer_env`
        both do.

    Deliberately does NOT request `monkeypatch`, for the ordering reason spelled
    out in `_isolate_hidden_repos` — a suite-wide autouse fixture that does
    forces monkeypatch's setup ahead of every module-level autouse fixture and
    flips their relative teardown order.
    """
    managed = (*_GIT_ENV_LEAKS, "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")
    prev = {k: os.environ.get(k) for k in managed}
    for var in _GIT_ENV_LEAKS:
        os.environ.pop(var, None)
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull
    yield
    for key, value in prev.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(autouse=True)
def _reset_config_cache():
    """`load_config()` caches the parsed config for the process lifetime. Reset
    it around every test so each starts like a fresh process and one test's
    config can't leak into the next. Imported inside the body to pick up the
    live module object (some fixtures `importlib.reload` the config module)."""
    import cockpit.lib.config as cockpit_config

    cockpit_config.reset_config_cache()
    yield
    cockpit_config.reset_config_cache()


@pytest.fixture(autouse=True)
def _isolate_hidden_repos(tmp_path):
    """`hidden.HIDDEN_PATH` is resolved off the real `COCKPIT_HOME` at import, so
    without this every test would read the developer's own parked-repo list (and
    a `toggle_hidden` in a test would write to it).

    Deliberately does NOT request `monkeypatch`: a suite-wide autouse fixture
    that does forces monkeypatch's setup ahead of every module-level autouse
    fixture, which flips their relative *teardown* order — that's what silently
    broke `tests/lib/test_colors.py::_reset_colors_module` (it reloaded
    lib.colors before monkeypatch had unset $NO_COLOR, leaving every later
    module with colorless colorizers)."""
    import cockpit.lib.hidden as hidden_mod

    prev = hidden_mod.HIDDEN_PATH
    hidden_mod.HIDDEN_PATH = tmp_path / "hidden-repos.json"
    yield
    hidden_mod.HIDDEN_PATH = prev


@pytest.fixture(autouse=True)
def _isolate_pidfile(tmp_path):
    """`daemon.PID_FILE` is resolved off the real `COCKPIT_HOME` at import, so
    without this any test reaching `_fast_tick` (which calls `reassert_pidfile`)
    writes the developer's own `~/.config/cockpit/cockpit.pid` — planting a
    stale pid that later makes `cockpit close` report a daemon that isn't there.

    It also made five `test_fast_tick_*` tests depend on a *different* test
    having created `~/.config/cockpit/` first: serially something always had, so
    they passed; under `-n auto` they land on workers that never ran it and fail
    on the missing directory. A test that only passes because of another test's
    side effect is the bug, not the parallelism.

    Same shape as `_isolate_hidden_repos` above, including not requesting
    `monkeypatch` — see its docstring for why that ordering matters.
    """
    import cockpit.lib.daemon as daemon_mod

    prev = daemon_mod.PID_FILE
    daemon_mod.PID_FILE = tmp_path / "cockpit.pid"
    yield
    daemon_mod.PID_FILE = prev


@pytest.fixture(autouse=True)
def _isolate_runtime_dir(tmp_path):
    """Point `$COCKPIT_RUNTIME_DIR` — the pidfile + close-request queue — at a
    per-test tmp path.

    The runtime dir deliberately does NOT follow `$COCKPIT_HOME` (that is the
    whole point of the split: COCKPIT_HOME may be synced, this must not be), so
    the many fixtures that isolate only COCKPIT_HOME do not cover it. Without
    this, any test reaching `enqueue` writes a real teardown marker into the
    developer's own queue and the next live daemon tick drains it —
    `orchestrators.teardown` against whatever path the test happened to name.
    That is not hypothetical: the first run after the split deposited a
    `workspace:99` marker and a fake `4242` pidfile in the author's real
    `~/.local/state/cockpit`.

    It sets the **environment variable** and not just the module attributes,
    because several fixtures `importlib.reload(cockpit.lib.config)` after
    setting `$COCKPIT_HOME` — a reload re-derives these paths from the
    environment and would otherwise silently undo an attribute-only patch,
    which is exactly how the markers above escaped. The attributes are patched
    too, for modules already imported that nothing reloads.

    Same shape as `_hermetic_git_env` above, including not requesting
    `monkeypatch` — see `_isolate_hidden_repos` for why that ordering matters.
    """
    import cockpit.lib.config as config_mod
    import cockpit.lib.daemon_signal as signal_mod

    runtime = tmp_path / "runtime"
    prev_env = os.environ.get("COCKPIT_RUNTIME_DIR")
    os.environ["COCKPIT_RUNTIME_DIR"] = str(runtime)
    prev = (
        config_mod.COCKPIT_RUNTIME_DIR,
        config_mod.PID_FILE,
        signal_mod.STATE_DIR,
        signal_mod.PID_FILE,
    )
    config_mod.COCKPIT_RUNTIME_DIR = runtime
    config_mod.PID_FILE = runtime / "cockpit.pid"
    signal_mod.STATE_DIR = runtime / "close-requests"
    # daemon_signal does `from .config import PID_FILE`, binding it by value at
    # import — so patching config's alone leaves `kick_running` reading (and
    # `os.kill`-ing, and unlinking) the developer's real pidfile.
    signal_mod.PID_FILE = runtime / "cockpit.pid"
    yield
    (
        config_mod.COCKPIT_RUNTIME_DIR,
        config_mod.PID_FILE,
        signal_mod.STATE_DIR,
        signal_mod.PID_FILE,
    ) = prev
    if prev_env is None:
        os.environ.pop("COCKPIT_RUNTIME_DIR", None)
    else:
        os.environ["COCKPIT_RUNTIME_DIR"] = prev_env


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    res = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return res.stdout.strip()


@dataclass
class RepoFixture:
    repo: Path  # local clone with origin remote set
    origin: Path  # bare repo serving as origin
    cockpit_home: Path
    repo_name: str = "testrepo"
    branch_prefix: str = "khivi/"
    default_base: str = "main"


@pytest.fixture
def cockpit_repo(tmp_path, monkeypatch) -> RepoFixture:
    """Tmp local git repo with `origin` set to a bare repo, plus a fake
    cockpit config.json pointing at it. `main` exists on both sides.

    Strips GIT_* env vars (GIT_INDEX_FILE etc.) so test subprocesses can't
    corrupt the outer repo's staged index when run under a pre-commit hook.
    """
    for var in _GIT_ENV_LEAKS:
        monkeypatch.delenv(var, raising=False)
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    cockpit_home = tmp_path / "cockpit-home"
    cockpit_home.mkdir()

    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "init", "-b", "main", str(repo))
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "seed")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")

    cfg = {
        "repos": [
            {
                "name": "testrepo",
                "path": str(repo),
                "branch_prefix": "khivi/",
                "default_base": "main",
            }
        ],
        "slow_poll_interval_seconds": 300,
    }
    (cockpit_home / "config.json").write_text(json.dumps(cfg))
    monkeypatch.setenv("COCKPIT_HOME", str(cockpit_home))

    # COCKPIT_HOME is read at module-import time in lib.config; reload so the
    # env override actually takes effect.
    import importlib

    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    # spawn imports find_repo_by_name from lib.config; refresh that binding.
    import cockpit.spawn as spawn

    importlib.reload(spawn)

    return RepoFixture(repo=repo, origin=origin, cockpit_home=cockpit_home)


@pytest.fixture
def push_branch(cockpit_repo):
    """Push a fresh branch (off `main` by default) to origin and prune locally.

    Returns a callable: push_branch(name, base="main") -> None.
    """

    def _push(name: str, base: str = "main") -> None:
        _git(cockpit_repo.repo, "branch", name, base)
        _git(cockpit_repo.repo, "push", "origin", f"{name}:{name}")
        _git(cockpit_repo.repo, "branch", "-D", name)

    return _push
