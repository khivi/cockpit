"""Tests for cockpit/spawn.py.

Three layers:
  - detect_source: pure-function classification of positional input.
  - resolve_worktree: branch/worktree resolution against a real tmp repo.
  - main: argument-validation + dispatch end-to-end (cmux stubbed).
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from cockpit.spawn import detect_source


def _set_config_key(cockpit_repo, key: str, value) -> None:
    """Mutate the on-disk config.json the `cockpit_repo` fixture wrote.

    `load_config()` re-reads the file on every call, so an in-place edit is
    enough — no module reload required. Used by Linear-flow tests that need
    `use_linear: true` (the fixture defaults `use_linear` to absent → False).
    """
    cfg_path = cockpit_repo.cockpit_home / "config.json"
    data = json.loads(cfg_path.read_text())
    data[key] = value
    cfg_path.write_text(json.dumps(data))


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


def test_actions_run_url_returns_actions_mode_and_nwo():
    mode, value, nwo = detect_source("https://github.com/owner/repo/actions/runs/12345")
    assert mode == "actions"
    assert value == "12345"
    assert nwo == "owner/repo"


def test_actions_job_url_packs_run_and_job():
    mode, value, nwo = detect_source(
        "https://github.com/owner/repo/actions/runs/12345/job/67890"
    )
    assert mode == "actions"
    assert value == "12345:67890"
    assert nwo == "owner/repo"


def test_actions_attempts_url_still_parses():
    mode, value, nwo = detect_source(
        "https://github.com/owner/repo/actions/runs/12345/attempts/2"
    )
    assert mode == "actions"
    assert value == "12345"
    assert nwo == "owner/repo"


def test_actions_attempts_with_job_url_parses():
    mode, value, nwo = detect_source(
        "https://github.com/owner/repo/actions/runs/12345/attempts/2/job/67890"
    )
    assert mode == "actions"
    assert value == "12345:67890"
    assert nwo == "owner/repo"


def test_slack_archives_url_returns_slack_mode_verbatim():
    url = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
    mode, value, nwo = detect_source(url)
    assert mode == "slack"
    assert value == url  # passed through untouched — Claude reads it via the MCP
    assert nwo is None  # no GitHub owner/repo to route to


def test_slack_archives_url_with_query_still_slack():
    url = (
        "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
        "?thread_ts=1700000000.123456&cid=C0123ABC"
    )
    mode, value, _nwo = detect_source(url)
    assert mode == "slack"
    assert value == url


def test_slack_client_deep_link_returns_slack_mode():
    url = "https://app.slack.com/client/T01234567/C0123ABC"
    mode, value, _nwo = detect_source(url)
    assert mode == "slack"
    assert value == url


def test_non_slack_url_is_branch():
    # A bare branch name that merely contains the word slack is NOT a URL.
    mode, _value, _nwo = detect_source("khivi/slack-feature")
    assert mode == "branch"


# ────────────────────────────────────────────────────────────────────────────
# resolve_worktree (real tmp repo via cockpit_repo)
# ────────────────────────────────────────────────────────────────────────────


def test_from_name_creates_prefixed_branch_when_free(cockpit_repo):
    from cockpit.spawn import resolve_worktree

    wt, branch, attached = resolve_worktree("cship", None, "testrepo", from_name=True)
    assert branch == "khivi/cship"
    assert attached is False
    assert wt.exists()
    assert wt == cockpit_repo.repo.parent / "cship"


def test_from_name_bumps_branch_when_remote_collides(cockpit_repo, push_branch):
    from cockpit.spawn import resolve_worktree

    push_branch("khivi/cship")

    wt, branch, attached = resolve_worktree("cship", None, "testrepo", from_name=True)
    assert branch == "khivi/cship-2"
    assert attached is False
    assert wt.exists()


def test_from_name_bumps_branch_when_local_collides(cockpit_repo):
    from cockpit.spawn import resolve_worktree

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
    from cockpit.spawn import resolve_worktree

    push_branch("khivi/foo/cship")

    wt, branch, attached = resolve_worktree("cship", None, "testrepo", from_name=True)
    assert branch == "khivi/cship"
    assert attached is False


def test_from_name_creates_branch_from_origin_main(cockpit_repo):
    """New branch's tip must be origin/main, not some stale local ref."""
    from cockpit.spawn import resolve_worktree

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
    from cockpit.spawn import resolve_worktree

    with pytest.raises(ValueError, match="no configured repo"):
        resolve_worktree("cship", None, "nonexistent", from_name=True)


