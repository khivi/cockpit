"""Tests for cockpit/lib/registry.py — `register_cwd` (bare `cockpit new`).

Leaf-module style: run against a real `git init` repo on `tmp_path`, with the
`gh`-touching helpers (`gh_self_user`, `default_branch`) stubbed so the branch
prefix / base are deterministic regardless of the dev's `gh` auth state.
"""

from __future__ import annotations

import json
import subprocess

import pytest

import cockpit.lib.registry as registry
from cockpit.lib.registry import register_cwd

# GIT_* env vars pre-commit/pre-push inject would redirect the tmp-repo `git`
# calls (and `main_worktree_path`) at the outer repo — strip them like the
# shared `cockpit_repo` fixture does.
_GIT_ENV_LEAKS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
)


def _git(cwd, *args) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _strip_git_env(monkeypatch) -> None:
    for var in _GIT_ENV_LEAKS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def fresh_repo(tmp_path, monkeypatch):
    """A `git init` repo NOT present in cockpit's config, with cwd set into it
    and an isolated COCKPIT_HOME (no config.json yet → register writes a fresh
    one). `gh` helpers stubbed deterministic."""
    _strip_git_env(monkeypatch)
    home = tmp_path / "cockpit-home"
    home.mkdir()
    # Pre-seed an empty config so `ensure_state_dirs` doesn't copy the shipped
    # example (which carries its own demo repos) over our isolated home.
    (home / "config.json").write_text(json.dumps({"repos": []}))
    monkeypatch.setenv("COCKPIT_HOME", str(home))

    import importlib

    import cockpit.lib.config as cfg_mod

    importlib.reload(cfg_mod)  # re-read COCKPIT_HOME into config.CONFIG_PATH

    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed")
    monkeypatch.chdir(repo)

    monkeypatch.setattr(registry, "gh_self_user", lambda: "khivi")
    monkeypatch.setattr(registry, "default_branch", lambda _repo: "main")
    return repo, home


def test_register_cwd_in_place_writes_entry(fresh_repo):
    repo, home = fresh_repo
    entry = register_cwd(in_place=True)

    assert entry["name"] == "proj"
    assert entry["path"] == str(repo.resolve())
    assert entry["in_place"] is True
    assert entry["branch_prefix"] == "khivi/"
    assert entry["default_base"] == "main"

    on_disk = json.loads((home / "config.json").read_text())
    assert on_disk["repos"][0]["in_place"] is True
    assert on_disk["repos"][0]["path"] == str(repo.resolve())


def test_register_cwd_in_place_is_idempotent(fresh_repo, capsys):
    repo, home = fresh_repo
    register_cwd(in_place=True)
    capsys.readouterr()  # drop the "added repo" line

    again = register_cwd(in_place=True)
    assert again["in_place"] is True
    assert "already managed" in capsys.readouterr().out

    on_disk = json.loads((home / "config.json").read_text())
    assert len(on_disk["repos"]) == 1  # no duplicate


def test_register_cwd_off_github_empty_prefix(fresh_repo, monkeypatch):
    repo, home = fresh_repo
    # No gh user (e.g. off-GitHub repo): gh_self_user raises → prefix empty.
    monkeypatch.setattr(
        registry, "gh_self_user", lambda: (_ for _ in ()).throw(RuntimeError("no gh"))
    )
    entry = register_cwd(in_place=True)
    assert entry["branch_prefix"] == ""
    assert entry["in_place"] is True


def test_register_cwd_in_place_skips_prefix_prompt(fresh_repo, monkeypatch):
    repo, _home = fresh_repo
    # in_place must NOT prompt even on a TTY — fail loudly if it tries to.
    monkeypatch.setattr(
        registry, "_prompt_branch_prefix", lambda _d: pytest.fail("prompted")
    )
    entry = register_cwd(in_place=True)
    assert entry["branch_prefix"] == "khivi/"


def test_register_cwd_non_git_raises(tmp_path, monkeypatch):
    _strip_git_env(monkeypatch)
    home = tmp_path / "cockpit-home"
    home.mkdir()
    monkeypatch.setenv("COCKPIT_HOME", str(home))
    import importlib

    import cockpit.lib.config as cfg_mod

    importlib.reload(cfg_mod)

    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    monkeypatch.chdir(plain)
    with pytest.raises(RuntimeError, match="not in a git repo"):
        register_cwd(in_place=True)
