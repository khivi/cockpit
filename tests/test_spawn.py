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
from unittest.mock import patch

import pytest

from cockpit.spawn import detect_source


def _set_config_key(cockpit_repo, key: str, value) -> None:
    """Mutate the on-disk config.json the `cockpit_repo` fixture wrote.

    `load_config()` re-reads the file on every call, so an in-place edit is
    enough — no module reload required. Used by ticket-flow tests that need
    `tickets: linear` / `tickets: github` (the fixture defaults `tickets` to
    absent → "none").
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


def test_linear_issue_url_returns_linear_mode_with_bare_id():
    """The clipboard shape. Without this it fell through to `branch` and git
    rejected the whole URL as a branch name.
    """
    mode, value, nwo = detect_source(
        "https://linear.app/acme/issue/TOOLS-1300/add-widget-support"
    )
    assert mode == "linear"
    assert value == "TOOLS-1300"  # the id, not the URL — the branch derives from it
    assert nwo is None


def test_linear_issue_url_without_slug_or_with_query_still_linear():
    for url in (
        "https://linear.app/acme/issue/PE-1234",
        "https://linear.app/acme/issue/pe-1234/foo?tab=activity",
    ):
        mode, value, _nwo = detect_source(url)
        assert mode == "linear"
        assert value == "PE-1234"


def test_linear_non_issue_url_is_branch():
    # A team/project URL carries no issue id — must not classify as linear.
    mode, _value, _nwo = detect_source("https://linear.app/acme/team/PE/all")
    assert mode == "branch"


def test_jira_browse_url_returns_linear_mode_with_key():
    """Jira shares `linear` mode — same key shape, provider picks the prompt."""
    mode, value, nwo = detect_source("https://acme.atlassian.net/browse/PROJ-123")
    assert mode == "linear"
    assert value == "PROJ-123"
    assert nwo is None


def test_jira_board_deep_link_returns_linear_mode():
    mode, value, _nwo = detect_source(
        "https://acme.atlassian.net/jira/software/c/projects/PROJ/issues/PROJ-123"
    )
    assert mode == "linear"
    assert value == "PROJ-123"


def test_jira_url_key_beyond_bare_id_shape_still_classifies():
    """A key the bare-id guard rejects (digits in the prefix) is unambiguous in a
    URL, so the URL route classifies it where a bare `R2D2-7` would not.
    """
    assert detect_source("R2D2-7")[0] == "branch"
    mode, value, _nwo = detect_source("https://acme.atlassian.net/browse/R2D2-7")
    assert mode == "linear"
    assert value == "R2D2-7"


def test_linear_id_inside_path_stays_branch():
    """`khivi/PE-1234-foo` is a branch name, not a Linear id (no fullmatch)."""
    mode, value, _ = detect_source("khivi/PE-1234-foo")
    assert mode == "branch"
    assert value == "khivi/PE-1234-foo"


# ── gh-issue detection ──────────────────────────────────────────────────────


def test_issue_url_returns_gh_issue_mode_with_nwo():
    mode, value, nwo = detect_source("https://github.com/o/r/issues/42")
    assert mode == "gh-issue"
    assert value == "42"
    assert nwo == "o/r"


def test_pr_url_still_pr_not_gh_issue():
    mode, value, nwo = detect_source("https://github.com/o/r/pull/42")
    assert mode == "pr"
    assert value == "42" and nwo == "o/r"


@pytest.mark.parametrize("token", ["i#42", "gh#42", "I#42"])
def test_issue_shorthand_returns_gh_issue_mode(token):
    mode, value, nwo = detect_source(token)
    assert mode == "gh-issue"
    assert value == "42"
    assert nwo is None


def test_bare_hash_number_stays_pr_not_issue():
    """`#42` is ambiguous (PR/issue share a number space) → stays PR mode."""
    mode, value, _ = detect_source("#42")
    assert mode == "pr"
    assert value == "42"


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


def test_trello_card_url_returns_trello_mode_verbatim():
    url = "https://trello.com/c/aB3dZ9"
    mode, value, nwo = detect_source(url)
    assert mode == "trello"
    assert value == url  # passed through untouched — Claude reads it via the MCP
    assert nwo is None  # no GitHub owner/repo to route to


def test_trello_card_url_with_tail_and_query_still_trello():
    url = "https://trello.com/c/aB3dZ9/42-fix-oauth?filter=x"
    mode, value, _nwo = detect_source(url)
    assert mode == "trello"
    assert value == url


def test_trello_board_url_is_branch_not_trello():
    # A board URL (`/b/`) is not a card (`/c/`) — must not classify as trello.
    mode, _value, _nwo = detect_source("https://trello.com/b/aB3dZ9/my-board")
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
    followup_calls: list[tuple[str, str]] = []

    def fake_cmux(*args, **kwargs):
        cmux_calls.append(args)
        return None

    def fake_spawn_workspace(name, cwd, command):
        cmux_calls.append(
            ("new-workspace", "--name", name, "--cwd", str(cwd), "--command", command)
        )
        return "ws:test"

    def fake_deliver_followup(ref, text):
        followup_calls.append((ref, text))
        # Also synthesized into cmux-style tuples, like `spawn_workspace` above:
        # `deliver_followup` IS the send pair (plus a readiness wait and the
        # one-line collapse), so an assertion about "the prompt was delivered
        # and submitted" must not care which primitive carried it.
        cmux_calls.append(("send", "--workspace", ref, text))
        cmux_calls.append(("send-key", "--workspace", ref, "enter"))
        return True

    monkeypatch.setattr(spawn, "cmux", fake_cmux)
    monkeypatch.setattr(spawn, "spawn_workspace", fake_spawn_workspace)
    monkeypatch.setattr(spawn, "deliver_followup", fake_deliver_followup)
    monkeypatch.setattr(spawn, "workspace_names", lambda: {})
    monkeypatch.setattr(spawn, "workspace_cwds", lambda *, include_self=False: {})
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
    _run.followup_calls = followup_calls  # type: ignore[attr-defined]
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


# ── bare `cockpit new` (no args): register cwd repo + in-place workspace ─────


def _init_git_repo(path) -> None:
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init", "-b", "main"], check=True)
    (path / "README.md").write_text("seed\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "seed",
        ],
        check=True,
    )


def test_bare_registers_no_worktree_repo_and_spawns_no_worktree(
    spawn_main, cockpit_repo, tmp_path, monkeypatch
):
    import cockpit.lib.registry as registry

    monkeypatch.setattr(registry, "gh_self_user", lambda: "khivi")
    monkeypatch.setattr(registry, "default_branch", lambda _r: "main")
    proj = tmp_path / "proj"
    _init_git_repo(proj)
    monkeypatch.chdir(proj)

    code, out, _err = spawn_main([])
    assert code == 0
    assert "(no worktree)" in out
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "cwd") == str(proj.resolve())
    assert _cmux_kwarg(call, "name") == "proj"

    cfg = json.loads((cockpit_repo.cockpit_home / "config.json").read_text())
    entry = next(r for r in cfg["repos"] if r["path"] == str(proj.resolve()))
    assert entry["use_worktree"] is False


# ── spawning into a parked repo un-parks it ─────────────────────────────────


def test_spawn_into_parked_repo_unhides_it(spawn_main, tmp_path):
    # `cycle_all` skips a parked repo, so a worktree spawned into one would never
    # be reconciled. Asking for a workspace there ends the park.
    from cockpit.lib.hidden import is_hidden, toggle_hidden

    proj = tmp_path / "proj"
    _init_git_repo(proj)
    toggle_hidden(proj)
    assert is_hidden(proj)

    code, out, _err = spawn_main(["--cwd", str(proj)])
    assert code == 0
    assert not is_hidden(proj)
    assert "un-hid parked repo" in out


def test_spawn_leaves_other_repos_parked(spawn_main, tmp_path):
    # Only the spawn target is un-parked — an unrelated parked repo stays dormant,
    # and a target that was never parked writes nothing at all.
    from cockpit.lib.hidden import load_hidden, toggle_hidden

    parked, other = tmp_path / "parked", tmp_path / "other"
    _init_git_repo(parked)
    _init_git_repo(other)
    toggle_hidden(parked)

    code, out, _err = spawn_main(["--cwd", str(other)])
    assert code == 0
    assert load_hidden() == {str(parked.resolve())}
    assert "un-hid" not in out


def test_bare_outside_git_repo_errors(spawn_main, tmp_path, monkeypatch):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    monkeypatch.chdir(plain)
    code, _out, err = spawn_main([])
    assert code == 1
    assert "not in a git repo" in err
    assert "--cwd" in err  # points at the arbitrary-dir escape hatch


def test_bare_in_managed_repo_does_not_reflag_use_worktree(
    spawn_main, cockpit_repo, monkeypatch
):
    # cwd is the already-configured `testrepo` (worktree-managed). Bare spawn
    # opens an in-place workspace but must NOT mutate the existing entry.
    monkeypatch.chdir(cockpit_repo.repo)
    code, out, _err = spawn_main([])
    assert code == 0
    assert "(no worktree)" in out
    cfg = json.loads((cockpit_repo.cockpit_home / "config.json").read_text())
    entry = next(r for r in cfg["repos"] if r["name"] == "testrepo")
    assert "use_worktree" not in entry


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


def _set_repo_key(cockpit_repo, key: str, value) -> None:
    """Mutate the FIRST repo entry of the on-disk config.json.

    `_set_config_key`'s per-repo sibling — `sidebar_tag` is a repo field, and
    setting it at the top level would silently be read as unset."""
    cfg_path = cockpit_repo.cockpit_home / "config.json"
    data = json.loads(cfg_path.read_text())
    data["repos"][0][key] = value
    cfg_path.write_text(json.dumps(data))


def test_sidebar_tag_prefixes_the_spawned_workspace_name(spawn_main, cockpit_repo):
    """`cockpit new` must apply the same tag `Worktree.workspace_name` does, or
    the daemon's next reconcile renames the workspace one tick after creation."""
    from cockpit.lib.git import SIDEBAR_TAG_SEP

    _set_repo_key(cockpit_repo, "sidebar_tag", "trp")
    code, _out, _err = spawn_main(["--name", "foo", "--repo", "testrepo"])
    assert code == 0
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "name") == f"trp{SIDEBAR_TAG_SEP}foo"