def test_non_from_name_attaches_to_existing_remote_branch(cockpit_repo, push_branch):
    """Regression on the original code path: passing an existing branch
    explicitly (no from_name) should still attach to it, not bump."""
    from cockpit.spawn import resolve_worktree

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

    Captures call args on `spawn_main.cmux_calls`: direct `cmux(...)` calls
    (send/send-key on attach) and `spawn_workspace(...)` calls (synthesized
    into cmux-style new-workspace tuples so `_cmux_kwarg` works unchanged).
    """
    import cockpit.spawn as spawn

    cmux_calls: list[tuple] = []

    def fake_cmux(*args, **kwargs):
        cmux_calls.append(args)
        return None

    def fake_spawn_workspace(name, cwd, command):
        cmux_calls.append(
            ("new-workspace", "--name", name, "--cwd", str(cwd), "--command", command)
        )
        return None

    monkeypatch.setattr(spawn, "cmux", fake_cmux)
    monkeypatch.setattr(spawn, "spawn_workspace", fake_spawn_workspace)
    monkeypatch.setattr(spawn, "workspace_names", lambda: {})
    monkeypatch.setattr(spawn, "workspace_cwds", lambda: {})
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
            return str(call_args[i + 1])
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


# ── _pr_author ─────────────────────────────────────────────────────────────


def test_pr_author_extracts_login():
    import cockpit.spawn as spawn

    assert spawn._pr_author({"author": {"login": "coworker"}}) == "coworker"


def test_pr_author_falls_back_when_author_null_or_absent():
    """`gh` can emit `author: null` (deleted account) or omit it entirely."""
    import cockpit.spawn as spawn

    assert spawn._pr_author({"author": None}) == "unknown"
    assert spawn._pr_author({}) == "unknown"


# ── --review (per-repo review_prs) ─────────────────────────────────────────


def test_review_prompt_leads_with_slash_review():
    import cockpit.spawn as spawn

    p = spawn._review_prompt(
        "coworker/x",
        {
            "number": 7,
            "title": "fix the thing",
            "author": {"login": "coworker"},
            "url": "https://github.com/o/n/pull/7",
        },
    )
    assert p.startswith("/review")
    assert "#7" in p and "coworker" in p and "fix the thing" in p
    assert "Do not post" in p


def test_review_prompt_without_pr_info_mentions_branch():
    import cockpit.spawn as spawn

    p = spawn._review_prompt("coworker/x", None)
    assert p.startswith("/review")
    assert "coworker/x" in p


def test_review_branch_mode_seeds_review_command(spawn_main, push_branch, monkeypatch):
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    push_branch("khivi/reviewme")
    code, _out, _err = spawn_main(
        ["--branch", "khivi/reviewme", "--repo", "testrepo", "--review"]
    )
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "/review" in cmd
    assert "PLAN ONLY" not in cmd


def test_review_with_skill_is_error(spawn_main):
    code, _out, err = spawn_main(
        ["--skill", "review", "--review", "--repo", "testrepo"]
    )
    assert code == 1
    assert "--review" in err


def test_review_with_bare_cwd_is_error(spawn_main, tmp_path):
    target = tmp_path / "d"
    target.mkdir()
    code, _out, err = spawn_main(["--cwd", str(target), "--review"])
    assert code == 1
    assert "--review" in err


# ── actions URL dispatch ───────────────────────────────────────────────────
#
# A GitHub Actions run/job URL spawns a worktree on the run's headBranch
# (looked up via gh) and seeds a plan-only prompt directing Claude to
# fetch `--log-failed` first. fetch_run_info is mocked because we don't
# want test runs to hit the real gh CLI.


def _actions_run_info(
    branch: str = "khivi/positional-branch",
    *,
    workflow: str = "CI",
    display_title: str = "fix login retry loop",
) -> dict:
    return {
        "databaseId": 12345,
        "headBranch": branch,
        "headSha": "deadbeef",
        "workflowName": workflow,
        "displayTitle": display_title,
        "conclusion": "failure",
        "status": "completed",
        "event": "pull_request",
        "url": "https://github.com/owner/repo/actions/runs/12345",
        "jobs": [
            {
                "databaseId": 67890,
                "name": "unit-tests",
                "conclusion": "failure",
                "status": "completed",
                "url": "https://github.com/owner/repo/actions/runs/12345/job/67890",
            }
        ],
    }


def test_actions_url_creates_fresh_investigation_branch(spawn_main, monkeypatch):
    """An Actions URL must spawn a fresh `khivi/ci-...` worktree, never attach
    to the run's headBranch — even when the head was a feature branch."""
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "fetch_run_info", lambda *a, **kw: _actions_run_info())
    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)

    code, out, _err = spawn_main(
        [
            "https://github.com/owner/repo/actions/runs/12345",
            "--repo",
            "testrepo",
        ]
    )
    assert code == 0
    assert "on khivi/ci-" in out
    assert "khivi/positional-branch" not in out


