"""Argument-semantics tests for spawn.main().

Complements test_detect_source.py (positional classification) and
test_resolve_worktree.py (branch resolution unit tests). Tests here
exercise main()'s argument validation and dispatch.

Contract enforced:
  - Exactly one of {positional, --branch, --pr, --name, --skill} may be
    given (strict mutex). --cwd alone is a valid 6th mode.
  - --name and --skill require --repo <n> or --cwd <path>.
  - --cwd cannot combine with positional/--branch/--pr.
  - --cwd path must exist.
  - --repo <name> must reference a configured repo.

Cmux + daemon hooks are stubbed so main() runs end-to-end against the
tmp git repo from cockpit_repo without spawning anything.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture
def spawn_main(cockpit_repo, monkeypatch, capsys):
    """Returns `run(argv) -> (exit_code, stdout, stderr)`.

    Captures cmux call args on `spawn_main.cmux_calls` for assertions.
    """
    import spawn

    cmux_calls: list[tuple] = []

    def fake_cmux(*args, **kwargs):
        cmux_calls.append(args)
        return None

    monkeypatch.setattr(spawn, "cmux", fake_cmux)
    monkeypatch.setattr(spawn, "workspace_names", lambda: {})
    monkeypatch.setattr(spawn, "kick_running", lambda *a, **kw: None)

    def _run(argv: list[str]) -> tuple[int, str, str]:
        monkeypatch.setattr(sys, "argv", ["spawn", *argv])
        try:
            code = spawn.main()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    _run.cmux_calls = cmux_calls  # type: ignore[attr-defined]
    return _run


def _cmux_kwarg(call_args: tuple, key: str) -> str:
    flag = f"--{key}"
    for i, a in enumerate(call_args):
        if a == flag and i + 1 < len(call_args):
            return call_args[i + 1]
    raise AssertionError(f"flag {flag} not in {call_args}")


# ---------- source mutex (strict: pick at most one) ----------

_SOURCE_PAIRS = [
    # (argv, label-of-both-flags-to-find-in-stderr)
    (["pos-branch", "--branch", "khivi/b"], "positional"),
    (["pos-branch", "--pr", "1"], "positional"),
    (["pos-branch", "--name", "x"], "positional"),
    (["pos-branch", "--skill", "x"], "positional"),
    (["--branch", "khivi/b", "--pr", "1"], "--branch"),
    (["--branch", "khivi/b", "--name", "x"], "--branch"),
    (["--branch", "khivi/b", "--skill", "x"], "--branch"),
    (["--pr", "1", "--name", "x"], "--pr"),
    (["--pr", "1", "--skill", "x"], "--pr"),
    (["--name", "x", "--skill", "y"], "--name"),
]


@pytest.mark.parametrize("argv,present", _SOURCE_PAIRS)
def test_source_flags_are_strictly_mutex(spawn_main, argv, present):
    code, _out, err = spawn_main(argv)
    assert code == 1
    assert "at most one" in err
    assert present in err


def test_no_source_and_no_cwd_is_error(spawn_main):
    code, _out, err = spawn_main(["--repo", "testrepo"])
    assert code == 1
    assert "required" in err


# ---------- --cwd combinations ----------


@pytest.mark.parametrize(
    "argv",
    [
        ["pos", "--cwd", "/tmp"],
        ["--branch", "khivi/x", "--cwd", "/tmp"],
        ["--pr", "1", "--cwd", "/tmp"],
    ],
)
def test_cwd_cannot_combine_with_positional_branch_or_pr(spawn_main, argv, tmp_path):
    # Use a real existing dir so it's the mutex (not the existence check) that fires.
    argv = [str(tmp_path) if a == "/tmp" else a for a in argv]
    code, _out, err = spawn_main(argv)
    assert code == 1
    assert "--cwd" in err


def test_cwd_path_must_exist(spawn_main, tmp_path):
    missing = tmp_path / "does-not-exist"
    code, _out, err = spawn_main(["--cwd", str(missing)])
    assert code == 1
    assert "does not exist" in err


def test_cwd_alone_with_existing_dir(spawn_main, tmp_path):
    target = tmp_path / "freestanding"
    target.mkdir()
    code, out, _err = spawn_main(["--cwd", str(target)])
    assert code == 0
    assert "(no worktree)" in out
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "cwd") == str(target)
    assert _cmux_kwarg(call, "name") == "freestanding"


# ---------- --name semantics ----------


def test_name_requires_repo_or_cwd(spawn_main):
    code, _out, err = spawn_main(["--name", "foo"])
    assert code == 1
    assert "--repo" in err and "--cwd" in err


def test_name_with_repo_creates_new_prefixed_branch(spawn_main):
    code, out, _err = spawn_main(["--name", "foo", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/foo" in out
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "name") == "foo"


def test_name_with_cwd_spawns_at_path_without_branch(spawn_main, tmp_path):
    target = tmp_path
    code, out, _err = spawn_main(["--name", "myshort", "--cwd", str(target)])
    assert code == 0
    assert "(no worktree)" in out
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "cwd") == str(target)
    assert _cmux_kwarg(call, "name") == "myshort"


def test_name_with_repo_ignores_unrelated_suffix_remote(spawn_main, push_branch):
    """Regression: --name cship with khivi/foo/cship on remote still creates
    khivi/cship cleanly (no suffix-match fetch)."""
    push_branch("khivi/foo/cship")
    code, out, _err = spawn_main(["--name", "cship", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/cship" in out


# ---------- --branch / positional ----------


def test_branch_alone_uses_branch_short_as_workspace_name(spawn_main, push_branch):
    push_branch("khivi/feature")
    code, out, _err = spawn_main(["--branch", "khivi/feature", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/feature" in out
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "name") == "feature"


def test_positional_branch_dispatches_to_branch_mode(spawn_main, push_branch):
    push_branch("khivi/positional-branch")
    code, out, _err = spawn_main(["khivi/positional-branch", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/positional-branch" in out


# ---------- --skill semantics ----------


def test_skill_requires_repo_or_cwd(spawn_main):
    code, _out, err = spawn_main(["--skill", "anything"])
    assert code == 1
    assert "--repo" in err and "--cwd" in err


def test_skill_with_repo_resolves_global_skill(spawn_main, tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    skill_dir = fake_home / ".claude" / "skills" / "myskill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("# myskill\n")
    monkeypatch.setenv("HOME", str(fake_home))

    code, out, _err = spawn_main(["--skill", "myskill", "--repo", "testrepo"])
    assert code == 0
    assert "(no worktree)" in out
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "name") == "myskill"


def test_skill_with_cwd_uses_path_as_workspace_cwd(spawn_main, tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    skill_dir = fake_home / ".claude" / "skills" / "myskill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("# myskill\n")
    monkeypatch.setenv("HOME", str(fake_home))
    target = tmp_path / "ws-dir"
    target.mkdir()

    code, out, _err = spawn_main(["--skill", "myskill", "--cwd", str(target)])
    assert code == 0
    assert "(no worktree)" in out
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "cwd") == str(target)
    assert _cmux_kwarg(call, "name") == "myskill"


def test_skill_missing_errors(spawn_main, tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    code, _out, err = spawn_main(["--skill", "nope", "--repo", "testrepo"])
    assert code == 1
    assert "not found" in err


# ---------- --repo validation ----------


def test_unknown_repo_exits_one(spawn_main):
    code, _out, err = spawn_main(["--name", "foo", "--repo", "nonexistent"])
    assert code == 1
    assert "nonexistent" in err
    assert "no configured repo" in err