def test_no_sidebar_tag_leaves_the_spawned_name_alone(spawn_main):
    """The unset default must be byte-identical to the pre-tag behaviour."""
    code, _out, _err = spawn_main(["--name", "foo", "--repo", "testrepo"])
    assert code == 0
    assert _cmux_kwarg(spawn_main.cmux_calls[0], "name") == "foo"


def test_cwd_alone_takes_no_sidebar_tag(spawn_main, cockpit_repo, tmp_path):
    """A `--cwd` with no `--repo` has no repo determined, so it gets no tag —
    guessing one from the spawn process's cwd would stamp the workspace with
    whichever repo the user happened to be standing in."""
    _set_repo_key(cockpit_repo, "sidebar_tag", "trp")
    target = tmp_path / "freestanding"
    target.mkdir()
    code, _out, _err = spawn_main(["--cwd", str(target)])
    assert code == 0
    assert _cmux_kwarg(spawn_main.cmux_calls[0], "name") == "freestanding"


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


def test_review_prompt_leads_with_default_review_command():
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
    assert p.startswith("/review")  # the built-in default
    assert "#7" in p and "coworker" in p and "fix the thing" in p
    assert "Ask before posting" in p


def test_review_prompt_without_pr_info_mentions_branch():
    import cockpit.spawn as spawn

    p = spawn._review_prompt("coworker/x", None)
    assert p.startswith("/review")
    assert "coworker/x" in p