def test_actions_url_on_master_does_not_attach_to_main_worktree(
    spawn_main, monkeypatch
):
    """The bug this branch fixes: a CI failure on `main`/`master` (after merge)
    must NOT attach to the main repo checkout. Spawn a fresh ci-... worktree."""
    import cockpit.spawn as spawn

    monkeypatch.setattr(
        spawn, "fetch_run_info", lambda *a, **kw: _actions_run_info(branch="main")
    )
    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)

    code, out, _err = spawn_main(
        [
            "https://github.com/owner/repo/actions/runs/12345",
            "--repo",
            "testrepo",
        ]
    )
    assert code == 0
    assert "spawned" in out  # not "attached"
    assert "on main" not in out
    assert "on khivi/ci-" in out


def test_actions_url_seeds_log_failed_prompt(spawn_main, monkeypatch):
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "fetch_run_info", lambda *a, **kw: _actions_run_info())
    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)

    spawn_main(
        [
            "https://github.com/owner/repo/actions/runs/12345",
            "--repo",
            "testrepo",
        ]
    )
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "gh run view 12345 --log-failed" in cmd
    assert "--job" not in cmd  # run-scoped, not job-scoped
    assert "PLAN ONLY" in cmd
    assert "CI" in cmd  # workflowName
    assert "Conclusion" in cmd
    assert "khivi/positional-branch" in cmd  # head branch surfaced in prompt


def test_actions_run_short_name_uses_workflow_and_title(spawn_main, monkeypatch):
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "fetch_run_info", lambda *a, **kw: _actions_run_info())
    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)

    spawn_main(
        [
            "https://github.com/owner/repo/actions/runs/12345",
            "--repo",
            "testrepo",
        ]
    )
    call = spawn_main.cmux_calls[0]
    name = _cmux_kwarg(call, "name")
    # `slugify("ci-CI-fix login retry loop")` → "ci-ci-fix-login-retry-loop" (capped at 30)
    assert name.startswith("ci-")
    assert "fix-login" in name


def test_actions_job_url_short_name_uses_job_name(spawn_main, monkeypatch):
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "fetch_run_info", lambda *a, **kw: _actions_run_info())
    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)

    spawn_main(
        [
            "https://github.com/owner/repo/actions/runs/12345/job/67890",
            "--repo",
            "testrepo",
        ]
    )
    call = spawn_main.cmux_calls[0]
    name = _cmux_kwarg(call, "name")
    assert name == "ci-unit-tests"
    cmd = _cmux_kwarg(call, "command")
    assert "gh run view 12345 --log-failed --job 67890" in cmd
    assert "unit-tests" in cmd


def test_actions_url_with_pr_includes_related_pr_in_prompt(spawn_main, monkeypatch):
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "fetch_run_info", lambda *a, **kw: _actions_run_info())
    monkeypatch.setattr(
        spawn,
        "pr_for_branch",
        lambda *_a, **_kw: {
            "number": 42,
            "title": "fix the bug",
            "author": {"login": "khivi"},
            "url": "https://github.com/owner/repo/pull/42",
        },
    )

    spawn_main(
        [
            "https://github.com/owner/repo/actions/runs/12345",
            "--repo",
            "testrepo",
        ]
    )
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Related PR" in cmd
    assert "#42" in cmd
    assert "fix the bug" in cmd


def test_actions_url_missing_head_branch_errors(spawn_main, monkeypatch):
    """gh returns the run JSON but headBranch is empty (detached/tag run) →
    we can't resolve a worktree, surface a clean error."""
    import cockpit.spawn as spawn

    monkeypatch.setattr(
        spawn,
        "fetch_run_info",
        lambda *a, **kw: {"databaseId": 12345, "headBranch": ""},
    )

    code, _out, err = spawn_main(
        [
            "https://github.com/owner/repo/actions/runs/12345",
            "--repo",
            "testrepo",
        ]
    )
    assert code == 1
    assert "headBranch" in err


def test_actions_url_gh_failure_propagates(spawn_main, monkeypatch):
    import cockpit.spawn as spawn

    def boom(*a, **kw):
        raise RuntimeError("gh run view failed: not found")

    monkeypatch.setattr(spawn, "fetch_run_info", boom)

    code, _out, err = spawn_main(
        [
            "https://github.com/owner/repo/actions/runs/12345",
            "--repo",
            "testrepo",
        ]
    )
    assert code == 1
    assert "gh run view failed" in err


# ── linear dispatch ────────────────────────────────────────────────────────
#
# `/cockpit:new PE-1234` creates a worktree on `khivi/<id-lower>` and, when
# `use_linear: true` AND the Linear MCP is detected, seeds a first-turn
# prompt instructing Claude to fetch the ticket via the Linear MCP and
# rename the branch + workspace. Cockpit does NOT call the Linear API
# itself — no network surface to mock, only prompt + branch shape + gating.


