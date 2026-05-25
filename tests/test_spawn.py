"""Tests for scripts/spawn.py.

Three layers:
  - detect_source: pure-function classification of positional input.
  - resolve_worktree: branch/worktree resolution against a real tmp repo.
  - main: argument-validation + dispatch end-to-end (cmux stubbed).
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from scripts.spawn import detect_source

# ────────────────────────────────────────────────────────────────────────────
# detect_source (pure)
# ────────────────────────────────────────────────────────────────────────────


def test_pr_url_returns_pr_mode_and_nwo():
    mode, value, nwo = detect_source("https://github.com/owner/repo/pull/42")
    assert mode == "pr"
    assert value == "42"
    assert nwo == "owner/repo"


def test_pr_url_http_also_matches():
    mode, value, nwo = detect_source("http://github.com/owner/repo/pull/7")
    assert mode == "pr"
    assert value == "7"
    assert nwo == "owner/repo"


def test_hash_prefix_returns_pr_mode_no_nwo():
    mode, value, nwo = detect_source("#123")
    assert mode == "pr"
    assert value == "123"
    assert nwo is None


def test_bare_integer_is_branch_not_pr():
    mode, value, nwo = detect_source("123")
    assert mode == "branch"
    assert value == "123"
    assert nwo is None


def test_branch_name_returns_branch_mode():
    mode, value, nwo = detect_source("khivi/my-feature")
    assert mode == "branch"
    assert value == "khivi/my-feature"
    assert nwo is None


def test_linear_id_uppercase_returns_linear_mode():
    mode, value, nwo = detect_source("PE-1234")
    assert mode == "linear"
    assert value == "PE-1234"
    assert nwo is None


def test_linear_id_lowercase_normalised_to_upper():
    mode, value, nwo = detect_source("pe-1234")
    assert mode == "linear"
    assert value == "PE-1234"


def test_linear_id_inside_path_stays_branch():
    """`khivi/PE-1234-foo` is a branch name, not a Linear id (no fullmatch)."""
    mode, value, _ = detect_source("khivi/PE-1234-foo")
    assert mode == "branch"
    assert value == "khivi/PE-1234-foo"


def test_slack_url_returns_slack_mode():
    url = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
    mode, value, nwo = detect_source(url)
    assert mode == "slack"
    assert value == url
    assert nwo is None


def test_slack_thread_reply_url_still_slack_mode():
    url = (
        "https://acme.slack.com/archives/C0123ABC/p1700000000999999"
        "?thread_ts=1700000000.123456"
    )
    mode, _, _ = detect_source(url)
    assert mode == "slack"


# ────────────────────────────────────────────────────────────────────────────
# resolve_worktree (real tmp repo via cockpit_repo)
# ────────────────────────────────────────────────────────────────────────────


def test_from_name_creates_prefixed_branch_when_free(cockpit_repo):
    from scripts.spawn import resolve_worktree

    wt, branch, attached = resolve_worktree("cship", None, "testrepo", from_name=True)
    assert branch == "khivi/cship"
    assert attached is False
    assert wt.exists()
    assert wt == cockpit_repo.repo.parent / "cship"


def test_from_name_bumps_branch_when_remote_collides(cockpit_repo, push_branch):
    from scripts.spawn import resolve_worktree

    push_branch("khivi/cship")

    wt, branch, attached = resolve_worktree("cship", None, "testrepo", from_name=True)
    assert branch == "khivi/cship-2"
    assert attached is False
    assert wt.exists()


def test_from_name_bumps_branch_when_local_collides(cockpit_repo):
    from scripts.spawn import resolve_worktree

    subprocess.run(
        ["git", "-C", str(cockpit_repo.repo), "branch", "khivi/cship", "main"],
        check=True,
    )

    wt, branch, attached = resolve_worktree("cship", None, "testrepo", from_name=True)
    assert branch == "khivi/cship-2"


def test_from_name_does_not_match_suffix_ref(cockpit_repo, push_branch):
    """Regression: with OLD code, ls-remote --heads origin cship would
    suffix-match a remote like `khivi/foo/cship` and trigger a failing
    `fetch origin cship:cship`. The from_name path must skip the fetch
    dance entirely and create khivi/cship fresh."""
    from scripts.spawn import resolve_worktree

    push_branch("khivi/foo/cship")

    wt, branch, attached = resolve_worktree("cship", None, "testrepo", from_name=True)
    assert branch == "khivi/cship"
    assert attached is False


def test_from_name_creates_branch_from_origin_main(cockpit_repo):
    """New branch's tip must be origin/main, not some stale local ref."""
    from scripts.spawn import resolve_worktree

    wt, _branch, _ = resolve_worktree("cship", None, "testrepo", from_name=True)

    head = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    main_tip = subprocess.run(
        ["git", "-C", str(cockpit_repo.repo), "rev-parse", "origin/main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head == main_tip


def test_unknown_repo_name_raises(cockpit_repo):
    from scripts.spawn import resolve_worktree

    with pytest.raises(ValueError, match="no configured repo"):
        resolve_worktree("cship", None, "nonexistent", from_name=True)


def test_non_from_name_attaches_to_existing_remote_branch(cockpit_repo, push_branch):
    """Regression on the original code path: passing an existing branch
    explicitly (no from_name) should still attach to it, not bump."""
    from scripts.spawn import resolve_worktree

    push_branch("khivi/existing")

    wt, branch, attached = resolve_worktree(
        "khivi/existing", None, "testrepo", from_name=False
    )
    assert branch == "khivi/existing"
    assert wt.exists()


# ────────────────────────────────────────────────────────────────────────────
# main() argument validation + dispatch
# ────────────────────────────────────────────────────────────────────────────
#
# Contract enforced:
#   - Exactly one of {positional, --branch, --pr, --name, --skill} may be
#     given (strict mutex). --cwd alone is a valid 6th mode.
#   - --name and --skill require --repo <n> or --cwd <path>.
#   - --cwd cannot combine with positional/--branch/--pr.
#   - --cwd path must exist.
#   - --repo <name> must reference a configured repo.
#
# Cmux + daemon hooks are stubbed so main() runs end-to-end against the
# tmp git repo from cockpit_repo without spawning anything.


@pytest.fixture
def spawn_main(cockpit_repo, monkeypatch, capsys):
    """Returns `run(argv) -> (exit_code, stdout, stderr)`.

    Captures cmux call args on `spawn_main.cmux_calls` for assertions.
    """
    import scripts.spawn as spawn

    cmux_calls: list[tuple] = []

    def fake_cmux(*args, **kwargs):
        cmux_calls.append(args)
        return None

    monkeypatch.setattr(spawn, "cmux", fake_cmux)
    monkeypatch.setattr(spawn, "workspace_names", lambda: {})
    monkeypatch.setattr(spawn, "kick_running", lambda *a, **kw: None)
    monkeypatch.setattr(spawn, "require_workspace_binary", lambda: None)

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


# ── source mutex (strict: pick at most one) ────────────────────────────────

_SOURCE_PAIRS = [
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


# ── --cwd combinations ─────────────────────────────────────────────────────


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


# ── --name semantics ───────────────────────────────────────────────────────


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


# ── --branch / positional ──────────────────────────────────────────────────


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


# ── linear / slack dispatch ────────────────────────────────────────────────
#
# These exercise main()'s positional path for Linear ids and Slack URLs. The
# `resolve_*` calls are patched on the spawn module so the tests don't hit
# the network; the underlying lib.linear / lib.slack are covered separately
# in tests/lib/.


def test_positional_linear_resolved_creates_derived_branch(spawn_main, monkeypatch):
    import scripts.spawn as spawn
    from scripts.lib.linear import ResolvedIssue

    issue = ResolvedIssue(
        identifier="PE-1234",
        title="Add login flow",
        description="Users need to log in.",
        url="https://linear.app/team/issue/PE-1234",
        branch_name="",
    )
    monkeypatch.setattr(spawn, "resolve_issue", lambda _id: issue)

    code, out, _err = spawn_main(["PE-1234", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/pe-1234-add-login-flow" in out
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "name") == "pe-1234-add-login-flow"
    # Seeded prompt should include the Linear context.
    cmd = _cmux_kwarg(call, "command")
    assert "PE-1234" in cmd
    assert "Add login flow" in cmd
    assert "Users need to log in." in cmd
    assert "PLAN ONLY" in cmd


def test_positional_linear_degrades_to_branch_without_api_key(spawn_main, monkeypatch):
    """`resolve_issue` returns None → fall through to plain branch mode with
    the lowercased id as the branch name. Repo's `branch_prefix` still applies."""
    import scripts.spawn as spawn

    monkeypatch.setattr(spawn, "resolve_issue", lambda _id: None)

    code, out, _err = spawn_main(["PE-1234", "--repo", "testrepo"])
    assert code == 0
    # branch_prefix `khivi/` is applied by resolve_worktree's no-slash branch path.
    assert "on khivi/pe-1234" in out


def test_positional_linear_lowercase_normalised(spawn_main, monkeypatch):
    """`pe-1234` and `PE-1234` go through the same resolve path."""
    import scripts.spawn as spawn

    captured: dict = {}

    def _capture(identifier):
        captured["id"] = identifier
        return None

    monkeypatch.setattr(spawn, "resolve_issue", _capture)
    spawn_main(["pe-1234", "--repo", "testrepo"])
    assert captured["id"] == "PE-1234"


def test_positional_linear_no_title_falls_back_to_bare_id(spawn_main, monkeypatch):
    import scripts.spawn as spawn
    from scripts.lib.linear import ResolvedIssue

    issue = ResolvedIssue(
        identifier="PE-7", title="", description="", url="", branch_name=""
    )
    monkeypatch.setattr(spawn, "resolve_issue", lambda _id: issue)

    code, out, _err = spawn_main(["PE-7", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/pe-7" in out


def test_positional_slack_resolved_creates_text_branch(spawn_main, monkeypatch):
    import scripts.spawn as spawn
    from scripts.lib.slack import ResolvedThread

    thread = ResolvedThread(
        channel="C0123ABC",
        ts="1700000000.123456",
        text="Investigate login flake",
        permalink="https://acme.slack.com/archives/C0123ABC/p1700000000123456",
        reply_count=3,
    )
    monkeypatch.setattr(spawn, "resolve_thread", lambda _url: thread)

    url = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
    code, out, _err = spawn_main([url, "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/slack-investigate-login-flake" in out
    call = spawn_main.cmux_calls[0]
    cmd = _cmux_kwarg(call, "command")
    assert "Investigate login flake" in cmd
    assert "C0123ABC" in cmd
    assert "PLAN ONLY" in cmd


def test_positional_slack_degrades_to_url_derived_branch(spawn_main, monkeypatch):
    """`resolve_thread` None → fall back to a deterministic branch from URL."""
    import scripts.spawn as spawn

    monkeypatch.setattr(spawn, "resolve_thread", lambda _url: None)

    url = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
    code, out, _err = spawn_main([url, "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/slack-c0123abc-1700000000-123456" in out


def test_positional_slack_empty_text_falls_back_to_channel_ts(spawn_main, monkeypatch):
    import scripts.spawn as spawn
    from scripts.lib.slack import ResolvedThread

    thread = ResolvedThread(
        channel="C0123ABC",
        ts="1700000000.123456",
        text="",  # file-only post
        permalink="https://acme.slack.com/archives/C0123ABC/p1700000000123456",
        reply_count=0,
    )
    monkeypatch.setattr(spawn, "resolve_thread", lambda _url: thread)

    url = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
    code, out, _err = spawn_main([url, "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/slack-c0123abc-1700000000-123456" in out


def test_explicit_claude_prompt_overrides_linear_seeded_prompt(spawn_main, monkeypatch):
    """`--claude-prompt` wins over the auto-seeded Linear prompt."""
    import scripts.spawn as spawn
    from scripts.lib.linear import ResolvedIssue

    issue = ResolvedIssue(
        identifier="PE-1", title="t", description="d", url="", branch_name=""
    )
    monkeypatch.setattr(spawn, "resolve_issue", lambda _id: issue)

    spawn_main(
        [
            "PE-1",
            "--repo",
            "testrepo",
            "--claude-prompt",
            "OVERRIDDEN",
        ]
    )
    call = spawn_main.cmux_calls[0]
    cmd = _cmux_kwarg(call, "command")
    assert "OVERRIDDEN" in cmd
    assert "PLAN ONLY" not in cmd


# ── --skill semantics ──────────────────────────────────────────────────────


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


# ── --repo validation ──────────────────────────────────────────────────────


def test_unknown_repo_exits_one(spawn_main):
    code, _out, err = spawn_main(["--name", "foo", "--repo", "nonexistent"])
    assert code == 1
    assert "nonexistent" in err
    assert "no configured repo" in err
