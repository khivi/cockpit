"""Shared fixtures: tmp git repo with origin remote + isolated COCKPIT_HOME."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


# Binaries that reach the developer's OWN live cockpit. `cockpit`/`cockpit.cli`
# is here because `_bg_spawn_pr` launches a detached `python -m cockpit.cli new`,
# which is a whole real daemon-side spawn against the real machine.
_LIVE_BINARIES = frozenset({"cmux", "limux", "cockpit"})

# A test that builds its own fake `cmux` under `tmp_path` and puts it on PATH is
# doing the right thing — `tests/lib/test_events.py` drives the real
# subprocess/stream logic that way. Only the binary the DEVELOPER installed is
# off limits, so the guards below key on where the executable resolves, not on
# its name alone.
_TMP_ROOT = Path(tempfile.gettempdir()).resolve()


def _under_tmp(path: str) -> bool:
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        return False
    return resolved == _TMP_ROOT or _TMP_ROOT in resolved.parents


def _live_backend_argv(cmd: object, resolve=None) -> str | None:
    """The live binary `cmd` would exec, or None. Never raises: a guard that
    blows up on an argv shape it didn't anticipate would break unrelated tests
    rather than protect them."""
    if isinstance(cmd, str | bytes | os.PathLike):
        argv = [os.fsdecode(cmd)]
    elif isinstance(cmd, list | tuple):
        try:
            argv = [os.fsdecode(a) for a in cmd]
        except TypeError:
            return None
    else:
        return None
    if not argv:
        return None
    head = Path(argv[0]).name
    if head in _LIVE_BINARIES:
        # A bare name is not a path, so `_under_tmp` on it would test the
        # *cwd* — resolve it the way exec will, against PATH. `lib.events`
        # spawns `["cmux", "events", …]` by name, and its tests legitimately
        # put a fake `cmux` on PATH under `tmp_path`.
        found = argv[0]
        if os.sep not in argv[0] and resolve is not None:
            found = resolve(argv[0]) or argv[0]
        if not _under_tmp(found):
            return head
    if any(a == "cockpit.cli" for a in argv[1:]):
        return "cockpit"
    return None


@pytest.fixture(autouse=True)
def _no_live_backend(request):
    """Make it impossible for a test to reach the developer's real cmux.

    Every fixture above isolates a *file* cockpit writes. This one isolates the
    thing that has no path to redirect: the backend process. `cmux create` /
    `cmux send` act on the live sidebar, and nothing about them is undone by a
    tmp_path.

    Not hypothetical, and the failure mode is worse than a dirtied file. A
    helper was refactored to call `workspace_cwds()` from inside `lib.cmux`
    while every TUI test patched it in `cockpit.tui.app` — patching a name in
    one module never rebinds another module's copy, so those paths quietly went
    live. Under `-n auto` four xdist workers each spawned a real workspace in
    the developer's checkout and sent each one cockpit's orphan-nudge prompt,
    naming a test fixture's branch. Four live Claude sessions, told to resume
    work on a branch that does not exist.

    Two independent layers, because one patch is one thing to get wrong:

      * `subprocess.Popen` — the universal net. `lib.run` and `subprocess.run`
        both bottom out here, so this catches `cmux()`, the raw `run(["limux",
        …])` at `cmux.py`'s workspace listing, `events`' own `Popen`, and
        `spawn`'s detached one. Filtered by argv, so git and gh still run.
      * `lib.cmux.run` — the binding `cmux()` actually calls (`from . import
        run` copies the function object, so patching `lib.run` would miss it).
        Redundant with the net above and kept for the error message, which
        names the verb at the point a reader is looking.

    Loud, never silent: `cmux(..., check=False)` returns `""` on a missing
    binary, and a test that believes it talked to cmux and got nothing back is
    how this goes unnoticed for another six months.

    Opt out with `@pytest.mark.real_backend` — the e2e suite, which exists to
    run the real binaries.
    """
    if request.node.get_closest_marker("real_backend"):
        yield
        return

    import shutil as shutil_mod
    import subprocess as subprocess_mod

    import cockpit.lib.cmux as cmux_mod

    def _blocked(binary: str, cmd: object) -> None:
        raise RuntimeError(
            f"blocked: this test tried to exec the real {binary!r} "
            f"({cmd!r}). Mock the collaborator in the module that CALLS it, "
            "or mark the test @pytest.mark.real_backend."
        )

    real_popen = subprocess_mod.Popen
    real_run = cmux_mod.run
    real_which = shutil_mod.which

    def guarded_popen(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        hit = _live_backend_argv(cmd, real_which)
        if hit:
            _blocked(hit, cmd)
        return real_popen(cmd, *args, **kwargs)

    def guarded_run(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        hit = _live_backend_argv(cmd, real_which)
        if hit:
            _blocked(hit, cmd)
        return real_run(cmd, *args, **kwargs)

    # Third layer, and the one that keeps the suite HONEST rather than merely
    # safe: a unit-test machine has no cmux installed. `resolve_tool()`'s `auto`
    # then resolves to `none` and `_resolve_binary` returns None, so every call
    # degrades through the production gates `tool: none` already validates —
    # the same axis `dev.sh` relies on — instead of reading the developer's live
    # sidebar and passing because of what happened to be on it. `git` and `gh`
    # are untouched. A test that needs a backend patches `which`, `is_cmux` or
    # `resolve_tool` itself, which is now a visible choice rather than a default.
    def guarded_which(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        found = real_which(cmd, *args, **kwargs)
        # `cockpit` is deliberately NOT hidden: `preflight` only asks whether it
        # is on PATH and prints a warning, which is a read with no side effect,
        # and hiding it turns 30 unrelated preflight assertions red.
        if found and Path(str(cmd)).name in {"cmux", "limux"} and not _under_tmp(found):
            return None
        return found

    # `Popen` is a class, so replacing it with a function is a type error by
    # construction — which is the whole intent here. ruff's B010 rules out the
    # `setattr` spelling that would dodge it, so the ignore is the honest form.
    subprocess_mod.Popen = guarded_popen  # type: ignore[misc,assignment]
    cmux_mod.run = guarded_run
    shutil_mod.which = guarded_which
    try:
        yield
    finally:
        shutil_mod.which = real_which
        subprocess_mod.Popen = real_popen  # type: ignore[misc]
        cmux_mod.run = real_run


# The developer's real cockpit state, resolved ONCE at import — before any
# fixture has moved $COCKPIT_HOME — so the guard below keeps naming the live
# directory no matter what a test does to the environment afterwards.
_REAL_COCKPIT_HOME = Path(
    os.environ.get("COCKPIT_HOME") or Path.home() / ".config" / "cockpit"
).resolve()


@pytest.fixture(autouse=True)
def _isolate_cockpit_home():
    """No test reads or writes the developer's real `$COCKPIT_HOME`.

    The fixtures above each isolate one *derived* path — the parked-repo list,
    the pidfile, the runtime dir — because each was found the hard way after it
    leaked. This isolates the root they all hang off, so a path nobody has
    thought about yet (a new cache cell, a nudge pref, `watch.log`) is covered
    before it is written rather than after.

    Two layers, since the first can be undone by the code under test:

      * `$COCKPIT_HOME` and the already-imported module attributes point at
        `tmp_path`. The env var matters as much as the attributes: several
        fixtures `importlib.reload(cockpit.lib.config)`, which re-derives every
        path from the environment and would silently undo an attribute-only
        patch — the same trap `_isolate_runtime_dir` documents.
      * `config._atomic_write_text`, the funnel every cockpit write goes
        through, raises if the target resolves inside the real home. A test that
        reconstructs a path by hand, or a module that captured one at import
        before this fixture ran, still cannot land a byte in it.

    A test that genuinely wants the real home does not exist; there is no opt
    out on purpose.
    """
    import cockpit.lib.config as config_mod

    # Its own temp dir rather than a child of `tmp_path`: a fixture that plants
    # a directory in `tmp_path` collides with the module fixtures that build
    # their own `cockpit-home` there, and breaks the tests that assert
    # `tmp_path` is empty (`test_atomic_write_leaves_no_temp_behind_when_the_write_fails`). A
    # suite-wide default must be invisible to tests that never think about it.
    home = Path(tempfile.mkdtemp(prefix="cockpit-home-"))
    prev_env = os.environ.get("COCKPIT_HOME")
    os.environ["COCKPIT_HOME"] = str(home)
    prev = (config_mod.COCKPIT_HOME, config_mod.CONFIG_PATH, config_mod.CACHE_DIR)
    config_mod.COCKPIT_HOME = home
    config_mod.CONFIG_PATH = home / "config.json"
    config_mod.CACHE_DIR = home / "cache"

    real_write = config_mod._atomic_write_text

    def guarded_write(path: Path, text: str) -> None:
        resolved = Path(path).resolve()
        if resolved == _REAL_COCKPIT_HOME or _REAL_COCKPIT_HOME in resolved.parents:
            raise RuntimeError(
                f"blocked: this test tried to write the developer's real "
                f"cockpit state at {resolved}. Use the isolated $COCKPIT_HOME "
                "this fixture provides instead of rebuilding the path."
            )
        real_write(path, text)

    config_mod._atomic_write_text = guarded_write
    try:
        yield home
    finally:
        shutil.rmtree(home, ignore_errors=True)
        config_mod._atomic_write_text = real_write
        (
            config_mod.COCKPIT_HOME,
            config_mod.CONFIG_PATH,
            config_mod.CACHE_DIR,
        ) = prev
        if prev_env is None:
            os.environ.pop("COCKPIT_HOME", None)
        else:
            os.environ["COCKPIT_HOME"] = prev_env