def test_positional_linear_creates_lowercased_branch(spawn_main):
    code, out, _err = spawn_main(["PE-1234", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/pe-1234" in out
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "name") == "pe-1234"


def test_positional_linear_lowercase_input_normalised(spawn_main):
    """`pe-1234` and `PE-1234` produce the same branch."""
    code, out, _err = spawn_main(["pe-1234", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/pe-1234" in out


def test_positional_linear_prompt_instructs_mcp_fetch(
    spawn_main, cockpit_repo, monkeypatch
):
    _set_config_key(cockpit_repo, "use_linear", True)
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "linear_mcp_available", lambda: True)
    spawn_main(["PE-1234", "--repo", "testrepo"])
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "PE-1234" in cmd
    assert "Linear MCP" in cmd
    assert "STOP" in cmd  # error path when MCP not connected
    assert "PLAN ONLY" in cmd
    # Connection-lag retry is an immediate re-attempt loop, not a shell `sleep`
    # backoff — `sleep` is blocked in some debug harnesses (exit 144) and never
    # helped, so the prompt must not instruct any shell wait.
    assert "retry the SAME MCP tool call up to three times" in cmd
    assert "sleep" not in cmd or "do not insert shell `sleep`" in cmd
    assert "/mcp" in cmd  # STOP message points the user at the reconnect fix


def test_positional_linear_prompt_instructs_branch_rename(
    spawn_main, cockpit_repo, monkeypatch
):
    """Step 2 of the Linear prompt asks Claude to rename the branch to include
    the ticket title slug — that's how the title gets into the branch name
    without cockpit ever calling the Linear API. The prompt reads the current
    branch via git so it's robust against `-2`/`-3` collision bumping."""
    _set_config_key(cockpit_repo, "use_linear", True)
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "linear_mcp_available", lambda: True)
    spawn_main(["PE-1234", "--repo", "testrepo"])
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "git branch --show-current" in cmd
    assert 'git branch -m "$CUR" "$CUR-<slug>"' in cmd


def test_positional_linear_prompt_instructs_workspace_rename(
    spawn_main, cockpit_repo, monkeypatch
):
    """Step 3: drop the `pe-1234`-style placeholder from the cmux workspace name
    by renaming it to the same `<slug>` derived from the Linear title.
    `CMUX_WORKSPACE_ID` is the default target; `cmux identify` is the fallback."""
    _set_config_key(cockpit_repo, "use_linear", True)
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "linear_mcp_available", lambda: True)
    spawn_main(["PE-1234", "--repo", "testrepo"])
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert 'cmux workspace-action --action rename --title "<slug>"' in cmd
    assert "CMUX_WORKSPACE_ID" in cmd
    assert "cmux identify" in cmd


# ── use_linear gating ─────────────────────────────────────────────────────
#
# With `use_linear: false` (the default), Linear-id input still classifies
# as linear-mode (branch lower-cased, statusline pill keeps working) but
# the MCP-instructing prompt is suppressed: the workspace starts with the
# generic plan-only prompt. The Linear key still counts as context, so
# plan-only IS seeded (unlike a bare `--branch pe-1234`, which seeds none);
# only the MCP fetch + branch/workspace rename are skipped.


def test_linear_default_off_skips_mcp_instructing_prompt(spawn_main, monkeypatch):
    """Default (use_linear absent) → no 'Linear MCP', no 'STOP', no rename
    instructions — only the generic plan-only prompt."""
    import cockpit.spawn as spawn

    called: list[bool] = []

    def _available():
        called.append(True)
        return True

    monkeypatch.setattr(spawn, "linear_mcp_available", _available)
    code, out, _err = spawn_main(["PE-1234", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/pe-1234" in out
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Linear MCP" not in cmd
    assert "STOP" not in cmd
    assert 'git branch -m "$CUR" "$CUR-<slug>"' not in cmd
    assert "cmux workspace-action" not in cmd
    assert "PLAN ONLY" in cmd  # generic plan prompt still present
    # MCP probe must NOT run when the flag is off — it's a wasted subprocess.
    assert called == []


def test_linear_on_but_mcp_missing_falls_back_with_warning(
    spawn_main, cockpit_repo, monkeypatch
):
    """use_linear: true + `claude mcp list` reports no Linear entry → warn
    on stderr and seed the generic plan prompt, not the rename prompt."""
    _set_config_key(cockpit_repo, "use_linear", True)
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "linear_mcp_available", lambda: False)
    code, _out, err = spawn_main(["PE-1234", "--repo", "testrepo"])
    assert code == 0
    assert "Linear MCP not detected" in err
    assert "PE-1234" in err
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Linear MCP" not in cmd
    assert "STOP" not in cmd
    assert "PLAN ONLY" in cmd  # generic plan prompt


def test_linear_on_with_inconclusive_probe_seeds_smart_prompt(
    spawn_main, cockpit_repo, monkeypatch
):
    """use_linear: true + probe returns None (claude missing / timeout) →
    proceed with the smart flow; Claude itself STOPs on the first turn if
    the MCP is truly missing."""
    _set_config_key(cockpit_repo, "use_linear", True)
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "linear_mcp_available", lambda: None)
    code, _out, err = spawn_main(["PE-1234", "--repo", "testrepo"])
    assert code == 0
    assert "not detected" not in err  # no fallback warning
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Linear MCP" in cmd
    assert "STOP" in cmd


