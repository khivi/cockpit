"""Argument-semantics tests for spawn.main().

Complements test_detect_source.py (positional classification) and
test_resolve_worktree.py (branch resolution unit tests). Tests here
exercise main()'s argument promotion, mutex validation, and dispatch
across the four top-level modes: positional, --branch/--pr, --name,
--cwd, --skill — including the --repo override.

Cmux invocation (`new-workspace` + workspace queries) and the daemon
kick are stubbed so main() can run end-to-end against the tmp git repo
from cockpit_repo without spawning anything.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture
def spawn_main(cockpit_repo, monkeypatch, capsys):
    """Returns `run(argv) -> (exit_code, stdout, stderr)`.

    Patches the cmux + daemon hooks on the `spawn` module so main() runs
    its real argument-handling + resolve_worktree paths without ever
    creating a cmux workspace.

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
    """Extract a `--key value` arg from a cmux positional call."""
    flag = f"--{key}"
    for i, a in enumerate(call_args):
        if a == flag and i + 1 < len(call_args):
            return call_args[i + 1]
    raise AssertionError(f"flag {flag} not in {call_args}")


# ---------- mutex validation ----------


@pytest.mark.parametrize(
    "argv",
    [
        ["khivi/foo", "--branch", "khivi/bar"],
        ["khivi/foo", "--pr", "1"],
        ["khivi/foo", "--name", "x"],
        ["khivi/foo", "--skill", "x"],
    ],
)
def test_positional_is_mutex_with_explicit_args(spawn_main, argv):
    code, _out, err = spawn_main(argv)
    assert code == 1
    assert "mutually exclusive" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["--cwd", "/tmp/x", "--branch", "khivi/foo"],
        ["--cwd", "/tmp/x", "--pr", "1"],
        ["--cwd", "/tmp/x", "--skill", "s"],
    ],
)
def test_cwd_is_mutex_with_branch_pr_skill(spawn_main, argv):
    code, _out, err = spawn_main(argv)
    assert code == 1
    assert "--cwd" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["--skill", "s", "--branch", "khivi/foo"],
        ["--skill", "s", "--pr", "1"],
        ["--skill", "s", "--cwd", "/tmp/x"],
    ],
)
def test_skill_is_mutex_with_branch_pr_cwd(spawn_main, argv):
    code, _out, err = spawn_main(argv)
    assert code == 1
    assert "--skill" in err


def test_no_source_args_is_error(spawn_main):
    code, _out, err = spawn_main(["--repo", "testrepo"])
    assert code == 1
    assert "required" in err or "positional" in err


def test_unknown_repo_exits_one(spawn_main):
    code, _out, err = spawn_main(["--name", "foo", "--repo", "nonexistent"])
    assert code == 1
    assert "nonexistent" in err
    assert "no configured repo" in err


# ---------- --name semantics ----------


def test_name_alone_promotes_to_new_prefixed_branch(spawn_main, cockpit_repo):
    """`--name foo` alone: workspace short = "foo", branch = "khivi/foo"
    created fresh (from_name=True path)."""
    code, out, _err = spawn_main(["--name", "foo", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/foo" in out
    assert len(spawn_main.cmux_calls) == 1
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "name") == "foo"


def test_name_with_branch_keeps_branch_and_uses_name_as_short(
    spawn_main, cockpit_repo, push_branch
):
    """`--name short --branch khivi/explicit`: branch is the explicit
    value (not promoted from --name); workspace short = "short"."""
    push_branch("khivi/explicit")
    code, out, _err = spawn_main(
        ["--name", "shortname", "--branch", "khivi/explicit", "--repo", "testrepo"]
    )
    assert code == 0
    assert "on khivi/explicit" in out
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "name") == "shortname"


def test_name_alone_does_not_use_existing_remote_branch(
    spawn_main, cockpit_repo, push_branch
):
    """Regression: `--name cship` with `khivi/foo/cship` on remote must NOT
    suffix-match. Must create new khivi/cship cleanly."""
    push_branch("khivi/foo/cship")
    code, out, _err = spawn_main(["--name", "cship", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/cship" in out


# ---------- --branch semantics ----------


def test_branch_alone_uses_branch_short_as_workspace_name(
    spawn_main, cockpit_repo, push_branch
):
    """`--branch khivi/feature` alone: workspace short = "feature" (last
    segment of branch slug)."""
    push_branch("khivi/feature")
    code, out, _err = spawn_main(["--branch", "khivi/feature", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/feature" in out
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "name") == "feature"


def test_positional_branch_dispatches_to_branch_mode(
    spawn_main, cockpit_repo, push_branch
):
    """Positional branch name (no #, no URL): branch mode, attaches."""
    push_branch("khivi/positional-branch")
    code, out, _err = spawn_main(["khivi/positional-branch", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/positional-branch" in out


# ---------- --cwd semantics ----------


def test_cwd_creates_directory_and_spawns_without_worktree(
    spawn_main, cockpit_repo, tmp_path
):
    """`--cwd <path>`: skips repo discovery and worktree; creates the dir
    if missing; workspace cwd = the given path; short = slugified dir name."""
    target = tmp_path / "freestanding-dir"
    assert not target.exists()
    code, out, _err = spawn_main(["--cwd", str(target)])
    assert code == 0
    assert target.exists()
    assert "(no worktree)" in out
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "cwd") == str(target)
    assert _cmux_kwarg(call, "name") == "freestanding-dir"


def test_cwd_with_name_overrides_workspace_short(spawn_main, cockpit_repo, tmp_path):
    target = tmp_path / "some-dir"
    code, _out, _err = spawn_main(["--cwd", str(target), "--name", "custom-short"])
    assert code == 0
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "name") == "custom-short"


# ---------- --skill semantics ----------


def test_skill_resolves_global_skill_file(
    spawn_main, cockpit_repo, tmp_path, monkeypatch
):
    """Global skill (~/.claude/skills/<name>/skill.md) takes precedence;
    workspace cwd = $HOME by default; first-turn prompt = "/<name>"."""
    fake_home = tmp_path / "home"
    skill_dir = fake_home / ".claude" / "skills" / "myskill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("# myskill\n")
    monkeypatch.setenv("HOME", str(fake_home))

    code, out, _err = spawn_main(["--skill", "myskill"])
    assert code == 0
    assert "(no worktree)" in out
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "name") == "myskill"
    assert _cmux_kwarg(call, "cwd") == str(fake_home)


def test_skill_missing_errors(spawn_main, cockpit_repo, tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    code, _out, err = spawn_main(["--skill", "nope", "--repo", "testrepo"])
    assert code == 1
    assert "not found" in err
