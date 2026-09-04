"""Tests for `cockpit diff` (cockpit/diff.py).

CLI entry-point layer: mock at the two collaborator boundaries — `_pr_patch`
(the `gh` call) and `cmux.render_diff` (the viewer invocation). What
`render_diff` puts on the argv is its own business and is covered in
`tests/lib/test_cmux.py`; re-asserting it here would pin the same fact twice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import cockpit.diff as diff_cli
from cockpit.lib.diff_comments import Comment


@pytest.fixture
def rendered(monkeypatch):
    """Capture the kwargs of the one `render_diff` call, and report success."""
    calls: list[dict] = []

    def fake(patch=None, **kw):
        calls.append({"patch": patch, **kw})
        return ""

    monkeypatch.setattr(diff_cli, "render_diff", fake)
    return calls


@pytest.fixture
def in_worktree(monkeypatch, tmp_path):
    monkeypatch.setattr(diff_cli, "worktree_root", lambda *a, **k: tmp_path)
    monkeypatch.setattr(diff_cli, "current_branch", lambda *a: "khivi/some-fix")
    monkeypatch.setattr(diff_cli, "find_pr_payload_for_cwd", lambda *a: {"number": 42})
    return tmp_path


def test_default_opens_the_pr_patch(monkeypatch, in_worktree, rendered, capsys):
    monkeypatch.setattr(diff_cli, "_pr_patch", lambda root: ("--- a/x\n+++ b/x\n", ""))

    assert diff_cli.main([]) == 0
    (call,) = rendered
    assert call["patch"] == "--- a/x\n+++ b/x\n"
    assert call["source"] is None
    assert "PR #42" in call["title"]
    assert "PR #42" in capsys.readouterr().out


def test_no_pr_falls_back_to_branch_and_says_so(
    monkeypatch, in_worktree, rendered, capsys
):
    """A branch with no PR is the ordinary case on fresh work, not a failure:
    show the branch diff rather than exiting, and name the substitution — a
    silent fallback would read as "this IS your PR diff"."""
    monkeypatch.setattr(
        diff_cli, "_pr_patch", lambda root: ("", 'no pull requests found for "x"')
    )

    assert diff_cli.main([]) == 0
    (call,) = rendered
    assert call["patch"] is None
    assert call["source"] == "branch"
    out = capsys.readouterr().out
    assert "no PR diff" in out and "--branch instead" in out


@pytest.mark.parametrize("flag", ["--branch", "--staged", "--unstaged", "--last-turn"])
def test_source_flags_pass_through_without_touching_gh(
    monkeypatch, in_worktree, rendered, flag
):
    """A source flag is cmux's own git source — `gh` must not be reached at all,
    or `cockpit diff --staged` pays a network round-trip for a local diff."""

    def boom(root):
        raise AssertionError("gh must not run for a cmux source")

    monkeypatch.setattr(diff_cli, "_pr_patch", boom)

    assert diff_cli.main([flag]) == 0
    (call,) = rendered
    assert call["source"] == flag.lstrip("-")
    assert call["patch"] is None


def test_base_rides_along(monkeypatch, in_worktree, rendered):
    assert diff_cli.main(["--branch", "--base", "origin/stage"]) == 0
    assert rendered[0]["base"] == "origin/stage"


def test_targets_the_worktree_it_was_launched_from(monkeypatch, in_worktree, rendered):
    """The whole premise: the command runs inside the workspace it targets, so
    it names neither a workspace nor a surface and cmux's own defaults land the
    split beside this session. `cwd` is the worktree root, not the cwd — cmux
    keys the comment store by repo root and a subdirectory is not one."""
    assert diff_cli.main(["--branch"]) == 0
    call = rendered[0]
    assert "workspace" not in call
    assert "keep_surface" not in call
    assert call["cwd"] == in_worktree


def test_outside_a_git_repo_exits_2(monkeypatch, rendered, capsys):
    monkeypatch.setattr(diff_cli, "worktree_root", lambda *a, **k: None)

    assert diff_cli.main([]) == 2
    assert not rendered
    assert "not in a git repo" in capsys.readouterr().err


def test_two_sources_is_a_usage_error(in_worktree):
    with pytest.raises(SystemExit) as e:
        diff_cli.main(["--branch", "--staged"])
    assert e.value.code == 2


def test_viewer_failure_exits_1(monkeypatch, in_worktree, capsys):
    monkeypatch.setattr(diff_cli, "render_diff", lambda *a, **k: "browser is off")

    assert diff_cli.main(["--branch"]) == 1
    assert "browser is off" in capsys.readouterr().err


def test_comments_prints_without_marking_anything(
    monkeypatch, in_worktree, rendered, capsys
):
    """Reading is not addressing. The reader is the agent standing in the
    worktree, so acking on print would lose a note to any turn that died
    between reading it and acting on it — feedback that exists nowhere else."""
    monkeypatch.setattr(diff_cli, "main_worktree_path", lambda root: Path("/repo"))
    monkeypatch.setattr(
        diff_cli.diff_comments,
        "pending",
        lambda roots: [Comment(id="c1", file="a.py", line=7, message="rename this")],
    )
    marked: list[list[str]] = []
    monkeypatch.setattr(
        diff_cli.diff_comments, "mark_delivered", lambda ids: marked.append(list(ids))
    )

    assert diff_cli.main(["--comments"]) == 0
    out = capsys.readouterr().out
    assert "a.py:7 — rename this" in out
    assert "--ack" in out, "it must say how to retire them"
    assert marked == [], "--comments must mark nothing"
    assert not rendered, "--comments must not open a diff"


def test_ack_marks_them_delivered(monkeypatch, in_worktree, rendered, capsys):
    monkeypatch.setattr(diff_cli, "main_worktree_path", lambda root: Path("/repo"))
    monkeypatch.setattr(
        diff_cli.diff_comments,
        "pending",
        lambda roots: [Comment(id="c1", file="a.py", line=7, message="rename this")],
    )
    marked: list[list[str]] = []
    monkeypatch.setattr(
        diff_cli.diff_comments, "mark_delivered", lambda ids: marked.append(list(ids))
    )

    assert diff_cli.main(["--ack"]) == 0
    assert marked == [["c1"]]
    assert "acked a.py:7" in capsys.readouterr().out
    assert not rendered


def test_ack_with_nothing_pending_says_so(monkeypatch, in_worktree, capsys):
    monkeypatch.setattr(diff_cli, "main_worktree_path", lambda root: None)
    monkeypatch.setattr(diff_cli.diff_comments, "pending", lambda roots: [])
    marked: list = []
    monkeypatch.setattr(
        diff_cli.diff_comments, "mark_delivered", lambda ids: marked.append(ids)
    )

    assert diff_cli.main(["--ack"]) == 0
    assert "nothing to acknowledge" in capsys.readouterr().out
    assert marked == []


def test_comments_and_ack_are_mutually_exclusive(in_worktree):
    with pytest.raises(SystemExit) as e:
        diff_cli.main(["--comments", "--ack"])
    assert e.value.code == 2


def test_comments_offers_both_candidate_roots(monkeypatch, in_worktree):
    """Which root cmux files a worktree under is undocumented, so the lookup
    offers the worktree AND the checkout it was cut from — the same pair the
    TUI's `a` uses. Narrowing to one silently returns nothing."""
    monkeypatch.setattr(diff_cli, "main_worktree_path", lambda root: Path("/main"))
    seen: list[list] = []
    monkeypatch.setattr(
        diff_cli.diff_comments, "pending", lambda roots: seen.append(list(roots)) or []
    )

    assert diff_cli.main(["--comments"]) == 0
    assert seen == [[in_worktree, Path("/main")]]


def test_comments_with_none_pending_says_so(monkeypatch, in_worktree, capsys):
    monkeypatch.setattr(diff_cli, "main_worktree_path", lambda root: None)
    monkeypatch.setattr(diff_cli.diff_comments, "pending", lambda roots: [])

    assert diff_cli.main(["--comments"]) == 0
    assert "no pending diff comments" in capsys.readouterr().out