# ── slack dispatch ─────────────────────────────────────────────────────────
#
# A Slack permalink has no human name, so spawn synthesizes a deterministic
# codename branch from the thread's stable identity and seeds a prompt that
# delegates the thread read to the in-session Slack MCP. Cockpit never calls
# the Slack API — no network surface to mock, only branch shape + prompt +
# gating. There is deliberately no `claude mcp list` probe (unreliable for
# managed connectors), so unlike Linear these tests monkeypatch nothing.

_SLACK_URL = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"


def test_positional_slack_creates_codename_branch(spawn_main):
    from cockpit.lib.codename import codename
    from cockpit.lib.slack import slack_seed

    expected = codename(slack_seed(_SLACK_URL))
    code, out, _err = spawn_main([_SLACK_URL, "--repo", "testrepo"])
    assert code == 0
    assert f"on khivi/{expected}" in out
    assert _cmux_kwarg(spawn_main.cmux_calls[0], "name") == expected


def test_slack_branch_is_deterministic_across_query_params(spawn_main):
    """The same thread linked with and without `?thread_ts=…&cid=…` resolves
    to the same codename branch — the seed is the thread identity, not the URL."""
    from cockpit.lib.codename import codename
    from cockpit.lib.slack import slack_seed

    plain = codename(slack_seed(_SLACK_URL))
    with_query = codename(slack_seed(_SLACK_URL + "?thread_ts=1700000000.123456"))
    assert plain == with_query