def test_review_prompt_uses_custom_command():
    import cockpit.spawn as spawn

    p = spawn._review_prompt("coworker/x", None, command="/pr-review")
    assert p.startswith("/pr-review")
    assert "/review\n" not in p  # the built-in default isn't seeded


# ── skills.plan (plan-only first-turn slash command) ────────────────────────


def test_plan_only_prompt_uses_builtin_prose_by_default():
    import cockpit.spawn as spawn

    p = spawn._plan_only_prompt("khivi/feature", None)
    assert p.startswith("You are starting a fresh task")
    assert "PLAN ONLY" in p


def test_plan_only_prompt_uses_custom_command():
    import cockpit.spawn as spawn

    p = spawn._plan_only_prompt(
        "khivi/feature",
        {
            "number": 7,
            "title": "fix the thing",
            "author": {"login": "coworker"},
            "url": "https://github.com/o/n/pull/7",
        },
        command="/plan-pr",
    )
    assert p.startswith("/plan-pr")
    assert "#7" in p and "fix the thing" in p
    assert "PLAN ONLY" in p  # the shared no-code gate always rides along


def test_plan_only_branch_mode_seeds_configured_skills_plan_command(
    spawn_main, cockpit_repo
):
    """`skills.plan` set (global or per-repo) → the plan-only spawn leads with
    the configured slash command; the safety gate is never externalized."""
    _set_config_key(cockpit_repo, "skills", {"plan": "/plan-pr"})
    code, _out, _err = spawn_main(["PE-1234", "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "/plan-pr" in cmd
    assert "PLAN ONLY" in cmd


def test_plan_only_branch_mode_falls_back_to_builtin_when_skills_plan_unset(
    spawn_main,
):
    code, _out, _err = spawn_main(["PE-1234", "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "You are starting a fresh task" in cmd
    assert "PLAN ONLY" in cmd


def test_review_branch_mode_seeds_custom_review_command(
    spawn_main, push_branch, monkeypatch
):
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    push_branch("khivi/reviewme")
    code, _out, _err = spawn_main(
        [
            "--branch",
            "khivi/reviewme",
            "--repo",
            "testrepo",
            "--review",
            "--review-command",
            "/pr-review",
        ]
    )
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "/pr-review" in cmd


def test_review_branch_mode_seeds_default_review_command(
    spawn_main, push_branch, monkeypatch
):
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    push_branch("khivi/reviewme")
    code, _out, _err = spawn_main(
        ["--branch", "khivi/reviewme", "--repo", "testrepo", "--review"]
    )
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "/review" in cmd  # --review-command omitted → built-in default
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


def test_actions_prompt_uses_custom_command():
    import cockpit.spawn as spawn

    p = spawn._actions_prompt(
        "khivi/ci-fix",
        _actions_run_info(),
        None,
        {
            "number": 42,
            "title": "fix the bug",
            "author": {"login": "khivi"},
            "url": "https://github.com/owner/repo/pull/42",
        },
        command="/actions-pr",
    )
    assert p.startswith("/actions-pr")
    assert "runs/12345" in p  # run URL
    assert "Linked PR" in p and "#42" in p
    assert "PLAN ONLY" in p  # the shared no-code gate always rides along


def test_actions_branch_mode_seeds_configured_skills_actions_command(
    spawn_main, cockpit_repo, monkeypatch
):
    """`skills.actions` set → the Actions-run spawn leads with the configured
    slash command instead of the built-in `--log-failed` investigation prose."""
    import cockpit.spawn as spawn

    _set_config_key(cockpit_repo, "skills", {"actions": "/actions-pr"})
    monkeypatch.setattr(spawn, "fetch_run_info", lambda *a, **kw: _actions_run_info())
    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)

    code, _out, _err = spawn_main(
        ["https://github.com/owner/repo/actions/runs/12345", "--repo", "testrepo"]
    )
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "/actions-pr" in cmd
    assert "gh run view" not in cmd  # built-in log-fetch step is not seeded
    assert "PLAN ONLY" in cmd


def test_actions_branch_mode_falls_back_to_builtin_when_skills_actions_unset(
    spawn_main, monkeypatch
):
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "fetch_run_info", lambda *a, **kw: _actions_run_info())
    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)

    code, _out, _err = spawn_main(
        ["https://github.com/owner/repo/actions/runs/12345", "--repo", "testrepo"]
    )
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "gh run view 12345 --log-failed" in cmd
    assert "PLAN ONLY" in cmd


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
    _set_config_key(cockpit_repo, "tickets", "linear")

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


def test_positional_jira_prompt_instructs_mcp_fetch(
    spawn_main, cockpit_repo, monkeypatch
):
    """Under `tickets: jira`, a ticket-shaped positional seeds the Jira fetch
    prompt (delegated to the Atlassian/Jira MCP) — no `claude mcp list`
    pre-flight, mirroring Slack. Branch is the lowercased key."""
    _set_config_key(cockpit_repo, "tickets", "jira")
    code, out, _err = spawn_main(["PROJ-1234", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/proj-1234" in out
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "PROJ-1234" in cmd
    assert "Atlassian/Jira MCP" in cmd
    assert "STOP" in cmd
    assert "PLAN ONLY" in cmd
    assert "retry the SAME MCP tool call up to three times" in cmd
    assert "/mcp" in cmd


def test_positional_linear_prompt_instructs_branch_rename(
    spawn_main, cockpit_repo, monkeypatch
):
    """Step 2 of the Linear prompt asks Claude to rename the branch to include
    the ticket title slug — that's how the title gets into the branch name
    without cockpit ever calling the Linear API. The prompt reads the current
    branch via git so it's robust against `-2`/`-3` collision bumping."""
    _set_config_key(cockpit_repo, "tickets", "linear")

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
    _set_config_key(cockpit_repo, "tickets", "linear")

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


def test_linear_default_off_skips_mcp_instructing_prompt(spawn_main):
    """Default (use_linear absent) → no 'Linear MCP', no 'STOP', no rename
    instructions — only the generic plan-only prompt."""
    code, out, _err = spawn_main(["PE-1234", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/pe-1234" in out
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Linear MCP" not in cmd
    assert "STOP" not in cmd
    assert 'git branch -m "$CUR" "$CUR-<slug>"' not in cmd
    assert "cmux workspace-action" not in cmd
    assert "PLAN ONLY" in cmd  # generic plan prompt still present


def test_linear_seeds_smart_prompt_with_no_mcp_pre_flight(spawn_main, cockpit_repo):
    """`tickets: linear` always seeds the MCP fetch prompt — there is no
    `claude mcp list` pre-flight, so nothing can downgrade it to plain branch
    mode.

    Regression: the probe health-checks each server by *connecting* to it, and a
    claude.ai-managed connector handshakes asynchronously, so it reported the
    Linear entry missing while the connector was live. The spawn then printed
    "Linear MCP not detected" and seeded the generic plan prompt — silently
    dropping the ticket fetch on exactly the setup the feature targets. Jira,
    Trello, Slack and GitHub never had the probe; Linear was the last holdout.
    `prompts/linear.txt`'s retry-then-STOP step is what handles a genuinely
    absent connector, in-session.
    """
    _set_config_key(cockpit_repo, "tickets", "linear")
    code, _out, err = spawn_main(["PE-1234", "--repo", "testrepo"])
    assert code == 0
    assert "not detected" not in err  # the removed fallback warning
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Linear MCP" in cmd
    assert "STOP" in cmd


def test_spawn_never_shells_out_to_claude_mcp_list(spawn_main, cockpit_repo):
    """The probe is gone at the source, not just unused: a Linear spawn must
    make no `claude mcp list` subprocess call at all. Guards against it being
    reintroduced as a "cheap" pre-flight — it costs up to 15s per spawn and
    answers wrongly."""
    _set_config_key(cockpit_repo, "tickets", "linear")
    import cockpit.lib.linear as linear_mod

    assert not hasattr(linear_mod, "linear_mcp_available")

    calls: list[list[str]] = []
    real_run = subprocess.run

    def _spy(cmd, *a, **kw):
        if isinstance(cmd, list | tuple):
            calls.append(list(cmd))
        return real_run(cmd, *a, **kw)

    with patch("subprocess.run", _spy):
        assert spawn_main(["PE-1234", "--repo", "testrepo"])[0] == 0
    assert not [c for c in calls if c[:1] == ["claude"]], calls
    # A no-`claude` assertion passes trivially if the spy sees nothing at all,
    # so pin that it observed the spawn's real `git` calls. Without this the
    # test rots silently the day the fixture stubs `subprocess.run` out.
    assert calls, "spy observed no subprocess calls — the assertion above is vacuous"


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


# ── trello dispatch ────────────────────────────────────────────────────────
#
# A Trello card URL has no human name, so spawn synthesizes a deterministic
# codename branch from the card's short link (same shape as Slack) and — when
# `tickets: trello` — seeds a prompt delegating the card read to the Trello MCP.
# Cockpit never calls the Trello API at spawn time; no `claude mcp list` probe.

_TRELLO_URL = "https://trello.com/c/aB3dZ9"


def test_positional_trello_creates_codename_branch(spawn_main):
    from cockpit.lib.codename import codename
    from cockpit.lib.trello import trello_seed

    expected = codename(trello_seed(_TRELLO_URL))
    code, out, _err = spawn_main([_TRELLO_URL, "--repo", "testrepo"])
    assert code == 0
    assert f"on khivi/{expected}" in out
    assert _cmux_kwarg(spawn_main.cmux_calls[0], "name") == expected


def test_trello_branch_is_deterministic_across_tail_and_query(spawn_main):
    """The same card linked with a slug tail / query resolves to the same
    codename branch — the seed is the card short link, not the full URL."""
    from cockpit.lib.codename import codename
    from cockpit.lib.trello import trello_seed

    plain = codename(trello_seed(_TRELLO_URL))
    with_tail = codename(trello_seed(_TRELLO_URL + "/7-slug?filter=x"))
    assert plain == with_tail


def test_trello_non_provider_seeds_plan_only_no_rename(spawn_main):
    """Default (tickets != trello) → codename branch + plan-only, no MCP-fetch
    or rename instructions (mirrors the gh-issue non-provider path)."""
    code, _out, _err = spawn_main([_TRELLO_URL, "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Trello card" not in cmd
    assert 'git branch -m "$CUR" "$CUR-<slug>"' not in cmd
    assert "cmux workspace-action" not in cmd
    assert "PLAN ONLY" in cmd


def test_trello_provider_seeds_fetch_and_rename(spawn_main, cockpit_repo):
    """tickets: trello → full prompt: read via the Trello MCP, append a topic
    slug to the codename branch, rename the workspace. Mirrors the Slack/Jira
    flow, including the immediate-retry (no shell `sleep`) and /mcp STOP guard."""
    _set_config_key(cockpit_repo, "tickets", "trello")
    code, _out, _err = spawn_main([_TRELLO_URL, "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert _TRELLO_URL in cmd
    assert "Trello card" in cmd
    assert "Trello MCP" in cmd
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
    _set_config_key(cockpit_repo, "tickets", "linear")

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

    _set_config_key(cockpit_repo, "skills", {"session": "/session-coordination"})
    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    spawn_main(["fresh-feat", "--repo", "testrepo"])
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "/session-coordination" in cmd
    assert "PLAN ONLY" not in cmd  # prefix only, no plan guidance


def test_prefix_and_body_split_into_two_sends(spawn_main, cockpit_repo, monkeypatch):
    """With a `prompt_prefix` configured AND a seeded body (here a PR plan
    prompt), the prefix slash command rides in as the initial `claude` command
    on its own, and the body is delivered as a SEPARATE follow-up submission —
    so the skill and the task don't collapse onto one slash-command line."""
    import cockpit.spawn as spawn

    _set_config_key(cockpit_repo, "skills", {"session": "/session-coordination"})
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
    # Initial command is the prefix alone — body text is NOT collapsed onto it.
    assert cmd == "claude '/session-coordination'"
    assert "PLAN ONLY" not in cmd
    assert "#99" not in cmd
    # The body arrives as a single separate follow-up send.
    assert len(spawn_main.followup_calls) == 1
    _ref, body = spawn_main.followup_calls[0]
    assert "PLAN ONLY" in body
    assert "#99" in body


def test_body_only_no_prefix_stays_single_send(spawn_main, monkeypatch):
    """No `prompt_prefix` configured → the body rides in as the initial command
    and there is NO follow-up send (unchanged single-send behavior)."""
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
    assert "PLAN ONLY" in cmd  # body in the initial command, as before
    assert spawn_main.followup_calls == []


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


# ── --context injection ───────────────────────────────────────────────────


def test_context_injected_into_seeded_prompt(spawn_main, monkeypatch):
    """`--context <text>` is folded into the seeded prompt under a labeled
    heading, without clobbering the plan-only guard."""
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    spawn_main(["ctx-feat", "--repo", "testrepo", "--context", "goal: fix X"])
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Caller session context" in cmd
    assert "goal: fix X" in cmd
    assert "PLAN ONLY" in cmd  # seeded prompt preserved


def test_bare_context_errors(spawn_main):
    """Bare `--context` means 'summarize this session' — a job only the calling
    agent can do. Reaching the CLI unexpanded must fail loudly, not spawn a
    workspace that silently inherits nothing."""
    code, _out, err = spawn_main(["ctx-feat", "--repo", "testrepo", "--context"])
    assert code == 2
    assert "--context with no text" in err
    assert not spawn_main.cmux_calls


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
    """On attach, the `-- <text>` addendum and `--context` both ride into
    the running session via cmux send, same as a fresh spawn's --command."""
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "pr_for_branch", lambda *_a, **_kw: None)
    monkeypatch.setattr(spawn, "workspace_names", lambda: {"workspace:9": "ctx-attach"})
    spawn_main(
        [
            "ctx-attach",
            "--repo",
            "testrepo",
            "--context",
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
# Under a linear provider and no `--repo`, a positional Linear key is routed to
# the repo whose `tickets.keys` list contains the prefix. Single match wins;
# multi-match warns + falls back; no match falls back; the explicit `--repo`
# flag always wins.


def _add_linear_keys(cockpit_repo, keys: list[str], repo_name: str = "testrepo"):
    _set_repo_tickets(cockpit_repo, {"provider": "linear", "keys": keys}, repo_name)


def test_linear_key_routes_to_matching_repo_without_repo_flag(
    spawn_main, cockpit_repo, monkeypatch
):
    _set_config_key(cockpit_repo, "tickets", "linear")
    _add_linear_keys(cockpit_repo, ["PE"])

    code, out, _err = spawn_main(["PE-1234"])
    assert code == 0
    assert "on khivi/pe-1234" in out


def test_linear_key_routing_case_insensitive(spawn_main, cockpit_repo, monkeypatch):
    _set_config_key(cockpit_repo, "tickets", "linear")
    _add_linear_keys(cockpit_repo, ["pe"])

    code, out, _err = spawn_main(["PE-1234"])
    assert code == 0
    assert "on khivi/pe-1234" in out


def test_linear_key_routing_explicit_repo_wins(spawn_main, cockpit_repo, monkeypatch):
    """With `--repo testrepo` set, the team-key lookup is skipped — even
    if the lookup would otherwise route elsewhere or find nothing."""
    _set_config_key(cockpit_repo, "tickets", "linear")
    # No linear_keys configured anywhere; --repo still drives the spawn.

    code, out, _err = spawn_main(["PE-1234", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/pe-1234" in out


def test_linear_key_routing_disabled_without_a_provider(
    spawn_main, cockpit_repo, monkeypatch
):
    """`tickets.keys` alone names no provider (Jira declares the same field), so
    routing stays off and the spawn falls back to cwd discovery — which fails
    under tests (no managed repo at the test process cwd)."""
    _set_repo_tickets(cockpit_repo, {"keys": ["PE"]})  # would match if routing ran
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "discover_repo", lambda: None)
    code, _out, err = spawn_main(["PE-1234"])
    assert code != 0
    assert "cannot determine repo" in err


def test_linear_key_routing_reads_the_candidates_not_the_global_block(
    spawn_main, cockpit_repo, monkeypatch
):
    """The routing gate asks the matched *candidates* about their provider. With
    the provider declared per repo and nothing global, a global-only read saw
    "none" and switched routing off for the very repo declaring the key."""
    _add_linear_keys(cockpit_repo, ["PE"])  # per-repo provider, no global block
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "discover_repo", lambda: None)
    code, out, _err = spawn_main(["PE-1234"])
    assert code == 0
    assert "on khivi/pe-1234" in out


def test_linear_key_routing_multi_match_warns_and_falls_back(
    spawn_main, cockpit_repo, monkeypatch, tmp_path
):
    """Two repos declaring `PE` → stderr note, fall back to cwd discovery
    (which fails under tests)."""
    _set_config_key(cockpit_repo, "tickets", "linear")
    cfg_path = cockpit_repo.cockpit_home / "config.json"
    data = json.loads(cfg_path.read_text())
    data["repos"][0]["tickets"] = {"provider": "linear", "keys": ["PE"]}
    data["repos"].append(
        {
            "name": "second",
            "path": str(tmp_path / "second"),
            "branch_prefix": "khivi/",
            "default_base": "main",
            "tickets": {"provider": "linear", "keys": ["PE"]},
        }
    )
    cfg_path.write_text(json.dumps(data))

    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "discover_repo", lambda: None)
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
    _set_config_key(cockpit_repo, "tickets", "linear")
    _add_linear_keys(cockpit_repo, ["ENG"])  # different prefix
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "discover_repo", lambda: None)
    code, _out, err = spawn_main(["PE-1234"])
    assert code != 0
    assert "cannot determine repo" in err
    assert "matches multiple repos" not in err  # silent on no-match


# ── jira project-key routing ──────────────────────────────────────────────
#
# A Jira project key IS the identifier prefix — the analogue of a Linear team —
# so it routes through the very same `tickets.keys` lookup, with no `project`
# tiebreaker below it (JIRA is a `_no_narrow` passthrough).


def _set_repo_tickets(cockpit_repo, block: dict, repo_name: str = "testrepo") -> None:
    cfg_path = cockpit_repo.cockpit_home / "config.json"
    data = json.loads(cfg_path.read_text())
    for r in data["repos"]:
        if r["name"] == repo_name:
            r["tickets"] = block
    cfg_path.write_text(json.dumps(data))


def test_jira_key_routes_to_matching_repo_without_repo_flag(spawn_main, cockpit_repo):
    _set_config_key(cockpit_repo, "tickets", "jira")
    _set_repo_tickets(cockpit_repo, {"keys": ["PROJ"]})
    code, out, _err = spawn_main(["PROJ-123"])
    assert code == 0
    assert "on khivi/proj-123" in out


def test_jira_key_routing_no_match_falls_back_to_cwd(
    spawn_main, cockpit_repo, monkeypatch
):
    _set_config_key(cockpit_repo, "tickets", "jira")
    _set_repo_tickets(cockpit_repo, {"keys": ["OTHER"]})
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "discover_repo", lambda: None)
    code, _out, err = spawn_main(["PROJ-123"])
    assert code != 0
    assert "cannot determine repo" in err
    assert "matches multiple repos" not in err


def test_jira_key_routing_multi_match_warns_and_falls_back(
    spawn_main, cockpit_repo, monkeypatch, tmp_path
):
    """Two repos declaring `PROJ` → the shared ambiguity note, then cwd fallback
    (which fails under tests). Jira has no tiebreaker, so nothing is fetched."""
    _set_config_key(cockpit_repo, "tickets", "jira")
    _set_repo_tickets(cockpit_repo, {"keys": ["PROJ"]})
    cfg_path = cockpit_repo.cockpit_home / "config.json"
    data = json.loads(cfg_path.read_text())
    data["repos"].append(
        {
            "name": "second",
            "path": str(tmp_path / "second"),
            "branch_prefix": "khivi/",
            "default_base": "main",
            "tickets": {"keys": ["PROJ"]},
        }
    )
    cfg_path.write_text(json.dumps(data))

    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "discover_repo", lambda: None)
    code, _out, err = spawn_main(["PROJ-123"])
    assert code != 0
    assert "matches multiple repos" in err
    assert "testrepo" in err and "second" in err


# ── trello board routing ───────────────────────────────────────────────────
#
# A card short link carries no board, so there is no free first-stage match:
# declaring `tickets.board` is the opt-in, and only ≥2 declarers pay a fetch.


def test_trello_card_routes_to_the_lone_repo_declaring_a_board(
    spawn_main, cockpit_repo
):
    _set_config_key(cockpit_repo, "tickets", "trello")
    _set_repo_tickets(cockpit_repo, {"board": "Engineering"})
    with patch("cockpit.lib.tickets.fetch_card_board") as fetch:
        code, out, _err = spawn_main([_TRELLO_URL])
    assert code == 0
    assert "on khivi/" in out
    # One candidate is already an answer — the free match, no round-trip.
    fetch.assert_not_called()


def test_trello_card_routing_makes_no_network_call_without_a_board(
    spawn_main, cockpit_repo, monkeypatch
):
    """No repo opts in → empty candidate set → zero fetches and the pre-existing
    cwd-discovery route (which fails under tests)."""
    _set_config_key(cockpit_repo, "tickets", "trello")
    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "discover_repo", lambda: None)
    with patch("cockpit.lib.tickets.fetch_card_board") as fetch:
        code, _out, err = spawn_main([_TRELLO_URL])
    assert code != 0
    assert "cannot determine repo" in err
    fetch.assert_not_called()


def test_trello_card_routing_narrows_by_board_when_several_declare_one(
    spawn_main, cockpit_repo, monkeypatch, tmp_path
):
    _set_config_key(cockpit_repo, "tickets", "trello")
    _set_repo_tickets(cockpit_repo, {"board": "Engineering"})
    cfg_path = cockpit_repo.cockpit_home / "config.json"
    data = json.loads(cfg_path.read_text())
    data["repos"].append(
        {
            "name": "second",
            "path": str(tmp_path / "second"),
            "branch_prefix": "khivi/",
            "default_base": "main",
            "tickets": {"board": "Marketing"},
        }
    )
    cfg_path.write_text(json.dumps(data))
    monkeypatch.setenv("TRELLO_API_KEY", "k")
    monkeypatch.setenv("TRELLO_API_TOKEN", "t")

    with patch(
        "cockpit.lib.tickets.fetch_card_board", return_value="Engineering"
    ) as fetch:
        code, out, _err = spawn_main([_TRELLO_URL])
    assert code == 0
    assert "on khivi/" in out
    fetch.assert_called_once_with("aB3dZ9", key="k", token="t")


def test_trello_card_routing_inconclusive_fetch_warns_and_falls_back(
    spawn_main, cockpit_repo, monkeypatch, tmp_path
):
    """`narrow_repos` never narrows to zero, so a failed fetch leaves both
    candidates and prints the shared ambiguity note."""
    _set_config_key(cockpit_repo, "tickets", "trello")
    _set_repo_tickets(cockpit_repo, {"board": "Engineering"})
    cfg_path = cockpit_repo.cockpit_home / "config.json"
    data = json.loads(cfg_path.read_text())
    data["repos"].append(
        {
            "name": "second",
            "path": str(tmp_path / "second"),
            "branch_prefix": "khivi/",
            "default_base": "main",
            "tickets": {"board": "Marketing"},
        }
    )
    cfg_path.write_text(json.dumps(data))
    monkeypatch.setenv("TRELLO_API_KEY", "k")
    monkeypatch.setenv("TRELLO_API_TOKEN", "t")

    import cockpit.spawn as spawn

    monkeypatch.setattr(spawn, "discover_repo", lambda: None)
    with patch("cockpit.lib.tickets.fetch_card_board", return_value=None):
        code, _out, err = spawn_main([_TRELLO_URL])
    assert code != 0
    assert "matches multiple repos" in err
    assert "testrepo" in err and "second" in err


# ── per-repo / per-org provider gate ───────────────────────────────────────
#
# The fetch+rename prompt is gated on the provider resolved for the repo the
# spawn lands in (`repo_tickets`: repo → org → global), not on the global
# `tickets` block alone. Reading it globally made every ticket spawn into a repo
# whose provider lives on its own entry — or on a shared `orgs` block, the
# many-repos-one-team shape — come up on a bare branch with no ticket context.
# Every provider is gated through the same table, so each gets a case here.


def _set_org_tickets(
    cockpit_repo, block: dict, org: str = "acme", repo_name: str = "testrepo"
) -> None:
    """Declare `tickets` on an `orgs` block and point a repo at it — the
    provider is then reachable only through the org rung `load_config` merges in.
    """
    cfg_path = cockpit_repo.cockpit_home / "config.json"
    data = json.loads(cfg_path.read_text())
    data.setdefault("orgs", {})[org] = {"tickets": block}
    for r in data["repos"]:
        if r["name"] == repo_name:
            r["org"] = org
    cfg_path.write_text(json.dumps(data))


def test_trello_prompt_seeded_when_provider_is_org_level(spawn_main, cockpit_repo):
    """The reported bug, end to end: Trello on an `orgs` block, nothing global."""
    _set_org_tickets(cockpit_repo, {"provider": "trello"})
    code, _out, _err = spawn_main([_TRELLO_URL, "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Trello MCP" in cmd
    assert _TRELLO_URL in cmd


def test_trello_prompt_seeded_when_provider_is_repo_level(spawn_main, cockpit_repo):
    _set_repo_tickets(cockpit_repo, {"provider": "trello"})
    code, _out, _err = spawn_main([_TRELLO_URL, "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Trello MCP" in cmd


def test_linear_prompt_seeded_when_provider_is_org_level(spawn_main, cockpit_repo):
    _set_org_tickets(cockpit_repo, {"provider": "linear"})
    code, _out, _err = spawn_main(["PE-1234", "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Linear MCP" in cmd
    assert "PE-1234" in cmd


def test_jira_prompt_seeded_when_provider_is_org_level(spawn_main, cockpit_repo):
    """Same mode as Linear (shared identifier shape) — the resolved provider is
    the only thing that picks Jira's prompt over Linear's."""
    _set_org_tickets(cockpit_repo, {"provider": "jira"})
    code, _out, _err = spawn_main(["PROJ-1234", "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Jira" in cmd
    assert "Linear MCP" not in cmd


def test_gh_issue_prompt_seeded_when_provider_is_org_level(spawn_main, cockpit_repo):
    _set_org_tickets(cockpit_repo, {"provider": "github"})
    code, _out, _err = spawn_main(["i#42", "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "gh issue view 42" in cmd


def test_repo_provider_overrides_a_conflicting_global_one(spawn_main, cockpit_repo):
    """A global `tickets: linear` must not seed Linear's prompt for a repo that
    tracks work in Trello — the repo rung wins, as it does everywhere else."""
    _set_config_key(cockpit_repo, "tickets", "linear")
    _set_repo_tickets(cockpit_repo, {"provider": "trello"})
    code, _out, _err = spawn_main([_TRELLO_URL, "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Trello MCP" in cmd


def test_provider_resolves_after_ticket_key_routing_not_from_cwd(
    spawn_main, cockpit_repo
):
    """The gate runs *after* routing. `PROJ-1` routes to `second` on its
    `tickets.keys`, so `second`'s provider (jira) picks the prompt — even though
    `testrepo`, the repo named first, is on linear. Resolving the provider before
    routing settles reads whichever repo the caller happened to be standing in.

    Both entries point at the fixture repo so the worktree really gets created;
    only the config entry the spawn selects is under test.
    """
    _set_repo_tickets(cockpit_repo, {"provider": "linear", "keys": ["PE"]})
    cfg_path = cockpit_repo.cockpit_home / "config.json"
    data = json.loads(cfg_path.read_text())
    data["repos"].append(
        {
            "name": "second",
            "path": str(cockpit_repo.repo),
            "branch_prefix": "khivi/",
            "default_base": "main",
            "tickets": {"provider": "jira", "keys": ["PROJ"]},
        }
    )
    cfg_path.write_text(json.dumps(data))

    code, _out, _err = spawn_main(["PROJ-1"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Jira" in cmd
    assert "Linear MCP" not in cmd


# ── plan-only fallback carries the source ──────────────────────────────────
#
# When no provider matches, the fetch prompt is skipped — but the ticket ref is
# the one thing cockpit can still hand over, and dropping it left a codename
# branch (`solar-viper`) with nothing to say where it came from.


def test_trello_card_without_provider_seeds_url_in_plan_only(spawn_main, cockpit_repo):
    code, _out, _err = spawn_main([_TRELLO_URL, "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Trello MCP" not in cmd
    assert "PLAN ONLY" in cmd
    assert _TRELLO_URL in cmd


def test_linear_key_without_provider_seeds_ref_in_plan_only(spawn_main, cockpit_repo):
    code, _out, _err = spawn_main(["PE-1234", "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "Linear MCP" not in cmd
    assert "PE-1234" in cmd


def test_gh_issue_without_provider_seeds_ref_in_plan_only(spawn_main, cockpit_repo):
    """The URL form's ref keeps its repo (`o/r#42`), so the session can look the
    issue up without guessing which repo it belongs to."""
    code, _out, _err = spawn_main(
        ["https://github.com/o/r/issues/42", "--repo", "testrepo"]
    )
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "gh issue view" not in cmd
    assert "o/r#42" in cmd


def test_plan_only_source_rides_a_custom_plan_command(spawn_main, cockpit_repo):
    """`skills.plan` replaces the built-in prose, so the ticket ref has to ride
    the command seed's context block too — not just `plan_only.txt`."""
    _set_config_key(cockpit_repo, "skills", {"plan": "/my-plan"})
    code, _out, _err = spawn_main([_TRELLO_URL, "--repo", "testrepo"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "/my-plan" in cmd
    assert _TRELLO_URL in cmd


def test_plain_branch_spawn_has_no_source_block(spawn_main, cockpit_repo):
    """A non-ticket source seeds nothing new — the fallback only speaks up when
    there is a ticket it could not fetch."""
    code, _out, _err = spawn_main(["my-feature", "--repo", "testrepo", "--", "do it"])
    assert code == 0
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "**Source**" not in cmd


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
    monkeypatch.setattr(
        spawn, "workspace_cwds", lambda *, include_self=False: {"wt:1": wt_path}
    )

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

    def fake_cwds(*, include_self=False):
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
    monkeypatch.setattr(
        spawn, "workspace_cwds", lambda *, include_self=False: {"ws:cwd": target}
    )

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
        lambda *, include_self=False: (_ for _ in ()).throw(RuntimeError("cmux down")),
    )

    code, _out, _err = spawn_main(["khivi/cwds-error", "--repo", "testrepo"])
    assert code == 0
    # Falls through to creating a new workspace.
    assert any("new-workspace" in str(c) for c in spawn_main.cmux_calls)


# ── gh-issue dispatch ───────────────────────────────────────────────────────
#
# A GitHub issue URL / `i#N` shorthand creates a worktree on `issue-<N>` and,
# under `tickets: github`, seeds a first-turn prompt instructing Claude to read
# the issue via `gh issue view` and rename the branch + workspace. Cockpit does
# NOT call the GitHub API for the prompt — only prompt + branch shape + gating.


def test_positional_gh_issue_creates_issue_branch(spawn_main):
    code, out, _err = spawn_main(["i#42", "--repo", "testrepo"])
    assert code == 0
    assert "on khivi/issue-42" in out
    call = spawn_main.cmux_calls[0]
    assert _cmux_kwarg(call, "name") == "issue-42"


def test_gh_issue_prompt_seeded_under_tickets_github(spawn_main, cockpit_repo):
    _set_config_key(cockpit_repo, "tickets", "github")
    spawn_main(["i#42", "--repo", "testrepo"])
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "gh issue view 42" in cmd
    assert "GitHub issue #42" in cmd
    assert "PLAN ONLY" in cmd
    assert "Closes #42" in cmd  # instructs adding the closing-keyword footer


def test_gh_issue_url_prompt_includes_repo(spawn_main, cockpit_repo):
    _set_config_key(cockpit_repo, "tickets", "github")
    spawn_main(["https://github.com/o/r/issues/42", "--repo", "testrepo"])
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "gh issue view 42 --repo o/r" in cmd
    assert "o/r#42" in cmd


def test_gh_issue_without_provider_seeds_plan_only(spawn_main, cockpit_repo):
    """tickets unset (default none) → branch still created, generic plan prompt,
    no `gh issue view` fetch instructions."""
    spawn_main(["i#42", "--repo", "testrepo"])
    cmd = _cmux_kwarg(spawn_main.cmux_calls[0], "command")
    assert "gh issue view" not in cmd
    assert "PLAN ONLY" in cmd  # is_gh_issue still seeds plan-only


def test_gh_issue_spawn_applies_start_label(spawn_main, cockpit_repo, monkeypatch):
    """With `tickets.start_label` set, spawning a worktree on an issue marks it
    'work started' via `gh issue edit --add-label` (best-effort)."""
    _set_config_key(
        cockpit_repo, "tickets", {"provider": "github", "start_label": "accepted"}
    )
    import cockpit.spawn as spawn

    calls: list[tuple[str, str]] = []

    def _record(ref, label, **kw):
        calls.append((ref, label))
        return True

    monkeypatch.setattr(spawn, "add_label", _record)
    spawn_main(["i#42", "--repo", "testrepo"])
    assert calls == [("#42", "accepted")]


def test_gh_issue_spawn_no_label_when_unset(spawn_main, cockpit_repo, monkeypatch):
    """No `start_label` → no GitHub write on spawn."""
    _set_config_key(cockpit_repo, "tickets", {"provider": "github"})
    import cockpit.spawn as spawn

    calls: list[tuple[str, str]] = []

    def _record(ref, label, **kw):
        calls.append((ref, label))
        return True

    monkeypatch.setattr(spawn, "add_label", _record)
    spawn_main(["i#42", "--repo", "testrepo"])
    assert calls == []