def test_slack_default_off_seeds_url_context_no_rename(spawn_main):
    """Default (use_slack absent) → the thread URL is seeded as context, but no
    MCP-fetch or branch/workspace rename instructions."""
    code, _out, _err = spawn_main([_SLACK_URL, "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert _SLACK_URL in cmd  # URL always reaches the first turn
    assert "Slack thread" in cmd
    assert 'git branch -m "$CUR" "$CUR-<slug>"' not in cmd
    assert "cmux workspace-action" not in cmd
    assert "PLAN ONLY" in cmd


def test_slack_on_seeds_fetch_and_rename(spawn_main, cockpit_repo):
    """use_slack: true → full prompt: read via the Slack MCP, append a topic
    slug to the codename branch, rename the workspace. Mirrors the Linear flow,
    including the immediate-retry (no shell `sleep`) and /mcp STOP guidance."""
    _set_config_key(cockpit_repo, "use_slack", True)
    code, _out, _err = spawn_main([_SLACK_URL, "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert _SLACK_URL in cmd
    assert "slack_read_thread" in cmd
    assert 'git branch -m "$CUR" "$CUR-<slug>"' in cmd
    assert "cmux workspace-action --action rename" in cmd
    assert "STOP" in cmd
    assert "retry the SAME MCP tool call up to three times" in cmd
    assert "sleep" not in cmd or "do not insert shell `sleep`" in cmd
    assert "/mcp" in cmd
    assert "PLAN ONLY" in cmd


def test_trailing_addendum_is_appended_to_seeded_prompt(
    spawn_main, cockpit_repo, monkeypatch
):
    """Trailing `-- <text>` is appended to the auto-seeded Linear/skill/plan
    prompt rather than replacing it — preserves the plan-only safety guard."""
    _set_config_key(cockpit_repo, "use_linear", True)
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "linear_mcp_available", lambda: True)
    spawn_main(["PE-1", "--repo", "testrepo", "--", "EXTRA", "INSTRUCTIONS"])
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "EXTRA INSTRUCTIONS" in cmd
    assert "Linear MCP" in cmd, "seeded MCP prompt must survive when -- is used"


def test_trailing_addendum_alone_becomes_prompt(spawn_main, cockpit_repo, monkeypatch):
    """`-- <text>` on an otherwise-blank spawn is context, so it flips the
    spawn into plan-only and the text appends to the plan prompt."""
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    spawn_main(["fresh-feat", "--repo", "testrepo", "--", "do thing X"])
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "do thing X" in cmd
    assert "PLAN ONLY" in cmd  # addendum is context → plan prompt fires


def test_blank_spawn_seeds_no_plan_prompt(spawn_main, monkeypatch):
    """A blank `<name> --repo <repo>` spawn (no PR / Linear / Actions, no
    --context, no `-- text`) is ready to work on — no plan-only guidance is
    seeded; the workspace just starts `claude`."""
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    code, out, _err = spawn_main(["fresh-feat", "--repo", "testrepo"])
    assert code == 0
    assert "spawned" in out
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "PLAN ONLY" not in cmd
    assert "fresh task" not in cmd
    assert cmd == "claude"  # no prompt_prefix configured → bare claude


def test_blank_spawn_still_applies_prompt_prefix(spawn_main, cockpit_repo, monkeypatch):
    """Dropping the plan prompt for a blank spawn must NOT drop a configured
    `prompt_prefix` (e.g. a session-setup slash command) — it rides via
    claude_command()."""
    import cockpit.spawn as spawn

    _set_config_key(cockpit_repo, "prompt_prefix", "/session-coordination")
    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    spawn_main(["fresh-feat", "--repo", "testrepo"])
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "/session-coordination" in cmd
    assert "PLAN ONLY" not in cmd  # prefix only, no plan guidance


def test_pr_spawn_still_seeds_plan_prompt(spawn_main, monkeypatch):
    """A spawn that auto-detects an open PR is a sourced spawn → plan-only
    still fires (regression guard for the blank-spawn carve-out)."""
    import cockpit.spawn as spawn

    monkeypatch.setattr(
        spawn,
        "pr_for_branch",
        lambda *_a, **_kw: {
            "number": 99,
            "title": "fix the thing",
            "author": {"login": "someone"},
            "url": "https://github.com/owner/repo/pull/99",
        },
    )
    spawn_main(["has-a-pr", "--repo", "testrepo"])
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "PLAN ONLY" in cmd
    assert "#99" in cmd


# ── --context-text injection ──────────────────────────────────────────────


def test_context_text_injected_into_seeded_prompt(spawn_main, monkeypatch):
    """`--context-text` is folded into the seeded prompt under a labeled
    heading, without clobbering the plan-only guard."""
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    spawn_main(["ctx-feat", "--repo", "testrepo", "--context-text", "goal: fix X"])
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Caller session context" in cmd
    assert "goal: fix X" in cmd
    assert "PLAN ONLY" in cmd  # seeded prompt preserved


# ── attach-path prompt delivery (cmux send) ───────────────────────────────


def _send_calls(calls):
    return [c for c in calls if c and c[0] == "send"]


def test_attach_delivers_prompt_via_cmux_send(spawn_main, monkeypatch):
    """Re-spawning onto an EXISTING workspace must deliver the seeded prompt
    into the running Claude via `cmux send` + Enter — not silently drop it,
    and not create a second workspace."""
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        spawn, "workspace_names", lambda: {"workspace:7": "attach-only"}
    )
    # `-- text` makes this a sourced spawn → a plan prompt exists to deliver.
    code, _out, err = spawn_main(["attach-only", "--repo", "testrepo", "--", "do X"])
    assert code == 0
    sends = _send_calls(spawn_main.cmux_calls)
    assert sends, "expected a cmux send on attach"
    assert sends[0][1] == "--workspace" and sends[0][2] == "workspace:7"
    assert "PLAN ONLY" in sends[0][3]
    assert "do X" in sends[0][3]
    assert any(
        c[0] == "send-key" and c[1] == "--workspace" and c[-1] == "enter"
        for c in spawn_main.cmux_calls
    ), "prompt must be submitted with Enter"
    assert not any("new-workspace" in c for c in spawn_main.cmux_calls)
    assert "delivered prompt to existing workspace attach-only" in err


def test_blank_attach_delivers_nothing(spawn_main, monkeypatch):
    """Re-spawning a blank `<name> --repo` onto an existing workspace has no
    seeded prompt to deliver — the running session is left untouched (no
    cmux send), and spawn just reports the attach."""
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        spawn, "workspace_names", lambda: {"workspace:7": "attach-only"}
    )
    code, _out, err = spawn_main(["attach-only", "--repo", "testrepo"])
    assert code == 0
    assert not _send_calls(spawn_main.cmux_calls), "blank attach must not send"
    # existing workspace → no new workspace created, and nothing delivered.
    assert not any("new-workspace" in c for c in spawn_main.cmux_calls)
    assert "delivered prompt" not in err


def test_attach_delivers_addendum_and_context(spawn_main, monkeypatch):
    """On attach, the `-- <text>` addendum and `--context-text` both ride into
    the running session via cmux send, same as a fresh spawn's --command."""
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    monkeypatch.setattr(spawn, "workspace_names", lambda: {"workspace:9": "ctx-attach"})
    spawn_main(
        [
            "ctx-attach",
            "--repo",
            "testrepo",
            "--context-text",
            "prior: Y",
            "--",
            "next Z",
        ]
    )
    sends = _send_calls(spawn_main.cmux_calls)
    assert sends
    sent = sends[0][3]
    assert "next Z" in sent
    assert "Caller session context" in sent and "prior: Y" in sent


# ── linear team-key routing ───────────────────────────────────────────────
#
# When `use_linear: true` and no `--repo`, a positional Linear key is
# routed to the repo whose `linear_keys` list contains the prefix. Single
# match wins; multi-match warns + falls back; no match falls back; the
# explicit `--repo` flag always wins.


def _add_linear_keys(cockpit_repo, keys: list[str], repo_name: str = "testrepo"):
    cfg_path = cockpit_repo.cockpit_home / "config.json"
    data = json.loads(cfg_path.read_text())
    for r in data["repos"]:
        if r["name"] == repo_name:
            r["linear_keys"] = keys
    cfg_path.write_text(json.dumps(data))


def test_linear_key_routes_to_matching_repo_without_repo_flag(
    spawn_main, cockpit_repo, monkeypatch
):
    _set_config_key(cockpit_repo, "use_linear", True)
    _add_linear_keys(cockpit_repo, ["PE"])
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "linear_mcp_available", lambda: None)
    code, out, _err = spawn_main(["PE-1234"])
    assert code == 0
    assert "on khivi/pe-1234" in out


def test_linear_key_routing_case_insensitive(spawn_main, cockpit_repo, monkeypatch):
    _set_config_key(cockpit_repo, "use_linear", True)
    _add_linear_keys(cockpit_repo, ["pe"])
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "linear_mcp_available", lambda: None)
    code, out, _err = spawn_main(["PE-1234"])
    assert code == 0
    assert "on khivi/pe-1234" in out


def test_linear_key_routing_explicit_repo_wins(spawn_main, cockpit_repo, monkeypatch):
    """With `--repo testrepo` set, the team-key lookup is skipped — even
    if the lookup would otherwise route elsewhere or find nothing."""
    _set_config_key(cockpit_repo, "use_linear", True)
    # No linear_keys configured anywhere; --repo still drives the spawn.
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "linear_mcp_available", lambda: None)
    code, out, _err = spawn_main(["PE-1234", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/pe-1234" in out


def test_linear_key_routing_disabled_when_use_linear_false(
    spawn_main, cockpit_repo, monkeypatch
):
    """With `use_linear: false`, team-key routing is a no-op: the spawn
    falls back to cwd discovery, which fails under tests (no managed
    repo at the test process cwd)."""
    _add_linear_keys(cockpit_repo, ["PE"])  # would match if routing ran
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "discover_repo", lambda: None)
    code, _out, err = spawn_main(["PE-1234"])
    assert code != 0
    assert "cannot determine repo" in err


def test_linear_key_routing_multi_match_warns_and_falls_back(
    spawn_main, cockpit_repo, monkeypatch, tmp_path
):
    """Two repos declaring `PE` → stderr note, fall back to cwd discovery
    (which fails under tests)."""
    _set_config_key(cockpit_repo, "use_linear", True)
    cfg_path = cockpit_repo.cockpit_home / "config.json"
    data = json.loads(cfg_path.read_text())
    data["repos"][0]["linear_keys"] = ["PE"]
    data["repos"].append(
        {
            "name": "second",
            "path": str(tmp_path / "second"),
            "branch_prefix": "khivi/",
            "default_base": "main",
            "linear_keys": ["PE"],
        }
    )
    cfg_path.write_text(json.dumps(data))

    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "discover_repo", lambda: None)
    monkeypatch.setattr(spawn, "linear_mcp_available", lambda: None)
    code, _out, err = spawn_main(["PE-1234"])
    assert code != 0
    assert "matches multiple repos" in err
    assert "testrepo" in err
    assert "second" in err


def test_linear_key_routing_no_match_falls_back_to_cwd(
    spawn_main, cockpit_repo, monkeypatch
):
    """No repo declares the key → no auto-routing, fall back to cwd
    discovery (which fails under tests)."""
    _set_config_key(cockpit_repo, "use_linear", True)
    _add_linear_keys(cockpit_repo, ["ENG"])  # different prefix
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "discover_repo", lambda: None)
    code, _out, err = spawn_main(["PE-1234"])
    assert code != 0
    assert "cannot determine repo" in err
    assert "matches multiple repos" not in err  # silent on no-match


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


# ── --auto flag (keep-marker) ──────────────────────────────────────────────

_FAKE_PR = {
    "number": 9,
    "title": "fix the thing",
    "author": {"login": "khivi"},
    "url": "https://github.com/owner/repo/pull/9",
}


def test_context_separator_parses_into_addendum(monkeypatch):
    """A `-- <text>` separator (the shape `/cockpit:new <branch> -- text`
    produces) is joined into `claude_addendum`, not parsed as flags."""
    import cockpit.spawn as spawn

    monkeypatch.setattr(sys, "argv", ["spawn.py", "feat", "--", "do", "thing", "X"])
    args = spawn.parse_args()
    assert args.claude_addendum == "do thing X"


# ── workspace path-fallback deduplication ────────────────────────────────────
#
# When the daemon auto-spawned a workspace for a worktree under a different
# slug, a name-only lookup misses it. The path-fallback in main() consults
# workspace_cwds() to catch the match and prevent a duplicate workspace.


def test_path_fallback_attaches_when_name_mismatches(
    spawn_main, monkeypatch, cockpit_repo, tmp_path
):
    """workspace_cwds() matches the worktree path even when workspace name differs.

    Simulates: daemon spawned `wt:1` pointing at the worktree directory before
    the user ran /cockpit:new with slug `my-slug`. Name lookup misses; path
    lookup catches it; spawn attaches (no new-workspace) and delivers prompt.
    """
    import cockpit.spawn as spawn

    wt_path = cockpit_repo.repo.parent / "path-fallback"
    wt_path.mkdir(exist_ok=True)

    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        spawn,
        "resolve_worktree",
        lambda *a, **kw: (wt_path, "khivi/path-fallback", False),
    )
    # Name "my-slug" is not in ws_refs — name match misses.
    monkeypatch.setattr(spawn, "workspace_names", lambda: {"wt:1": "daemon-slug"})
    # Path match hits.
    monkeypatch.setattr(spawn, "workspace_cwds", lambda: {"wt:1": wt_path})

    code, _out, err = spawn_main(
        ["khivi/path-fallback", "--repo", "testrepo", "--", "do Y"]
    )
    assert code == 0
    # Must not create a second workspace.
    assert not any("new-workspace" in str(c) for c in spawn_main.cmux_calls)
    # Must deliver prompt into the existing workspace via send.
    sends = _send_calls(spawn_main.cmux_calls)
    assert sends, "expected cmux send on path-matched attach"
    assert sends[0][2] == "wt:1"
    assert "do Y" in sends[0][3]
    assert "delivered prompt to existing workspace daemon-slug" in err


def test_path_fallback_not_triggered_when_name_matches(
    spawn_main, monkeypatch, cockpit_repo, tmp_path
):
    """When name lookup already hits, workspace_cwds() is never consulted."""
    import cockpit.spawn as spawn

    wt_path = cockpit_repo.repo.parent / "named-match"
    wt_path.mkdir(exist_ok=True)

    cwds_called = []

    def fake_cwds():
        cwds_called.append(1)
        return {}

    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        spawn,
        "resolve_worktree",
        lambda *a, **kw: (wt_path, "khivi/named-match", False),
    )
    monkeypatch.setattr(spawn, "workspace_names", lambda: {"ws:5": "named-match"})
    monkeypatch.setattr(spawn, "workspace_cwds", fake_cwds)

    code, _out, _err = spawn_main(["khivi/named-match", "--repo", "testrepo"])
    assert code == 0
    assert (
        not cwds_called
    ), "workspace_cwds must not be called when name already matched"
    assert not any("new-workspace" in str(c) for c in spawn_main.cmux_calls)


def test_path_fallback_deduplicates_cwd_spawn(spawn_main, monkeypatch, tmp_path):
    """--cwd pointing at an existing workspace's directory must attach, not spawn."""
    import cockpit.spawn as spawn

    target = tmp_path / "cwd-dedup"
    target.mkdir()

    monkeypatch.setattr(spawn, "workspace_names", lambda: {"ws:cwd": "cwd-dedup"})
    monkeypatch.setattr(spawn, "workspace_cwds", lambda: {"ws:cwd": target})

    code, _out, _err = spawn_main(["--cwd", str(target)])
    assert code == 0
    # Must not create a second workspace — path matched.
    assert not any("new-workspace" in str(c) for c in spawn_main.cmux_calls)


def test_path_fallback_exception_is_swallowed(spawn_main, monkeypatch, cockpit_repo):
    """If workspace_cwds() raises, the exception is silently caught and spawn
    falls through to creating a new workspace — no crash."""
    import cockpit.spawn as spawn

    wt_path = cockpit_repo.repo.parent / "cwds-error"
    wt_path.mkdir(exist_ok=True)

    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        spawn,
        "resolve_worktree",
        lambda *a, **kw: (wt_path, "khivi/cwds-error", False),
    )
    monkeypatch.setattr(spawn, "workspace_names", lambda: {})
    monkeypatch.setattr(
        spawn,
        "workspace_cwds",
        lambda: (_ for _ in ()).throw(RuntimeError("cmux down")),
    )

    code, _out, _err = spawn_main(["khivi/cwds-error", "--repo", "testrepo"])
    assert code == 0
    # Falls through to creating a new workspace.
    assert any("new-workspace" in str(c) for c in spawn_main.cmux_calls)
