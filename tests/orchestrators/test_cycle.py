"""Tests for cockpit/orchestrators/cycle.py.

Sections:
  - _resolve_skill_prompt / _run_repo_skills: fast/slow skill dispatch.
  - _maybe_autoclose: ordering + dry/error guards (delegates to orchestrators.teardown).
  - _reap_workspace_orphans: gating logic for orphan-workspace cleanup
    (ownership derived from cwd vs registered repos).
"""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

import cockpit.orchestrators.cycle as cycle
from cockpit.lib.gh import PR
from cockpit.lib.git import Worktree
from cockpit.orchestrators import teardown as teardown_mod


def _pr(
    branch: str,
    *,
    is_draft: bool = False,
    ci: str = "passed",
    unaddressed: int = 0,
    state: str = "MERGED",
) -> PR:
    """Test helper: build a PR with merged defaults that pass the smart-skip gate."""
    return PR(
        number=1,
        title="t",
        branch=branch,
        url="",
        author="khivi",
        is_draft=is_draft,
        review_decision="APPROVED",
        mergeable="MERGEABLE",
        ci=ci,
        unaddressed=unaddressed,
        total_from_others=0,
        state=state,
    )


# ────────────────────────────────────────────────────────────────────────────
# _resolve_skill_prompt / _run_repo_skills
# ────────────────────────────────────────────────────────────────────────────


def test_resolve_skill_prompt_global(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    skill_dir = tmp_path / ".claude" / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("# my-skill")
    assert cycle._resolve_skill_prompt("my-skill", tmp_path / "repo") == "/my-skill"


def test_resolve_skill_prompt_repo_local(tmp_path, monkeypatch):
    """A bare-name skill living only in the managed repo's `.claude/skills/`
    resolves against `repo_path` — not cockpit's own plugin tree."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_path = tmp_path / "managed-repo"
    skill_dir = repo_path / ".claude" / "skills" / "repo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("# repo-skill")
    assert cycle._resolve_skill_prompt("repo-skill", repo_path) == "/repo-skill"


def test_resolve_skill_prompt_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cycle._resolve_skill_prompt("nonexistent", tmp_path / "repo") is None


def test_run_repo_skills_fast_runs_subprocess(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    skill_dir = tmp_path / ".claude" / "skills" / "cleanup-worktrees"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("# cleanup-worktrees")

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo_entry = {"path": str(repo_path), "fast_skills": ["cleanup-worktrees"]}

    calls: list[tuple] = []
    with patch.object(
        cycle.subprocess, "run", side_effect=lambda *a, **kw: calls.append((a, kw))
    ):
        cycle._run_repo_skills(repo_entry, dry=False)

    assert len(calls) == 1
    cmd = calls[0][0][0]
    assert "claude -p" in cmd
    assert "/cleanup-worktrees" in cmd
    assert calls[0][1]["cwd"] == repo_path


def test_run_repo_skills_fast_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    skill_dir = tmp_path / ".claude" / "skills" / "cleanup-worktrees"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("# cleanup-worktrees")

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo_entry = {"path": str(repo_path), "fast_skills": ["cleanup-worktrees"]}

    with patch.object(cycle.subprocess, "run") as mock_run:
        cycle._run_repo_skills(repo_entry, dry=True)
        mock_run.assert_not_called()

    out = capsys.readouterr().out
    assert "dry" in out and "cleanup-worktrees" in out


def test_run_repo_skills_fast_missing_skill_skips(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    repo_entry = {"path": str(tmp_path), "fast_skills": ["ghost-skill"]}

    with patch.object(cycle.subprocess, "run") as mock_run:
        cycle._run_repo_skills(repo_entry, dry=False)
        mock_run.assert_not_called()

    out = capsys.readouterr().out
    assert "skip" in out and "ghost-skill" in out


def test_run_repo_skills_slow_spawns_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    skill_dir = tmp_path / ".claude" / "skills" / "nudge-reviewers"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("# nudge-reviewers")

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo_entry = {"path": str(repo_path), "slow_skills": ["nudge-reviewers"]}

    spawn_calls: list[tuple] = []
    with (
        patch.object(cycle, "workspace_names", return_value={}),
        patch.object(
            cycle,
            "spawn_workspace",
            side_effect=lambda *a, **kw: spawn_calls.append(a),
        ),
    ):
        cycle._run_repo_skills(repo_entry, dry=False)

    assert len(spawn_calls) == 1
    name, cwd, _command = spawn_calls[0]
    assert name == "skill-nudge-reviewers"
    assert cwd == repo_path


def test_run_repo_skills_slow_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    skill_dir = tmp_path / ".claude" / "skills" / "nudge-reviewers"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("# nudge-reviewers")

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo_entry = {"path": str(repo_path), "slow_skills": ["nudge-reviewers"]}

    with (
        patch.object(
            cycle, "workspace_names", return_value={"ws:1": "skill-nudge-reviewers"}
        ),
        patch.object(cycle, "spawn_workspace") as mock_spawn,
    ):
        cycle._run_repo_skills(repo_entry, dry=False)
        mock_spawn.assert_not_called()


def test_run_repo_skills_empty_config(tmp_path):
    repo_entry = {"path": str(tmp_path)}
    with patch.object(cycle.subprocess, "run") as mock_run:
        cycle._run_repo_skills(repo_entry, dry=False)
        mock_run.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# _maybe_autoclose: cmux workspace MUST close before worktree removal,
# otherwise the cwd is yanked out from under a live Claude Code session and
# every Stop/PreToolUse hook fails with ENOENT.
# ────────────────────────────────────────────────────────────────────────────


def test_cmux_close_runs_before_remove_worktree(tmp_path):
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    calls: list[str] = []

    def fake_cmux_close(ref):
        calls.append("cmux_close")
        return True

    def fake_remove(repo_path, path, **kwargs):
        calls.append("remove_worktree")
        return True, ""

    with (
        patch.object(
            teardown_mod,
            "cmux_close_workspace_best_effort",
            side_effect=fake_cmux_close,
        ),
        patch.object(teardown_mod, "remove_worktree", side_effect=fake_remove),
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(teardown_mod, "ff_default_branch_worktrees", return_value=[]),
        patch.object(cycle, "is_ancestor", return_value=True),
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            prs=[_pr("khivi/feat")],
            dry=False,
        )

    assert calls == [
        "cmux_close",
        "remove_worktree",
    ], f"cmux workspace must close before worktree removal; got {calls}"


def test_autoclose_dry_run_calls_neither(tmp_path):
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as remove_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(cycle, "is_ancestor", return_value=True),
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            prs=[_pr("khivi/feat")],
            dry=True,
        )

    close_mock.assert_not_called()
    remove_mock.assert_not_called()


def test_autoclose_remove_failure_still_closes_cmux_and_skips_cache_delete(tmp_path):
    """If remove_worktree fails, cmux close has already run (correct), and
    we skip delete_pr_caches_for_branch (preserves prior gating behavior)."""
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree", return_value=(False, "boom")),
        patch.object(teardown_mod, "delete_pr_caches_for_branch") as cache_mock,
        patch.object(cycle, "is_ancestor", return_value=True),
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            prs=[_pr("khivi/feat")],
            dry=False,
        )

    close_mock.assert_called_once()
    cache_mock.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# Smart-skip: don't autoclose merged worktrees whose PR signals the author
# may still want to revisit (draft / CI not passing / unaddressed threads).
# Authoritative merge signal is `gh pr list --state merged`; teardown is then
# gated by `is_ancestor` (merge head still reachable from HEAD) so a reused
# branch name is kept while a squash-merge + pull-main worktree still reaps.
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pr_kwargs,reason",
    [
        ({"is_draft": True}, "draft"),
        ({"ci": "failed:1"}, "ci=failed"),
        ({"ci": "pending"}, "ci=pending"),
        ({"unaddressed": 2}, "unaddressed"),
    ],
)
def test_autoclose_smart_skip_on_pr_signals(tmp_path, pr_kwargs, reason):
    """Skip teardown when the PR carries draft/CI/unaddressed signals."""
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as remove_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(cycle, "is_ancestor", return_value=True),
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            prs=[_pr("khivi/feat", **pr_kwargs)],
            dry=False,
        )

    close_mock.assert_not_called(), f"expected skip on {reason}"
    remove_mock.assert_not_called(), f"expected skip on {reason}"


def test_autoclose_fires_when_no_pr_in_list(tmp_path):
    """A merged branch with no PR object (e.g. coworker's merged-and-fetched
    branch not in our self-relevant list) still autocloses."""
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree", return_value=(True, "")),
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(teardown_mod, "ff_default_branch_worktrees", return_value=[]),
        patch.object(cycle, "is_ancestor", return_value=True),
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            prs=[],
            dry=False,
        )

    close_mock.assert_called_once()


def test_autoclose_reaps_when_merge_head_still_reachable(tmp_path):
    """Squash-merge + pull-main case (#98 invariant, stated correctly).

    A worktree that squash-merged then pulled main on top has a HEAD that
    advanced past the merge head — so a `count_commits_since == 0` gate would
    wrongly skip it forever. But the merge head is still an *ancestor* of HEAD,
    so the reachability gate (`is_ancestor`) keeps reaping it. The gate consults
    the commit graph; what it must not do is require HEAD == merge head.
    """
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree", return_value=(True, "")),
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(teardown_mod, "ff_default_branch_worktrees", return_value=[]),
        patch.object(cycle, "is_ancestor", return_value=True),
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            prs=[_pr("khivi/feat")],
            dry=False,
        )

    close_mock.assert_called_once()


# ────────────────────────────────────────────────────────────────────────────
# delete_branch gating: the merged-feature path also deletes the local branch
# ref, but only when HEAD sits exactly at the merge head (no post-merge local
# commits the ref is the last copy of). The main-sibling path never deletes.
# ────────────────────────────────────────────────────────────────────────────


def test_autoclose_sets_delete_branch_when_head_at_merge_head(tmp_path):
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    with (
        patch.object(cycle, "teardown") as td_mock,
        patch.object(cycle, "is_ancestor", return_value=True),
        patch.object(cycle, "has_unique_commits", return_value=False) as huc_mock,
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            prs=[_pr("khivi/feat")],
            dry=False,
        )

    huc_mock.assert_called_once_with(wt_path, "deadbeef")
    req = td_mock.call_args[0][0]
    assert req.delete_branch is True
    assert req.branch == "khivi/feat"


def test_autoclose_keeps_branch_when_post_merge_commits_exist(tmp_path):
    """HEAD advanced past the merge head with new local commits — the worktree
    still reaps (merge head is an ancestor) but the branch ref is preserved so
    the unpushed work stays recoverable."""
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    with (
        patch.object(cycle, "teardown") as td_mock,
        patch.object(cycle, "is_ancestor", return_value=True),
        patch.object(cycle, "has_unique_commits", return_value=True),
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            prs=[_pr("khivi/feat")],
            dry=False,
        )

    req = td_mock.call_args[0][0]
    assert req.delete_branch is False


def test_autoclose_main_sibling_never_deletes_branch(tmp_path):
    """The orphan main-sibling teardown path must never delete `main`."""
    wt_path = tmp_path / "ex-feat"
    wt_path.mkdir()
    wt = Worktree(
        path=wt_path, branch="main", dirty_count=0, unpushed=0, is_primary=False
    )

    with (
        patch.object(cycle, "teardown") as td_mock,
        patch.object(cycle, "has_unique_commits") as huc_mock,
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={},
            cwds={"ws-ref": wt_path},
            prs=[],
            dry=False,
        )

    req = td_mock.call_args[0][0]
    assert req.delete_branch is False
    huc_mock.assert_not_called()


def test_autoclose_keeps_reused_branch_name(tmp_path):
    """Regression for the #81 nuke: a branch name reused after its old PR merged.

    `merged_branches` still lists the branch (the old merge's headRefOid), but
    the freshly re-created worktree's HEAD is on a different lineage, so the
    merge head is NOT an ancestor of HEAD. The worktree must survive — tearing
    it down nukes a workspace the user created moments earlier.
    """
    wt_path = tmp_path / "repo-todo"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/todo", dirty_count=0)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as remove_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(cycle, "is_ancestor", return_value=False) as ancestor_mock,
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/todo": "979e571"},
            cwds={"ws-ref": wt_path},
            prs=[],
            dry=False,
        )

    # The reachability gate must actually be consulted — otherwise the no-teardown
    # assertions could pass vacuously if the branch never reached the gate.
    ancestor_mock.assert_called_once_with(wt_path, "979e571")
    close_mock.assert_not_called()
    remove_mock.assert_not_called()


def test_autoclose_skips_dirty_even_with_clean_pr(tmp_path):
    """Uncommitted local work still wins over a clean merged PR."""
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=3)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as remove_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(cycle, "is_ancestor", return_value=True),
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            prs=[_pr("khivi/feat")],
            dry=False,
        )

    close_mock.assert_not_called()
    remove_mock.assert_not_called()


def test_autoclose_clears_devdone_pill_when_skipped_dirty(tmp_path):
    """A merged worktree whose teardown is skipped (dirty/mid-turn) must still
    clear any `devdone=` pill — the PR has left the tracked open-PR set, so
    `_track_dev_done` will never run again to clear it. Regression: a `devdone`
    pill stranded forever on a merged-but-running workspace.
    """
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=3)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as remove_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(cycle, "is_ancestor", return_value=True),
        patch.object(cycle, "apply_devdone_pill") as devdone_mock,
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            prs=[_pr("khivi/feat")],
            dry=False,
        )

    close_mock.assert_not_called()
    remove_mock.assert_not_called()
    devdone_mock.assert_called_once_with("ws-ref", None)


def test_autoclose_dry_run_does_not_clear_devdone_pill(tmp_path):
    """Dry runs never mutate pills — the devdone= clear is gated on `not dry`."""
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=3)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort"),
        patch.object(teardown_mod, "remove_worktree"),
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(cycle, "is_ancestor", return_value=True),
        patch.object(cycle, "apply_devdone_pill") as devdone_mock,
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            prs=[_pr("khivi/feat")],
            dry=True,
        )

    devdone_mock.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# Orphan-on-main: a non-trunk worktree FF'd onto main loses its original
# branch name, so `merged_branches` can't identify it. Autoclose still cleans
# it up when the working tree is clean and aligned with origin's default.
# ────────────────────────────────────────────────────────────────────────────


def test_autoclose_orphan_main_sibling_clean(tmp_path):
    """Non-primary worktree on main with dirty=0/unpushed=0 is torn down."""
    wt_path = tmp_path / "ex-feat"
    wt_path.mkdir()
    wt = Worktree(
        path=wt_path,
        branch="main",
        dirty_count=0,
        unpushed=0,
        is_primary=False,
    )

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree", return_value=(True, "")),
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(teardown_mod, "ff_default_branch_worktrees", return_value=[]),
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={},
            cwds={"ws-ref": wt_path},
            prs=[],
            dry=False,
        )

    close_mock.assert_called_once()


def test_autoclose_orphan_main_sibling_dirty_skipped(tmp_path):
    """Same setup as orphan_main_sibling_clean but with uncommitted work — skip
    teardown, and surface a WIP pill so the cell explains why it's kept."""
    wt_path = tmp_path / "ex-feat"
    wt_path.mkdir()
    wt = Worktree(
        path=wt_path,
        branch="main",
        dirty_count=2,
        unpushed=0,
        is_primary=False,
    )

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as remove_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(cycle, "apply_wip_pill") as wip_mock,
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={},
            cwds={"ws-ref": wt_path},
            prs=[],
            dry=False,
        )

    close_mock.assert_not_called()
    remove_mock.assert_not_called()
    wip_mock.assert_called_once_with("ws-ref", 2)


def test_autoclose_orphan_main_sibling_clean_no_wip_pill(tmp_path):
    """A clean main sibling is torn down, not annotated with a WIP pill."""
    wt_path = tmp_path / "ex-feat"
    wt_path.mkdir()
    wt = Worktree(
        path=wt_path, branch="main", dirty_count=0, unpushed=0, is_primary=False
    )

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort"),
        patch.object(teardown_mod, "remove_worktree", return_value=(True, "")),
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(teardown_mod, "ff_default_branch_worktrees", return_value=[]),
        patch.object(cycle, "apply_wip_pill") as wip_mock,
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={},
            cwds={"ws-ref": wt_path},
            prs=[],
            dry=False,
        )

    wip_mock.assert_not_called()


def test_autoclose_dirty_main_sibling_dry_run_no_wip_pill(tmp_path):
    """Dry run never writes — no WIP pill applied even when held back dirty."""
    wt_path = tmp_path / "ex-feat"
    wt_path.mkdir()
    wt = Worktree(
        path=wt_path, branch="main", dirty_count=2, unpushed=0, is_primary=False
    )

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort"),
        patch.object(teardown_mod, "remove_worktree"),
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(cycle, "apply_wip_pill") as wip_mock,
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={},
            cwds={"ws-ref": wt_path},
            prs=[],
            dry=True,
        )

    wip_mock.assert_not_called()


def test_autoclose_orphan_main_sibling_unpushed_skipped(tmp_path):
    """Local commits not on origin/main — can't safely sweep."""
    wt_path = tmp_path / "ex-feat"
    wt_path.mkdir()
    wt = Worktree(
        path=wt_path,
        branch="main",
        dirty_count=0,
        unpushed=3,
        is_primary=False,
    )

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as remove_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={},
            cwds={"ws-ref": wt_path},
            prs=[],
            dry=False,
        )

    close_mock.assert_not_called()
    remove_mock.assert_not_called()


def test_autoclose_orphan_main_sibling_unpushed_unknown_skipped(tmp_path):
    """`unpushed == -1` means git failed; treat as unknown and don't sweep."""
    wt_path = tmp_path / "ex-feat"
    wt_path.mkdir()
    wt = Worktree(
        path=wt_path,
        branch="main",
        dirty_count=0,
        unpushed=-1,
        is_primary=False,
    )

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as remove_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={},
            cwds={"ws-ref": wt_path},
            prs=[],
            dry=False,
        )

    close_mock.assert_not_called()
    remove_mock.assert_not_called()


def test_autoclose_primary_on_main_never_swept(tmp_path):
    """The trunk worktree (is_primary=True) is always skipped, even with
    dirty=0/unpushed=0 — we must not nuke the user's main checkout."""
    wt_path = tmp_path / "repo"
    wt_path.mkdir()
    wt = Worktree(
        path=wt_path,
        branch="main",
        dirty_count=0,
        unpushed=0,
        is_primary=True,
    )

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as remove_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
    ):
        cycle._maybe_autoclose(
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={},
            cwds={"ws-ref": wt_path},
            prs=[],
            dry=False,
        )

    close_mock.assert_not_called()
    remove_mock.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# _reap_workspace_orphans: a workspace is reap-eligible iff its cwd resolves
# under a registered repo (main path or live worktree) AND no live worktree
# matches by cwd or name.
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def reap_isolated(tmp_path, monkeypatch):
    """Isolated COCKPIT_HOME and reloaded modules so each test starts fresh."""
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    import cockpit.lib.config as cfg

    importlib.reload(cfg)
    import cockpit.lib.daemon_signal as cr

    importlib.reload(cr)
    import cockpit.orchestrators.cycle as cycle_mod

    importlib.reload(cycle_mod)
    return cycle_mod, cr


def _wt_stub(path: Path, branch: str):
    return Worktree(path=path, branch=branch, dirty_count=0, unpushed=0)


def test_reap_skips_tracked_workspace(reap_isolated, tmp_path):
    cycle_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt_path = tmp_path / "wt-tracked"
    wt_path.mkdir()
    wt = _wt_stub(wt_path, "khivi/feat")

    repos = [{"path": str(repo_path), "name": "repo"}]

    with (
        patch.object(cycle_mod, "worktrees_basic", return_value=[wt]),
        patch.object(
            cycle_mod,
            "workspace_state",
            return_value=({"workspace:1": "feat-x"}, {"workspace:1": wt_path}),
        ),
    ):
        cycle_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []


def test_reap_enqueues_stranded_workspace_in_registered_repo(reap_isolated, tmp_path):
    cycle_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]

    ghost_cwd = repo_path / "removed-worktree"
    ghost_cwd.mkdir()

    with (
        patch.object(cycle_mod, "worktrees_basic", return_value=[wt]),
        patch.object(
            cycle_mod,
            "workspace_state",
            return_value=(
                {"workspace:99": "khivi/ghost"},
                {"workspace:99": ghost_cwd},
            ),
        ),
        patch.object(cycle_mod, "workspace_is_idle", return_value=True),
    ):
        cycle_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    pending = cr.iter_pending()
    assert len(pending) == 1
    _, req = pending[0]
    assert req.ref == "workspace:99"
    assert req.worktree_path is None
    assert req.forced is True
    assert req.repo_name == "repo"


def test_reap_defers_when_workspace_not_idle(reap_isolated, tmp_path, capsys):
    """A stranded workspace whose Claude is mid-turn is left for next cycle."""
    cycle_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]
    ghost_cwd = repo_path / "removed-worktree"
    ghost_cwd.mkdir()

    with (
        patch.object(cycle_mod, "worktrees_basic", return_value=[wt]),
        patch.object(
            cycle_mod,
            "workspace_state",
            return_value=(
                {"workspace:99": "khivi/ghost"},
                {"workspace:99": ghost_cwd},
            ),
        ),
        patch.object(cycle_mod, "workspace_is_idle", return_value=False),
    ):
        cycle_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []
    out = capsys.readouterr().out
    assert "defer" in out
    assert "reap" in out
    assert "not idle" in out
    assert "workspace:99" in out


def test_reap_ignores_workspace_outside_registered_repos(reap_isolated, tmp_path):
    cycle_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    with (
        patch.object(cycle_mod, "worktrees_basic", return_value=[wt]),
        patch.object(
            cycle_mod,
            "workspace_state",
            return_value=(
                {"workspace:42": "research"},
                {"workspace:42": elsewhere},
            ),
        ),
    ):
        cycle_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []


def test_reap_dry_run_does_not_enqueue(reap_isolated, tmp_path):
    cycle_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]
    ghost_cwd = repo_path / "ghost"
    ghost_cwd.mkdir()

    with (
        patch.object(cycle_mod, "worktrees_basic", return_value=[wt]),
        patch.object(
            cycle_mod,
            "workspace_state",
            return_value=(
                {"workspace:99": "khivi/ghost"},
                {"workspace:99": ghost_cwd},
            ),
        ),
        patch.object(cycle_mod, "workspace_is_idle", return_value=True),
    ):
        cycle_mod._reap_workspace_orphans(repos, "khivi", dry=True)

    assert cr.iter_pending() == []


def test_reap_skips_workspace_matched_by_name(reap_isolated, tmp_path):
    """Even with a missing cwd, name-match to an existing wt.short keeps it alive."""
    cycle_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt_path = tmp_path / "feat-named"
    wt_path.mkdir()
    wt = _wt_stub(wt_path, "khivi/feat-named")

    repos = [{"path": str(repo_path), "name": "repo"}]

    with (
        patch.object(cycle_mod, "worktrees_basic", return_value=[wt]),
        patch.object(
            cycle_mod,
            "workspace_state",
            return_value=({"workspace:5": "feat-named"}, {}),
        ),
    ):
        cycle_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []


# ── _prepare_cycle: cmux unavailable should skip the repo ────────────────────


def test_prepare_cycle_skips_repo_on_cmux_unavailable(tmp_path, monkeypatch, capsys):
    from cockpit.lib.cmux import CmuxUnavailable

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo_entry = {"path": str(repo_path), "name": "repo"}

    monkeypatch.setattr(cycle, "repo_nwo", lambda _p: ("ai-needl", "repo"))
    monkeypatch.setattr(cycle, "worktrees", lambda _p, _prefix="": [])
    monkeypatch.setattr(cycle, "fetch_merged_branches", lambda *_a, **_k: {})
    monkeypatch.setattr(cycle, "is_cmux", lambda: True)

    def _boom() -> tuple[dict, dict]:
        raise CmuxUnavailable("backend offline")

    monkeypatch.setattr(cycle, "workspace_state", _boom)

    result = cycle._prepare_cycle(
        repo_entry,
        "khivi",
        cfg={},
        pr_cache={},
        pill_state={},
        dry=False,
    )

    assert result is None
    out = capsys.readouterr().out
    assert "skip" in out
    assert "cmux unavailable" in out
    assert "backend offline" in out


def test_prepare_cycle_prunes_worktrees_before_listing(tmp_path, monkeypatch):
    """Stale `.git/worktrees` entries are pruned before the list is read, so
    downstream teardown never sees a path that no longer exists."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo_entry = {"path": str(repo_path), "name": "repo"}

    calls: list[str] = []

    def _record_prune(_p):
        calls.append("prune")

    def _record_list(_p, _prefix=""):
        calls.append("list")
        return []

    monkeypatch.setattr(cycle, "repo_nwo", lambda _p: ("ai-needl", "repo"))
    monkeypatch.setattr(cycle, "prune_worktrees", _record_prune)
    monkeypatch.setattr(cycle, "worktrees", _record_list)
    monkeypatch.setattr(cycle, "workspace_state", lambda: ({}, {}))
    monkeypatch.setattr(cycle, "fetch_merged_branches", lambda *_a, **_k: {})
    monkeypatch.setattr(cycle, "is_cmux", lambda: True)

    def _stop(*_a, **_k):  # short-circuit after prune+list already ran
        raise RuntimeError("stop")

    monkeypatch.setattr(cycle, "list_relevant_prs", _stop)

    cycle._prepare_cycle(
        repo_entry,
        "khivi",
        cfg={},
        pr_cache={},
        pill_state={},
        dry=False,
    )

    assert calls[:2] == ["prune", "list"], f"prune must precede list; got {calls}"


def test_refresh_base_distance_short_circuits_when_no_feature_worktrees(tmp_path):
    from cockpit.lib.git import Worktree

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    primary = Worktree(path=repo_path, branch="main", is_primary=True)

    with (
        patch.object(cycle, "origin_head_branch") as ohb,
        patch.object(cycle.subprocess, "run") as run,
        patch.object(cycle, "write_base_distance") as wbd,
        patch.object(cycle, "write_base_ahead") as wba,
    ):
        distances = cycle._refresh_base_distance(repo_path, [primary], "main")

    assert distances == {}
    ohb.assert_not_called()
    run.assert_not_called()
    wbd.assert_not_called()
    wba.assert_not_called()


def test_refresh_base_distance_invalidates_on_fetch_nonzero(tmp_path, capsys):
    from cockpit.lib.git import Worktree

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = Worktree(path=tmp_path / "wt", branch="khivi/feat")

    with (
        patch.object(cycle, "origin_head_branch", return_value="main"),
        patch.object(
            cycle.subprocess,
            "run",
            return_value=type(
                "Res",
                (),
                {"returncode": 128, "stderr": "fatal: no such remote", "stdout": ""},
            )(),
        ),
        patch.object(cycle, "write_base_distance") as wbd,
        patch.object(cycle, "write_base_ahead") as wba,
    ):
        distances = cycle._refresh_base_distance(repo_path, [wt], "main")

    assert distances == {}
    wbd.assert_called_once_with("khivi/feat", -1)
    wba.assert_called_once_with("khivi/feat", -1)
    err = capsys.readouterr().err
    assert "skip" in err
    assert "exited 128" in err
    assert "no such remote" in err


# ── cycle_repo phase ordering ────────────────────────────────────────────────


def _stub_repo_cycle(tmp_path, *, headless: bool = False):
    return cycle.RepoCycle(
        cfg={},
        repo_path=tmp_path,
        owner="o",
        name="n",
        self_user="khivi",
        wts=[],
        prs=[],
        tracked={},
        names={},
        cwds={},
        merged_branches={},
        merged_branches_deep={},
        pill_state={},
        dry=False,
        headless=headless,
    )


def _cycle_patches(tmp_path, calls, *, headless=False):
    ctx = _stub_repo_cycle(tmp_path, headless=headless)
    return [
        patch.object(cycle, "_prepare_cycle", return_value=ctx),
        patch.object(cycle, "_write_pr_caches"),
        patch.object(
            cycle,
            "_dedupe_workspaces",
            side_effect=lambda *_a, **_kw: calls.append("dedupe") or set(),
        ),
        patch.object(
            cycle,
            "_refresh_tracked_pills",
            side_effect=lambda *_a, **_kw: (
                calls.append("refresh_pills") or (True, [], [])
            ),
        ),
        patch.object(
            cycle,
            "_handle_orphans_and_close_stale",
            side_effect=lambda *_a, **_kw: calls.append("handle_orphans"),
        ),
        patch.object(
            cycle,
            "_apply_repo_colors",
            side_effect=lambda *_a, **_kw: calls.append("apply_colors"),
        ),
        patch.object(
            cycle,
            "_spawn_missing_workspaces",
            side_effect=lambda *_a, **_kw: calls.append("spawn_missing"),
        ),
        patch.object(
            cycle,
            "_transition_merged_tickets",
            side_effect=lambda *_a, **_kw: calls.append("transition"),
        ),
        patch.object(
            cycle,
            "_maybe_autoclose",
            side_effect=lambda *_a, **_kw: calls.append("autoclose"),
        ),
        patch.object(cycle, "log_ff_advances"),
        patch.object(cycle, "ff_default_branch_worktrees", return_value=[]),
    ]


def _run_cycle_repo():
    cycle.cycle_repo(
        repo_entry={"name": "n", "path": "/tmp"},
        self_user="khivi",
        dry=False,
        pr_cache={},
        pill_state={},
        cfg={},
    )


def _enter_all(patches):
    from contextlib import ExitStack

    stack = ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


def test_cycle_repo_phase_order(tmp_path):
    calls: list[str] = []
    with _enter_all(_cycle_patches(tmp_path, calls)):
        _run_cycle_repo()
    assert calls == [
        "dedupe",
        "refresh_pills",
        "handle_orphans",
        "apply_colors",
        "spawn_missing",
        "transition",
        "autoclose",
    ]


def test_cycle_repo_headless_skips_workspace_phases(tmp_path):
    """When ctx.headless is True (cache_only backend), cycle_repo returns
    after writing PR caches — none of the 5 workspace phases run."""
    calls: list[str] = []
    with _enter_all(_cycle_patches(tmp_path, calls, headless=True)):
        _run_cycle_repo()
    assert calls == []


# ── _transition_merged_tickets: the opt-in Linear write at OPEN→MERGED ───────


def _transition_ctx(
    tmp_path,
    *,
    cfg=None,
    repo_entry=None,
    tickets=None,
    branch="khivi/feat",
    dry=False,
):
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir(exist_ok=True)
    wt = Worktree(path=wt_path, branch=branch, dirty_count=0, is_primary=False)
    return cycle.RepoCycle(
        cfg=cfg if cfg is not None else {"linear_done_on_merge": True},
        repo_path=tmp_path,
        owner="o",
        name="n",
        self_user="khivi",
        wts=[wt],
        prs=[],
        tracked={},
        names={},
        cwds={},
        merged_branches={branch: "deadbeef"},
        merged_branches_deep={},
        pill_state={},
        dry=dry,
        headless=False,
        repo_entry=repo_entry if repo_entry is not None else {"linear_keys": ["PE"]},
    )


def _transition_patches(
    *,
    viewer="u-me",
    meta=None,
    team_states=None,
    payload=None,
):
    """Patch the read-only linear leaf calls + is_ancestor (drives
    _is_post_merge_stale) and find_pr_payload. `update_ticket_state` is left for
    each test to patch so it can assert on the call. `payload` defaults to a
    one-ticket linear block."""
    if payload is None:
        payload = {"linear": {"tickets": [{"id": "PE-1", "state": "Dev Done"}]}}
    if meta is None:
        meta = {
            "id": "issue-uuid",
            "state": "Dev Done",
            "type": "completed",
            "assignee_id": "u-me",
            "team_id": "t-1",
        }
    if team_states is None:
        team_states = {"done": "s-done"}
    return [
        patch.object(cycle, "is_ancestor", return_value=True),
        patch.object(cycle, "find_pr_payload", return_value=payload),
        patch.object(cycle, "fetch_viewer_id", return_value=viewer),
        patch.object(cycle, "fetch_ticket_meta", return_value=meta),
        patch.object(cycle, "fetch_team_states", return_value=team_states),
    ]


def test_transition_happy_path_moves_ticket(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    ctx = _transition_ctx(tmp_path)
    with (
        _enter_all(_transition_patches()),
        patch.object(cycle, "update_ticket_state", return_value=True) as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    upd.assert_called_once_with("issue-uuid", "s-done")
    assert ctx.pill_state.get("merged-done:o/n:PE-1") is True


def test_transition_noop_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    ctx = _transition_ctx(tmp_path, cfg={"linear_done_on_merge": False})
    with (
        _enter_all(_transition_patches()),
        patch.object(cycle, "update_ticket_state") as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    upd.assert_not_called()


def test_transition_repo_override_enables(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    # Global off, per-repo on → fires.
    ctx = _transition_ctx(
        tmp_path,
        cfg={"linear_done_on_merge": False},
        repo_entry={"linear_keys": ["PE"], "linear_done_on_merge": True},
    )
    with (
        _enter_all(_transition_patches()),
        patch.object(cycle, "update_ticket_state", return_value=True) as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    upd.assert_called_once()


def test_transition_noop_when_dry(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    ctx = _transition_ctx(tmp_path, dry=True)
    with (
        _enter_all(_transition_patches()),
        patch.object(cycle, "update_ticket_state") as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    upd.assert_not_called()


def test_transition_noop_when_no_linear_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    ctx = _transition_ctx(tmp_path, repo_entry={"name": "n"})
    with (
        _enter_all(_transition_patches()),
        patch.object(cycle, "update_ticket_state") as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    upd.assert_not_called()


def test_transition_noop_when_no_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    ctx = _transition_ctx(tmp_path)
    with (
        _enter_all(_transition_patches()),
        patch.object(cycle, "update_ticket_state") as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    upd.assert_not_called()


def test_transition_skips_other_assignee(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    ctx = _transition_ctx(tmp_path)
    meta = {
        "id": "issue-uuid",
        "state": "Dev Done",
        "type": "completed",
        "assignee_id": "someone-else",
        "team_id": "t-1",
    }
    with (
        _enter_all(_transition_patches(meta=meta)),
        patch.object(cycle, "update_ticket_state") as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    upd.assert_not_called()
    # Still marked so it isn't re-queried every tick.
    assert ctx.pill_state.get("merged-done:o/n:PE-1") is True


def test_transition_skips_already_at_target(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    ctx = _transition_ctx(tmp_path)
    meta = {
        "id": "issue-uuid",
        "state": "Done",  # already terminal
        "type": "completed",
        "assignee_id": "u-me",
        "team_id": "t-1",
    }
    with (
        _enter_all(_transition_patches(meta=meta)),
        patch.object(cycle, "update_ticket_state") as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    upd.assert_not_called()


def test_transition_skips_canceled_ticket(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    ctx = _transition_ctx(tmp_path)
    meta = {
        "id": "issue-uuid",
        "state": "Closed",
        "type": "canceled",  # never resurrect
        "assignee_id": "u-me",
        "team_id": "t-1",
    }
    with (
        _enter_all(_transition_patches(meta=meta)),
        patch.object(cycle, "update_ticket_state") as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    upd.assert_not_called()


def test_transition_idempotent_marker_skips_refetch(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    ctx = _transition_ctx(tmp_path)
    ctx.pill_state["merged-done:o/n:PE-1"] = True
    with (
        _enter_all(_transition_patches()),
        patch.object(cycle, "fetch_ticket_meta") as meta_mock,
        patch.object(cycle, "update_ticket_state") as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    meta_mock.assert_not_called()
    upd.assert_not_called()


def test_transition_unknown_target_state_skips_and_warns(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    ctx = _transition_ctx(tmp_path)
    # Team has no state matching the target name.
    with (
        _enter_all(_transition_patches(team_states={"todo": "s-todo"})),
        patch.object(cycle, "update_ticket_state") as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    upd.assert_not_called()
    assert "not found" in capsys.readouterr().out


def test_transition_failed_mutation_clears_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    ctx = _transition_ctx(tmp_path)
    with (
        _enter_all(_transition_patches()),
        patch.object(cycle, "update_ticket_state", return_value=False),
    ):
        cycle._transition_merged_tickets(ctx)
    # Marker cleared so a later tick retries.
    assert "merged-done:o/n:PE-1" not in ctx.pill_state


def test_transition_skips_non_stale_worktree(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    ctx = _transition_ctx(tmp_path)
    patches = _transition_patches()
    # Override is_ancestor → not post-merge-stale → nothing happens.
    patches[0] = patch.object(cycle, "is_ancestor", return_value=False)
    with (
        _enter_all(patches),
        patch.object(cycle, "update_ticket_state") as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    upd.assert_not_called()


def test_transition_no_viewer_call_when_nothing_eligible(tmp_path, monkeypatch):
    """Viewer id is resolved lazily on the first real candidate, so a repo with
    no stale merged worktree never makes the viewer call."""
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    ctx = _transition_ctx(tmp_path)
    patches = _transition_patches()
    patches[0] = patch.object(cycle, "is_ancestor", return_value=False)  # not stale
    with (
        _enter_all(patches),
        patch.object(cycle, "fetch_viewer_id") as viewer,
        patch.object(cycle, "update_ticket_state") as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    viewer.assert_not_called()
    upd.assert_not_called()


def test_transition_skips_when_viewer_none(tmp_path, monkeypatch):
    """A set key but unresolvable viewer → move nothing, no marker (so a
    transient viewer failure retries next tick), no meta fetch."""
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    ctx = _transition_ctx(tmp_path)
    with (
        _enter_all(_transition_patches(viewer=None)),
        patch.object(cycle, "fetch_ticket_meta") as meta_mock,
        patch.object(cycle, "update_ticket_state") as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    meta_mock.assert_not_called()
    upd.assert_not_called()
    assert "merged-done:o/n:PE-1" not in ctx.pill_state


def test_transition_viewer_and_team_states_fetched_once(tmp_path, monkeypatch):
    """Across multiple eligible tickets in one tick, the viewer is resolved once
    (cached) and a shared team's states are fetched once."""
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    ctx = _transition_ctx(tmp_path)
    wt2_path = tmp_path / "repo-feat2"
    wt2_path.mkdir(exist_ok=True)
    ctx.wts.append(
        Worktree(path=wt2_path, branch="khivi/feat2", dirty_count=0, is_primary=False)
    )
    ctx.merged_branches["khivi/feat2"] = "cafe"
    payloads = {
        "khivi/feat": {"linear": {"tickets": [{"id": "PE-1", "state": "Dev Done"}]}},
        "khivi/feat2": {"linear": {"tickets": [{"id": "PE-2", "state": "Dev Done"}]}},
    }
    metas = {
        "PE-1": {
            "id": "u1",
            "state": "Dev Done",
            "type": "completed",
            "assignee_id": "u-me",
            "team_id": "t-1",
        },
        "PE-2": {
            "id": "u2",
            "state": "Dev Done",
            "type": "completed",
            "assignee_id": "u-me",
            "team_id": "t-1",
        },
    }
    with (
        patch.object(cycle, "is_ancestor", return_value=True),
        patch.object(
            cycle, "find_pr_payload", side_effect=lambda b, n: payloads.get(b)
        ),
        patch.object(cycle, "fetch_viewer_id", return_value="u-me") as viewer,
        patch.object(cycle, "fetch_ticket_meta", side_effect=lambda t: metas.get(t)),
        patch.object(
            cycle, "fetch_team_states", return_value={"done": "s-done"}
        ) as teams,
        patch.object(cycle, "update_ticket_state", return_value=True) as upd,
    ):
        cycle._transition_merged_tickets(ctx)
    assert viewer.call_count == 1  # resolved once, cached for the second ticket
    assert teams.call_count == 1  # same team → one fetch
    assert upd.call_count == 2


# ── _cached_linear_identity / _cached_viewer_id — cross-tick identity cache ──


def test_cached_linear_identity_caches_within_ttl(tmp_path):
    ctx = _stub_repo_cycle(tmp_path)
    ctx.cfg = {"slow_poll_interval_seconds": 300}
    calls: list[int] = []

    def fetch():
        calls.append(1)
        return "v"

    with patch.object(cycle.time, "time", return_value=1000.0):
        assert cycle._cached_linear_identity(ctx, "k", fetch) == "v"
        assert cycle._cached_linear_identity(ctx, "k", fetch) == "v"
    assert len(calls) == 1  # second call served from the cache


def test_cached_linear_identity_refetches_when_stale(tmp_path):
    ctx = _stub_repo_cycle(tmp_path)
    ctx.cfg = {"slow_poll_interval_seconds": 300}  # identity TTL = 12 × 300 = 3600
    calls: list[int] = []

    def fetch():
        calls.append(1)
        return "v"

    with patch.object(cycle.time, "time", return_value=1000.0):
        cycle._cached_linear_identity(ctx, "k", fetch)
    with patch.object(cycle.time, "time", return_value=1000.0 + 3601):
        cycle._cached_linear_identity(ctx, "k", fetch)
    assert len(calls) == 2


def test_cached_linear_identity_does_not_cache_falsy(tmp_path):
    ctx = _stub_repo_cycle(tmp_path)
    calls: list[int] = []

    def fetch():
        calls.append(1)
        return None

    with patch.object(cycle.time, "time", return_value=1000.0):
        assert cycle._cached_linear_identity(ctx, "k", fetch) is None
        assert cycle._cached_linear_identity(ctx, "k", fetch) is None
    assert len(calls) == 2  # a falsy result is never cached → retried
    assert "k" not in ctx.pill_state


def test_cached_viewer_id_keyed_by_api_key_fingerprint(tmp_path, monkeypatch):
    ctx = _stub_repo_cycle(tmp_path)
    monkeypatch.setenv("LINEAR_API_KEY", "key-one")
    with (
        patch.object(cycle, "fetch_viewer_id", return_value="u-1") as fetch,
        patch.object(cycle.time, "time", return_value=1000.0),
    ):
        assert cycle._cached_viewer_id(ctx) == "u-1"
        assert cycle._cached_viewer_id(ctx) == "u-1"  # cache hit, same key
        # Rotate the key → different fingerprint → cache miss → refetch.
        monkeypatch.setenv("LINEAR_API_KEY", "key-two")
        fetch.return_value = "u-2"
        assert cycle._cached_viewer_id(ctx) == "u-2"
    assert fetch.call_count == 2
    assert not any("key-one" in k or "key-two" in k for k in ctx.pill_state)


# ── _spawn_missing_workspaces background creation + _bg_spawn_pr ─────────────


def _pr_n(number: int, branch: str, *, author: str = "khivi") -> PR:
    return PR(
        number=number,
        title="t",
        branch=branch,
        url="",
        author=author,
        is_draft=False,
        review_decision="APPROVED",
        mergeable="MERGEABLE",
        ci="passed",
        unaddressed=0,
        total_from_others=0,
        state="OPEN",
    )


def _spawn_ctx(
    tmp_path,
    *,
    prs=None,
    wts=None,
    tracked=None,
    review_candidates=None,
    pill_state=None,
    names=None,
    cwds=None,
    dry=False,
):
    return cycle.RepoCycle(
        cfg={},
        repo_path=tmp_path,
        owner="o",
        name="n",
        self_user="khivi",
        wts=wts or [],
        prs=prs or [],
        tracked=tracked or {},
        names=names or {},
        cwds=cwds or {},
        merged_branches={},
        merged_branches_deep={},
        pill_state={} if pill_state is None else pill_state,
        dry=dry,
        headless=False,
        review_candidates=review_candidates or [],
    )


def test_spawn_missing_bg_spawns_my_pr_without_worktree(tmp_path):
    """My open PR with no worktree → background create (not a WARN)."""
    ctx = _spawn_ctx(tmp_path, prs=[_pr_n(7, "khivi/feat")], wts=[])
    with (
        patch.object(cycle, "_bg_spawn_pr") as bg,
        patch.object(cycle, "spawn_pr_workspace") as sp,
        patch.object(cycle, "spawn_orphan_workspace"),
    ):
        cycle._spawn_missing_workspaces(ctx, {"name": "n"})
    bg.assert_called_once_with(ctx, "n", 7, "khivi/feat", review=False)
    sp.assert_not_called()


def test_spawn_missing_orphan_skips_name_clash_different_path(tmp_path, capsys):
    """A PR-less orphan whose branch label is already used by a workspace rooted
    at a different, existing path is a cross-repo clash → skip + log, never
    spawn a duplicate-named workspace that would churn every cycle."""
    orphan_wt = tmp_path / "fonx-groups"
    orphan_wt.mkdir()
    other_repo_ws = tmp_path / "other" / "fonx-groups"
    other_repo_ws.mkdir(parents=True)
    ctx = _spawn_ctx(
        tmp_path,
        wts=[
            Worktree(path=orphan_wt, branch="khivi/fonx-groups", branch_prefix="khivi/")
        ],
        names={"workspace:1": "fonx-groups"},
        cwds={"workspace:1": other_repo_ws},
    )
    with (
        patch.object(cycle, "_bg_spawn_pr"),
        patch.object(cycle, "spawn_pr_workspace"),
        patch.object(cycle, "spawn_orphan_workspace") as orphan,
    ):
        cycle._spawn_missing_workspaces(ctx, {"name": "n"})
    orphan.assert_not_called()
    out = capsys.readouterr().out
    assert "orphan-spawn fonx-groups — workspace name already used by" in out
    assert str(other_repo_ws) in out


def test_spawn_missing_orphan_spawns_when_clash_cwd_missing(tmp_path):
    """A same-named workspace whose cwd no longer exists must NOT suppress the
    orphan spawn — that dead workspace is reaped by close_gone_cwd_workspaces,
    so deferring to it would strand the orphan forever."""
    orphan_wt = tmp_path / "fonx-groups"
    orphan_wt.mkdir()
    dead_ws = tmp_path / "gone" / "fonx-groups"  # never created on disk
    ctx = _spawn_ctx(
        tmp_path,
        wts=[Worktree(path=orphan_wt, branch="khivi/fonx-groups")],
        names={"workspace:1": "fonx-groups"},
        cwds={"workspace:1": dead_ws},
    )
    with (
        patch.object(cycle, "_bg_spawn_pr"),
        patch.object(cycle, "spawn_pr_workspace"),
        patch.object(cycle, "spawn_orphan_workspace") as orphan,
    ):
        cycle._spawn_missing_workspaces(ctx, {"name": "n"})
    orphan.assert_called_once()
    assert orphan.call_args.args[0] is ctx.wts[0]


def test_spawn_missing_review_candidates_filtered(tmp_path):
    """review_prs: spawn a review worktree for each other-authored open PR
    without a worktree; skip mine and skip ones already checked out."""
    from cockpit.lib.gh import OpenPRHead

    ctx = _spawn_ctx(
        tmp_path,
        prs=[],
        wts=[Worktree(path=tmp_path / "wt", branch="coworker/has-wt")],
        review_candidates=[
            OpenPRHead(20, "coworker/new", "coworker"),  # → review spawn
            OpenPRHead(21, "khivi/mine", "khivi"),  # skip: mine
            OpenPRHead(22, "coworker/has-wt", "coworker"),  # skip: worktree exists
        ],
    )
    with (
        patch.object(cycle, "_bg_spawn_pr") as bg,
        patch.object(cycle, "spawn_pr_workspace"),
        patch.object(cycle, "spawn_orphan_workspace"),
    ):
        cycle._spawn_missing_workspaces(ctx, {"name": "n"})
    review_calls = [c for c in bg.call_args_list if c.kwargs.get("review")]
    assert len(review_calls) == 1
    assert review_calls[0].args == (ctx, "n", 20, "coworker/new")


def test_bg_spawn_pr_dry_run_does_not_launch(tmp_path, capsys):
    ctx = _spawn_ctx(tmp_path, dry=True)
    with patch.object(cycle.subprocess, "Popen") as popen:
        cycle._bg_spawn_pr(ctx, "n", 9, "khivi/x", review=False)
    popen.assert_not_called()
    assert "bg-spawn #9" in capsys.readouterr().out
    assert "spawn:o/n:khivi/x" not in ctx.pill_state


def test_bg_spawn_pr_launches_records_and_guards(tmp_path, monkeypatch):
    monkeypatch.setattr(cycle, "_SPAWN_LOG", tmp_path / "spawn.log")
    ctx = _spawn_ctx(tmp_path)
    with (
        patch.object(cycle.subprocess, "Popen") as popen,
        patch.object(cycle.time, "monotonic", return_value=100.0),
    ):
        cycle._bg_spawn_pr(ctx, "n", 9, "coworker/x", review=True)
        # Second call within the in-flight TTL is suppressed.
        cycle._bg_spawn_pr(ctx, "n", 9, "coworker/x", review=True)
    assert popen.call_count == 1
    argv = popen.call_args.args[0]
    assert "--auto" not in argv
    assert argv[1:4] == ["-m", "cockpit.cli", "new"]  # module dispatch, not spawn.py
    assert argv[4:] == ["--pr", "9", "--repo", "n", "--review"]
    assert ctx.pill_state["spawn:o/n:coworker/x"] == 100.0


def test_bg_spawn_pr_retries_after_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(cycle, "_SPAWN_LOG", tmp_path / "spawn.log")
    ctx = _spawn_ctx(tmp_path, pill_state={"spawn:o/n:khivi/x": 10.0})
    with (
        patch.object(cycle.subprocess, "Popen") as popen,
        patch.object(
            cycle.time,
            "monotonic",
            return_value=10.0 + cycle._SPAWN_INFLIGHT_TTL_SECONDS + 1,
        ),
    ):
        cycle._bg_spawn_pr(ctx, "n", 9, "khivi/x", review=False)
    popen.assert_called_once()


def test_bg_spawn_pr_omits_repo_flag_without_name(tmp_path, monkeypatch):
    """No config name → omit --repo and let the child discover by cwd."""
    monkeypatch.setattr(cycle, "_SPAWN_LOG", tmp_path / "spawn.log")
    ctx = _spawn_ctx(tmp_path)
    with (
        patch.object(cycle.subprocess, "Popen") as popen,
        patch.object(cycle.time, "monotonic", return_value=1.0),
    ):
        cycle._bg_spawn_pr(ctx, None, 9, "khivi/x", review=False)
    argv = popen.call_args.args[0]
    assert "--repo" not in argv
    assert "--auto" not in argv
    assert argv[1:4] == ["-m", "cockpit.cli", "new"]  # module dispatch, not spawn.py
    assert argv[4:] == ["--pr", "9"]


# ── _apply_repo_colors / _repo_owned_refs ───────────────────────────────────


def _color_ctx(
    tmp_path, *, wts, cwds, pill_state=None, dry=False, name="n", repo_path=None
):
    return cycle.RepoCycle(
        cfg={},
        repo_path=tmp_path if repo_path is None else repo_path,
        owner="o",
        name=name,
        self_user="khivi",
        wts=wts,
        prs=[],
        tracked={},
        names={},
        cwds=cwds,
        merged_branches={},
        merged_branches_deep={},
        pill_state={} if pill_state is None else pill_state,
        dry=dry,
        headless=False,
    )


def test_repo_owned_refs_scopes_to_repo(tmp_path):
    repo = tmp_path / "repo"
    wt = repo / "wt-feat"
    other = tmp_path / "other-repo"
    ctx = _color_ctx(
        tmp_path,
        repo_path=repo,
        wts=[Worktree(path=wt, branch="khivi/feat")],
        cwds={
            "workspace:1": repo,  # main worktree
            "workspace:2": wt / "subdir",  # under a feature worktree
            "workspace:3": other,  # different repo — excluded
            "workspace:4": repo,  # in repo but absent from keep_refs
        },
    )
    keep = {"workspace:1", "workspace:2", "workspace:3"}
    owned = cycle._repo_owned_refs(ctx, keep)
    assert set(owned) == {"workspace:1", "workspace:2"}


def test_apply_repo_colors_no_field_noops(tmp_path):
    ctx = _color_ctx(tmp_path, wts=[], cwds={"workspace:1": tmp_path})
    with patch.object(cycle, "set_workspace_color") as swc:
        cycle._apply_repo_colors(ctx, {"name": "n"}, {"workspace:1"})
    swc.assert_not_called()


def test_apply_repo_colors_dry_noops(tmp_path):
    ctx = _color_ctx(tmp_path, wts=[], cwds={"workspace:1": tmp_path}, dry=True)
    with patch.object(cycle, "set_workspace_color") as swc:
        cycle._apply_repo_colors(
            ctx, {"name": "n", "sidebar_color": "Blue"}, {"workspace:1"}
        )
    swc.assert_not_called()


def test_repo_name_color_falls_back_to_bold_when_unset():
    from cockpit.lib.colors import bold

    assert cycle._repo_name_color({"name": "n"}) is bold
    assert cycle._repo_name_color({"name": "n", "sidebar_color": None}) is bold


def test_repo_name_color_uses_configured_sidebar_color():
    from cockpit.lib.colors import CMUX_COLOR_ANSI

    assert (
        cycle._repo_name_color({"name": "n", "sidebar_color": "Teal"})
        is CMUX_COLOR_ANSI["Teal"]
    )


def test_apply_repo_colors_tints_owned_refs_and_records(tmp_path):
    repo = tmp_path / "repo"
    pill_state: dict = {}
    ctx = _color_ctx(
        tmp_path,
        repo_path=repo,
        wts=[],
        cwds={"workspace:1": repo, "workspace:2": tmp_path / "elsewhere"},
        pill_state=pill_state,
    )
    with patch.object(cycle, "set_workspace_color") as swc:
        cycle._apply_repo_colors(
            ctx, {"name": "n", "sidebar_color": "Teal"}, {"workspace:1", "workspace:2"}
        )
    # only workspace:1 sits under repo_path; workspace:2 is outside it
    swc.assert_called_once_with("workspace:1", "Teal")
    assert pill_state["color:workspace:1"] == "Teal"


def test_apply_repo_colors_dedupes_unchanged(tmp_path):
    pill_state = {"color:workspace:1": "Teal"}
    ctx = _color_ctx(
        tmp_path, wts=[], cwds={"workspace:1": tmp_path}, pill_state=pill_state
    )
    with patch.object(cycle, "set_workspace_color") as swc:
        cycle._apply_repo_colors(
            ctx, {"name": "n", "sidebar_color": "Teal"}, {"workspace:1"}
        )
    swc.assert_not_called()


def test_apply_repo_colors_reapplies_on_change(tmp_path):
    pill_state = {"color:workspace:1": "Blue"}
    ctx = _color_ctx(
        tmp_path, wts=[], cwds={"workspace:1": tmp_path}, pill_state=pill_state
    )
    with patch.object(cycle, "set_workspace_color") as swc:
        cycle._apply_repo_colors(
            ctx, {"name": "n", "sidebar_color": "Teal"}, {"workspace:1"}
        )
    swc.assert_called_once_with("workspace:1", "Teal")
    assert pill_state["color:workspace:1"] == "Teal"


# ── _drain_close_requests composition: real queue + mocked teardown ──────────


@pytest.fixture
def drain_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    import cockpit.lib.config as cfg

    importlib.reload(cfg)
    import cockpit.lib.daemon_signal as ds

    importlib.reload(ds)
    importlib.reload(cycle)
    return cycle, ds


def _enqueue_marker(ds_mod, repo_name="repo", ref="workspace:1"):
    from cockpit.orchestrators.teardown import TeardownRequest

    return ds_mod.enqueue(
        TeardownRequest(
            ref=ref,
            name="feat",
            worktree_path=None,
            branch="khivi/feat",
            repo_path=None,
            repo_name=repo_name,
            forced=False,
        )
    )


def test_drain_refused_marker_is_popped_not_requeued(drain_isolated):
    """Teardown refuses (blockers reappeared); marker must still be popped to
    prevent the daemon refusing the same blocker every cycle forever.

    Also asserts prune_stale runs BEFORE iter_pending — stale markers must
    not survive a drain cycle just because the live queue is non-empty.
    """
    cycle_mod, ds = drain_isolated
    marker = _enqueue_marker(ds)
    order: list[str] = []
    real_iter_pending = ds.iter_pending

    def _prune(*_a, **_kw):
        order.append("prune")
        return []

    def _iter(*_a, **_kw):
        order.append("iter")
        return real_iter_pending(*_a, **_kw)

    with (
        patch.object(
            cycle_mod.daemon_signal,
            "prune_stale",
            side_effect=_prune,
        ),
        patch.object(
            cycle_mod.daemon_signal,
            "iter_pending",
            side_effect=_iter,
        ),
        patch.object(
            cycle_mod,
            "teardown",
            return_value=(False, ["dirty: 3 uncommitted file(s)"]),
        ),
    ):
        cycle_mod._drain_close_requests(dry=False)

    assert not marker.exists(), "refused marker must be popped, not requeued"
    assert ds.iter_pending() == []
    assert order == ["prune", "iter"], f"prune must precede iter, got {order}"


def test_drain_dry_run_leaves_refused_markers_in_queue(drain_isolated):
    """Dry-run is read-only: refused markers stay so the user can inspect them."""
    cycle_mod, ds = drain_isolated
    marker = _enqueue_marker(ds)

    with patch.object(
        cycle_mod,
        "teardown",
        return_value=(False, ["dirty: 3 uncommitted file(s)"]),
    ):
        cycle_mod._drain_close_requests(dry=True)

    assert marker.exists(), "dry-run must not delete refused markers"
    assert len(ds.iter_pending()) == 1


def test_drain_successful_teardown_pops_marker(drain_isolated):
    """Sanity: success also pops, so the dry-run inverse really tests dry."""
    cycle_mod, ds = drain_isolated
    marker = _enqueue_marker(ds)

    with patch.object(cycle_mod, "teardown", return_value=(True, [])):
        cycle_mod._drain_close_requests(dry=False)

    assert not marker.exists()
    assert ds.iter_pending() == []


def _stale_pr(*, ci: str = "failed") -> PR:
    """An OPEN PR whose display_issue is the actionable `ci` category."""
    return PR(
        number=1,
        title="t",
        branch="khivi/feat",
        url="",
        author="khivi",
        is_draft=False,
        review_decision="",
        mergeable="MERGEABLE",
        ci=ci,
        unaddressed=0,
        total_from_others=0,
        state="OPEN",
    )


def test_refresh_orphan_renames_drifted_workspace(tmp_path):
    """An orphan workspace whose name drifted from its branch label is
    re-asserted to `wt.label` in the slow tick."""
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(
        path=wt_path, branch="khivi/feat", dirty_count=0, branch_prefix="khivi/"
    )
    ctx = _stub_repo_cycle(tmp_path)
    ctx.base_distance = {}

    with (
        patch.object(cycle, "cmux"),
        patch.object(cycle, "apply_wip_pill"),
        patch.object(cycle, "apply_stale_pill"),
        patch.object(cycle, "maybe_nudge"),
        patch.object(cycle, "rename_workspace_if_needed", return_value=True) as rn,
    ):
        cycle._refresh_orphan(ctx, "workspace:7", wt, "stale-name")

    rn.assert_called_once_with("workspace:7", "feat", "stale-name", dry=False)


def test_handle_orphans_never_closes_and_gates_nudge(tmp_path):
    """No-PR worktrees are never closed here — only a merged PR reaps (via
    `_maybe_autoclose`). Mine-prefix branches are nudged; coworker branches get
    orphan pills only (nudge=False — nudging a coworker branch to open a PR is
    nonsense)."""
    mine_path = tmp_path / "repo-mine"
    mine_path.mkdir()
    cow_path = tmp_path / "repo-cow"
    cow_path.mkdir()
    mine = Worktree(
        path=mine_path, branch="khivi/feat", dirty_count=0, branch_prefix="khivi/"
    )
    cow = Worktree(path=cow_path, branch="coworker/feat", dirty_count=0)
    ctx = _stub_repo_cycle(tmp_path)
    ctx.wts = [mine, cow]
    ctx.names = {"ws:mine": "feat", "ws:cow": "cow-feat"}
    ctx.cwds = {"ws:mine": mine_path, "ws:cow": cow_path}

    with (
        patch.object(cycle, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(cycle, "_refresh_orphan") as refresh_mock,
    ):
        cycle._handle_orphans_and_close_stale(ctx, {"ws:mine", "ws:cow"})

    close_mock.assert_not_called()
    nudge_by_ref = {c.args[1]: c.kwargs["nudge"] for c in refresh_mock.call_args_list}
    assert nudge_by_ref == {"ws:mine": True, "ws:cow": False}


def test_refresh_orphan_skips_nudge_when_disabled(tmp_path):
    """`nudge=False` suppresses the push-or-close nudge but still applies pills."""
    wt_path = tmp_path / "repo-cow"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="coworker/feat", dirty_count=0)
    ctx = _stub_repo_cycle(tmp_path)
    ctx.base_distance = {}

    with (
        patch.object(cycle, "cmux"),
        patch.object(cycle, "apply_wip_pill"),
        patch.object(cycle, "apply_stale_pill"),
        patch.object(cycle, "rename_workspace_if_needed", return_value=False),
        patch.object(cycle, "maybe_nudge") as nudge_mock,
    ):
        cycle._refresh_orphan(ctx, "ws:cow", wt, "cow-feat", nudge=False)

    nudge_mock.assert_not_called()


def _orphan_grace_ctx(tmp_path, grace_hours):
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(
        path=wt_path, branch="khivi/feat", dirty_count=0, branch_prefix="khivi/"
    )
    ctx = _stub_repo_cycle(tmp_path)
    ctx.cfg = {"orphan_nudge_grace_hours": grace_hours}
    ctx.base_distance = {}
    return ctx, wt


def test_refresh_orphan_grace_suppresses_nudge_for_fresh_worktree(tmp_path, capsys):
    """A mine-prefix orphan younger than the grace window gets pills but no nudge."""
    ctx, wt = _orphan_grace_ctx(tmp_path, grace_hours=4)

    with (
        patch.object(cycle, "cmux"),
        patch.object(cycle, "apply_wip_pill"),
        patch.object(cycle, "apply_stale_pill"),
        patch.object(cycle, "rename_workspace_if_needed", return_value=False),
        patch.object(cycle, "worktree_age_seconds", return_value=3600),  # 1h < 4h
        patch.object(cycle, "maybe_nudge") as nudge_mock,
    ):
        cycle._refresh_orphan(ctx, "ws:mine", wt, "feat")

    nudge_mock.assert_not_called()
    assert "grace" in capsys.readouterr().out


def test_refresh_orphan_nudges_after_grace_elapses(tmp_path):
    """Once the worktree ages past the grace window the push-or-close nudge fires."""
    ctx, wt = _orphan_grace_ctx(tmp_path, grace_hours=4)

    with (
        patch.object(cycle, "cmux"),
        patch.object(cycle, "apply_wip_pill"),
        patch.object(cycle, "apply_stale_pill"),
        patch.object(cycle, "rename_workspace_if_needed", return_value=False),
        patch.object(cycle, "worktree_age_seconds", return_value=5 * 3600),  # 5h > 4h
        patch.object(cycle, "maybe_nudge") as nudge_mock,
    ):
        cycle._refresh_orphan(ctx, "ws:mine", wt, "feat")

    nudge_mock.assert_called_once()


def test_refresh_orphan_grace_zero_nudges_immediately(tmp_path):
    """grace=0 disables the window — even a brand-new worktree is nudged."""
    ctx, wt = _orphan_grace_ctx(tmp_path, grace_hours=0)

    with (
        patch.object(cycle, "cmux"),
        patch.object(cycle, "apply_wip_pill"),
        patch.object(cycle, "apply_stale_pill"),
        patch.object(cycle, "rename_workspace_if_needed", return_value=False),
        patch.object(cycle, "worktree_age_seconds", return_value=1),  # 1s, grace off
        patch.object(cycle, "maybe_nudge") as nudge_mock,
    ):
        cycle._refresh_orphan(ctx, "ws:mine", wt, "feat")

    nudge_mock.assert_called_once()


def test_refresh_tracked_pills_renames_drifted_workspace(tmp_path):
    """A tracked workspace whose name drifted from its branch label is
    re-asserted to `wt.label` in the slow tick."""
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(
        path=wt_path, branch="khivi/feat", dirty_count=0, branch_prefix="khivi/"
    )
    pr = _pr("khivi/feat", state="OPEN")
    ctx = _stub_repo_cycle(tmp_path)
    ctx.tracked = {"workspace:7": (pr, wt)}
    ctx.names = {"workspace:7": "stale-name"}

    with (
        patch.object(cycle, "apply_pills"),
        patch.object(cycle, "status_pills", return_value=()),
        patch.object(cycle, "maybe_nudge", return_value=False),
        patch.object(cycle, "_track_dev_done"),
        patch.object(cycle, "rename_workspace_if_needed", return_value=True) as rn,
    ):
        cycle._refresh_tracked_pills(ctx, {"workspace:7"})

    rn.assert_called_once_with("workspace:7", "feat", "stale-name", dry=False)


# ── devdone pill: Linear-delivery resolution + decision ──────────────────────


def _devdone_pr(body: str = "", *, branch: str = "khivi/pe-1") -> PR:
    return PR(
        number=7,
        title="t",
        branch=branch,
        url="",
        author="khivi",
        is_draft=False,
        review_decision="",
        mergeable="MERGEABLE",
        ci="passed",
        unaddressed=0,
        total_from_others=0,
        state="OPEN",
        body=body,
    )


def _devdone_ctx(tmp_path, *, linear_keys=("PE",), dry=False, cfg=None):
    ctx = _stub_repo_cycle(tmp_path)
    ctx.dry = dry
    ctx.repo_entry = {"linear_keys": list(linear_keys)} if linear_keys else {}
    ctx.cfg = cfg if cfg is not None else {}
    return ctx


FOOTER = "desc\n\n---\nLinear: [PE-1234](https://linear.app/x/PE-1234)"


def _prefetch_one(ctx, pr):
    """Drive `_prefetch_linear_blocks` for a single PR and return its block."""
    ctx.prs = [pr]
    cycle._prefetch_linear_blocks(ctx)
    return ctx.linear_blocks.get(pr.branch)


def test_prefetch_linear_blocks_none_when_not_configured(tmp_path):
    ctx = _devdone_ctx(tmp_path, linear_keys=None)
    pr = _devdone_pr(FOOTER)
    with patch.object(cycle, "fetch_ticket_states") as fetch:
        block = _prefetch_one(ctx, pr)
    assert block is None
    fetch.assert_not_called()


def test_prefetch_linear_blocks_fetches_when_no_prior(tmp_path):
    ctx = _devdone_ctx(tmp_path)
    with (
        patch.object(
            cycle, "fetch_ticket_states", return_value={"PE-1234": "Dev Done"}
        ) as fetch,
        patch.object(cycle.time, "time", return_value=1000.0),
    ):
        block = _prefetch_one(ctx, _devdone_pr(FOOTER))

    assert block == {
        "tickets": [{"id": "PE-1234", "state": "Dev Done"}],
        "fetched_at": 1000.0,
    }
    fetch.assert_called_once_with(["PE-1234"])


def test_prefetch_linear_blocks_carries_forward_when_unchanged_and_fresh(tmp_path):
    ctx = _devdone_ctx(tmp_path, cfg={"slow_poll_interval_seconds": 300})
    prior = {
        "tickets": [{"id": "PE-1234", "state": "Dev Done"}],
        "fetched_at": 900.0,
    }
    pr = _devdone_pr(FOOTER)
    ctx.pr_payloads = {pr.branch: {"linear": prior}}
    with (
        patch.object(cycle, "fetch_ticket_states") as fetch,
        patch.object(cycle.time, "time", return_value=1000.0),  # 100s < 900s TTL
    ):
        block = _prefetch_one(ctx, pr)

    assert block is prior
    fetch.assert_not_called()


def test_prefetch_linear_blocks_refetches_when_footer_changed(tmp_path):
    ctx = _devdone_ctx(tmp_path, cfg={"slow_poll_interval_seconds": 300})
    pr = _devdone_pr(FOOTER)
    ctx.pr_payloads = {
        pr.branch: {
            "linear": {
                "tickets": [{"id": "PE-9", "state": "Dev Done"}],
                "fetched_at": 990.0,
            }
        }
    }
    with (
        patch.object(
            cycle, "fetch_ticket_states", return_value={"PE-1234": "In Progress"}
        ) as fetch,
        patch.object(cycle.time, "time", return_value=1000.0),  # fresh, but ids differ
    ):
        block = _prefetch_one(ctx, pr)

    assert block is not None
    assert block["tickets"] == [{"id": "PE-1234", "state": "In Progress"}]
    fetch.assert_called_once_with(["PE-1234"])


def test_prefetch_linear_blocks_refetches_when_stale(tmp_path):
    ctx = _devdone_ctx(tmp_path, cfg={"slow_poll_interval_seconds": 300})  # TTL 900s
    pr = _devdone_pr(FOOTER)
    ctx.pr_payloads = {
        pr.branch: {
            "linear": {
                "tickets": [{"id": "PE-1234", "state": "Dev Done"}],
                "fetched_at": 0.0,
            }
        }
    }
    with (
        patch.object(
            cycle, "fetch_ticket_states", return_value={"PE-1234": "Dev Done"}
        ) as fetch,
        patch.object(cycle.time, "time", return_value=10_000.0),  # way past TTL
    ):
        _prefetch_one(ctx, pr)

    fetch.assert_called_once_with(["PE-1234"])


def test_prefetch_linear_blocks_batches_across_prs_one_call(tmp_path):
    """All due tickets across every PR collapse into a single batched fetch, and
    each PR's block is assembled from the shared result."""
    ctx = _devdone_ctx(tmp_path)
    pr_a = _devdone_pr("Linear: [PE-1](https://linear.app/x/PE-1)", branch="khivi/pe-a")
    pr_b = _devdone_pr(
        "Linear: [PE-2](https://linear.app/x/PE-2)\nLinear: [ENG-3](https://e/ENG-3)",
        branch="khivi/pe-b",
    )
    ctx.prs = [pr_a, pr_b]
    states = {"PE-1": "Dev Done", "PE-2": "In Progress", "ENG-3": "Dev Done"}
    with (
        patch.object(cycle, "fetch_ticket_states", return_value=states) as fetch,
        patch.object(cycle.time, "time", return_value=1000.0),
    ):
        cycle._prefetch_linear_blocks(ctx)

    fetch.assert_called_once_with(["ENG-3", "PE-1", "PE-2"])  # union, sorted
    assert ctx.linear_blocks["khivi/pe-a"]["tickets"] == [
        {"id": "PE-1", "state": "Dev Done"}
    ]
    assert ctx.linear_blocks["khivi/pe-b"]["tickets"] == [
        {"id": "PE-2", "state": "In Progress"},
        {"id": "ENG-3", "state": "Dev Done"},
    ]


def test_prefetch_linear_blocks_no_footers_no_fetch(tmp_path):
    """A Linear repo whose PRs deliver no tickets builds empty blocks without any
    network call."""
    ctx = _devdone_ctx(tmp_path)
    pr = _devdone_pr("no footer here")
    with (
        patch.object(cycle, "fetch_ticket_states") as fetch,
        patch.object(cycle.time, "time", return_value=1000.0),
    ):
        block = _prefetch_one(ctx, pr)

    assert block == {"tickets": [], "fetched_at": 1000.0}
    fetch.assert_not_called()


def test_track_dev_done_dry_run_noop(tmp_path):
    ctx = _devdone_ctx(tmp_path, dry=True)
    with patch.object(cycle, "apply_devdone_pill") as pill:
        cycle._track_dev_done(
            ctx, "workspace:1", {"tickets": [{"id": "PE-1", "state": "Dev Done"}]}
        )
    pill.assert_not_called()


def test_track_dev_done_not_configured_noop(tmp_path):
    ctx = _devdone_ctx(tmp_path, linear_keys=None)
    with patch.object(cycle, "apply_devdone_pill") as pill:
        cycle._track_dev_done(
            ctx, "workspace:1", {"tickets": [{"id": "PE-1", "state": "Dev Done"}]}
        )
    pill.assert_not_called()


def test_track_dev_done_none_block_clears(tmp_path):
    ctx = _devdone_ctx(tmp_path)
    with patch.object(cycle, "apply_devdone_pill") as pill:
        cycle._track_dev_done(ctx, "workspace:1", None)
    pill.assert_called_once_with("workspace:1", None)


def test_track_dev_done_no_tickets_clears(tmp_path):
    ctx = _devdone_ctx(tmp_path)
    with patch.object(cycle, "apply_devdone_pill") as pill:
        cycle._track_dev_done(ctx, "workspace:1", {"tickets": []})
    pill.assert_called_once_with("workspace:1", None)


def test_track_dev_done_single_ticket_shows_id(tmp_path):
    ctx = _devdone_ctx(tmp_path, cfg={"linear_dev_done_state": "Dev Done"})
    block = {"tickets": [{"id": "PE-1234", "state": "Dev Done"}]}
    with patch.object(cycle, "apply_devdone_pill") as pill:
        cycle._track_dev_done(ctx, "workspace:1", block)
    pill.assert_called_once_with("workspace:1", "PE-1234")


def test_track_dev_done_all_done_multiple_shows_count(tmp_path):
    ctx = _devdone_ctx(tmp_path)
    block = {
        "tickets": [
            {"id": "PE-1", "state": "Dev Done"},
            {"id": "PE-2", "state": "dev done"},  # case-insensitive
        ]
    }
    with patch.object(cycle, "apply_devdone_pill") as pill:
        cycle._track_dev_done(ctx, "workspace:1", block)
    pill.assert_called_once_with("workspace:1", "2/2")


def test_track_dev_done_partial_clears(tmp_path):
    ctx = _devdone_ctx(tmp_path)
    block = {
        "tickets": [
            {"id": "PE-1", "state": "Dev Done"},
            {"id": "PE-2", "state": "In Progress"},
        ]
    }
    with patch.object(cycle, "apply_devdone_pill") as pill:
        cycle._track_dev_done(ctx, "workspace:1", block)
    pill.assert_called_once_with("workspace:1", None)


def test_track_dev_done_custom_state_name(tmp_path):
    ctx = _devdone_ctx(tmp_path, cfg={"linear_dev_done_state": "In Review"})
    block = {"tickets": [{"id": "PE-1", "state": "In Review"}]}
    with patch.object(cycle, "apply_devdone_pill") as pill:
        cycle._track_dev_done(ctx, "workspace:1", block)
    pill.assert_called_once_with("workspace:1", "PE-1")


# ── merged/closed PRs are never actionable (no nudge loop) ───────────────────


def _tracked_ctx(tmp_path, pr, wt):
    ctx = _stub_repo_cycle(tmp_path, headless=False)
    ctx.prefs = {}
    ctx.tracked = {"workspace:1": (pr, wt)}
    ctx.names = {"workspace:1": "repo-feat"}
    ctx.pill_state = {}
    return ctx


def _refresh_with_mocks(ctx):
    with (
        patch.object(cycle, "status_pills", return_value=[]),
        patch.object(cycle, "apply_pills"),
        patch.object(cycle, "find_pr_payload", return_value=None),
        patch.object(cycle, "_track_dev_done"),
        patch.object(cycle, "maybe_nudge", return_value=True) as nudge_mock,
    ):
        cycle._refresh_tracked_pills(ctx, {"workspace:1"})
    return nudge_mock


def test_refresh_does_not_nudge_merged_pr_with_failing_ci(tmp_path):
    """A merged PR kept by _maybe_autoclose (merged with red CI) must not be
    nudged: its CI can never be fixed, so the nudge would loop forever."""
    wt = Worktree(path=tmp_path / "repo-feat", branch="khivi/feat", dirty_count=0)
    pr = _stale_pr(ci="failed:2")
    pr.state = "MERGED"
    nudge_mock = _refresh_with_mocks(_tracked_ctx(tmp_path, pr, wt))

    nudge_mock.assert_not_called()


def test_refresh_nudges_open_pr_with_failing_ci(tmp_path):
    """Companion: an OPEN PR with the same failing CI still nudges, keyed by its
    PR number for the mute lookup."""
    wt = Worktree(path=tmp_path / "repo-feat", branch="khivi/feat", dirty_count=0)
    nudge_mock = _refresh_with_mocks(
        _tracked_ctx(tmp_path, _stale_pr(ci="failed:2"), wt)
    )

    nudge_mock.assert_called_once()
    assert nudge_mock.call_args.kwargs["pr_number"] == 1


# ── reused-branch merged-PR suppression ──────────────────────────────────────
#
# A merged/closed PR whose branch has been reused for new local work (HEAD
# advanced past the PR's head_oid) must show no PR on the card. The signal is
# computed once in the slow tick (`_is_reused_branch_merge`), persisted as
# `reusedBranch` in the snapshot, and read back wherever the card is rendered.


def _reused_pr(
    branch: str = "khivi/feat", *, state: str = "MERGED", head_oid="deadbeef"
):
    pr = _pr(branch, state=state)
    pr.head_oid = head_oid
    return pr


def test_is_reused_branch_merge_head_still_reachable(tmp_path):
    """Case A: merged, HEAD == merge head (ancestor) → not reused, card stays."""
    wt = Worktree(path=tmp_path / "wt", branch="khivi/feat", dirty_count=0)
    with patch.object(cycle, "is_ancestor", return_value=True) as anc:
        assert cycle._is_reused_branch_merge(wt, _reused_pr()) is False
    anc.assert_called_once_with(wt.path, "deadbeef")


def test_is_reused_branch_merge_head_diverged(tmp_path):
    """Case B: merged, HEAD advanced past merge head → reused, suppress."""
    wt = Worktree(path=tmp_path / "wt", branch="khivi/feat", dirty_count=0)
    with patch.object(cycle, "is_ancestor", return_value=False):
        assert cycle._is_reused_branch_merge(wt, _reused_pr()) is True


def test_is_reused_branch_merge_missing_head_oid(tmp_path):
    """Case C: old cached PR with no head_oid → never suppressed (no regression);
    is_ancestor is not even consulted."""
    wt = Worktree(path=tmp_path / "wt", branch="khivi/feat", dirty_count=0)
    with patch.object(cycle, "is_ancestor", return_value=False) as anc:
        assert cycle._is_reused_branch_merge(wt, _reused_pr(head_oid=None)) is False
    anc.assert_not_called()


def test_is_reused_branch_merge_open_pr_unaffected(tmp_path):
    """Case D: an OPEN PR is never a reused-branch merge, regardless of HEAD."""
    wt = Worktree(path=tmp_path / "wt", branch="khivi/feat", dirty_count=0)
    with patch.object(cycle, "is_ancestor", return_value=False) as anc:
        assert cycle._is_reused_branch_merge(wt, _reused_pr(state="OPEN")) is False
    anc.assert_not_called()


def test_is_reused_branch_merge_closed_pr_diverged(tmp_path):
    """A CLOSED-not-merged PR whose branch diverged is also suppressed — CLOSED
    PRs never enter merged_branches, so head_oid is the only signal."""
    wt = Worktree(path=tmp_path / "wt", branch="khivi/feat", dirty_count=0)
    with patch.object(cycle, "is_ancestor", return_value=False):
        assert cycle._is_reused_branch_merge(wt, _reused_pr(state="CLOSED")) is True


def test_is_reused_branch_merge_no_worktree():
    """A PR with no local worktree can't be a reused branch."""
    assert cycle._is_reused_branch_merge(None, _reused_pr()) is False


def test_refresh_suppresses_reused_branch_card(tmp_path):
    """Winning payload reusedBranch=True → clear the card pills, show no PR, and
    never nudge."""
    wt = Worktree(path=tmp_path / "repo-feat", branch="khivi/feat", dirty_count=0)
    ctx = _tracked_ctx(tmp_path, _pr("khivi/feat", state="MERGED"), wt)
    ctx.pr_payloads = {"khivi/feat": {"reusedBranch": True}}
    with (
        patch.object(cycle, "clear_pr_pills") as clear_mock,
        patch.object(cycle, "apply_pills") as apply_mock,
        patch.object(cycle, "maybe_nudge", return_value=True) as nudge_mock,
    ):
        cycle._refresh_tracked_pills(ctx, {"workspace:1"})

    clear_mock.assert_called_once_with("workspace:1")
    apply_mock.assert_not_called()
    nudge_mock.assert_not_called()
    assert ctx.pill_state["workspace:1"] == frozenset()


def _write_caches_ctx(tmp_path, prs, wt):
    ctx = _stub_repo_cycle(tmp_path, headless=False)
    ctx.name = "n"
    ctx.prs = prs
    ctx.wts = [wt]
    ctx.prefs = {}
    return ctx


def test_write_pr_caches_clears_cells_for_reused_branch(tmp_path):
    wt = Worktree(path=tmp_path / "wt", branch="khivi/feat", dirty_count=0)
    ctx = _write_caches_ctx(tmp_path, [_reused_pr()], wt)
    with (
        patch.object(cycle, "_refresh_base_distance", return_value={}),
        patch.object(cycle, "load_pr_payloads_by_branch", return_value={}),
        patch.object(cycle, "_prefetch_linear_blocks"),
        patch.object(cycle, "write_git_state_cache"),
        patch.object(cycle, "is_ancestor", return_value=False),  # diverged → reused
        patch.object(cycle, "write_pr_cache") as wpc,
        patch.object(cycle, "write_branch_pr_cache") as wbpc,
        patch.object(cycle, "clear_branch_pr_cache") as cbpc,
        patch.object(cycle, "prune_superseded_pr_caches"),
    ):
        cycle._write_pr_caches(ctx)

    assert wpc.call_args.kwargs["reused_branch"] is True
    cbpc.assert_called_once_with("khivi/feat")
    wbpc.assert_not_called()


def test_write_pr_caches_writes_cells_when_not_reused(tmp_path):
    wt = Worktree(path=tmp_path / "wt", branch="khivi/feat", dirty_count=0)
    ctx = _write_caches_ctx(tmp_path, [_reused_pr()], wt)
    with (
        patch.object(cycle, "_refresh_base_distance", return_value={}),
        patch.object(cycle, "load_pr_payloads_by_branch", return_value={}),
        patch.object(cycle, "_prefetch_linear_blocks"),
        patch.object(cycle, "write_git_state_cache"),
        patch.object(cycle, "is_ancestor", return_value=True),  # reachable → not reused
        patch.object(cycle, "write_pr_cache") as wpc,
        patch.object(cycle, "write_branch_pr_cache") as wbpc,
        patch.object(cycle, "clear_branch_pr_cache") as cbpc,
        patch.object(cycle, "prune_superseded_pr_caches"),
    ):
        cycle._write_pr_caches(ctx)

    assert wpc.call_args.kwargs["reused_branch"] is False
    # Self-authored (PR.author == ctx.self_user="khivi") → no coworker login.
    assert wpc.call_args.kwargs["other_author"] == ""
    assert wbpc.call_args.kwargs["author"] == ""
    cbpc.assert_not_called()
    wbpc.assert_called_once()


def test_write_pr_caches_passes_coworker_author(tmp_path):
    """An other-authored PR (PR.author != self_user) → the author login is
    threaded into both cache writers; my own PRs pass an empty author."""
    wt = Worktree(path=tmp_path / "wt", branch="coworker/feat", dirty_count=0)
    pr = _reused_pr(branch="coworker/feat", state="OPEN")
    pr.author = "octocat"
    ctx = _write_caches_ctx(tmp_path, [pr], wt)  # ctx.self_user == "khivi"
    with (
        patch.object(cycle, "_refresh_base_distance", return_value={}),
        patch.object(cycle, "load_pr_payloads_by_branch", return_value={}),
        patch.object(cycle, "_prefetch_linear_blocks"),
        patch.object(cycle, "write_git_state_cache"),
        patch.object(cycle, "is_ancestor", return_value=True),
        patch.object(cycle, "write_pr_cache") as wpc,
        patch.object(cycle, "write_branch_pr_cache") as wbpc,
        patch.object(cycle, "clear_branch_pr_cache"),
        patch.object(cycle, "prune_superseded_pr_caches"),
    ):
        cycle._write_pr_caches(ctx)

    assert wpc.call_args.kwargs["other_author"] == "octocat"
    assert wbpc.call_args.kwargs["author"] == "octocat"


def test_write_pr_caches_keeps_cells_when_open_pr_shares_branch(tmp_path):
    """Reused merged PR + a live OPEN PR on the same branch: don't clear the
    cells — the open PR's own iteration writes them and rank resolves the card."""
    wt = Worktree(path=tmp_path / "wt", branch="khivi/feat", dirty_count=0)
    merged = _reused_pr(head_oid="old")
    merged.number = 86
    opened = _reused_pr(state="OPEN", head_oid="new")
    opened.number = 99
    ctx = _write_caches_ctx(tmp_path, [merged, opened], wt)
    with (
        patch.object(cycle, "_refresh_base_distance", return_value={}),
        patch.object(cycle, "load_pr_payloads_by_branch", return_value={}),
        patch.object(cycle, "_prefetch_linear_blocks"),
        patch.object(cycle, "write_git_state_cache"),
        patch.object(cycle, "is_ancestor", return_value=False),
        patch.object(cycle, "write_pr_cache"),
        patch.object(cycle, "write_branch_pr_cache") as wbpc,
        patch.object(cycle, "clear_branch_pr_cache") as cbpc,
        patch.object(cycle, "prune_superseded_pr_caches"),
    ):
        cycle._write_pr_caches(ctx)

    cbpc.assert_not_called()  # open PR shares branch → no clear
    wbpc.assert_called_once()  # only the open PR writes cells


# ────────────────────────────────────────────────────────────────────────────
# _reap_branch_refs / _branch_reap_reason: delete stale local branch refs with
# no worktree. Leaves (list_local_branches, has_remote_branch,
# branch_commits_ahead, delete_local_branch) are validated in tests/lib;
# here we mock them to assert gating and the merged-first decision order.
# ────────────────────────────────────────────────────────────────────────────


def _reap_ctx(
    tmp_path,
    *,
    merged_deep=None,
    wts=None,
    prs=None,
    dry=False,
    cfg=None,
):
    return cycle.RepoCycle(
        cfg=cfg or {},
        repo_path=tmp_path,
        owner="o",
        name="n",
        self_user="khivi",
        wts=wts or [],
        prs=prs or [],
        tracked={},
        names={},
        cwds={},
        merged_branches={},
        merged_branches_deep=merged_deep or {},
        pill_state={},
        dry=dry,
        headless=False,
    )


def _run_reap(ctx, *, local_branches, has_remote=False, ahead=0, delete_ok=True):
    """Enter the git-leaf patches _reap_branch_refs consults, run it, and return
    the delete_local_branch mock for assertion. `ahead` is the return of
    branch_commits_ahead for every call (merged-head or origin/default baseline).
    """
    patches = [
        patch.object(cycle, "origin_head_branch", return_value="main"),
        patch.object(cycle, "list_local_branches", return_value=local_branches),
        patch.object(cycle, "has_remote_branch", return_value=has_remote),
        patch.object(cycle, "branch_commits_ahead", return_value=ahead),
    ]
    with (
        _enter_all(patches),
        patch.object(
            cycle, "delete_local_branch", return_value=(delete_ok, "")
        ) as dele,
    ):
        cycle._reap_branch_refs(ctx)
    return dele


def test_reap_deletes_merged_branch_with_no_post_merge_commits(tmp_path):
    ctx = _reap_ctx(tmp_path, merged_deep={"khivi/done": "abc123"})
    dele = _run_reap(ctx, local_branches=["khivi/done"], ahead=0)
    dele.assert_called_once_with(tmp_path, "khivi/done")


def test_reap_keeps_merged_branch_with_post_merge_commits(tmp_path):
    """Branch reset/recreated onto a fresh lineage (commits past the merge head)
    is kept — mirrors _maybe_autoclose's has_unique_commits guard."""
    ctx = _reap_ctx(tmp_path, merged_deep={"khivi/reused": "abc123"})
    dele = _run_reap(ctx, local_branches=["khivi/reused"], ahead=3)
    dele.assert_not_called()


def test_reap_deletes_no_remote_branch_contained_in_default(tmp_path):
    ctx = _reap_ctx(tmp_path)  # not in merged map
    dele = _run_reap(ctx, local_branches=["khivi/scratch"], has_remote=False, ahead=0)
    dele.assert_called_once_with(tmp_path, "khivi/scratch")


def test_reap_keeps_no_remote_branch_with_unique_commits(tmp_path):
    """The 'block' decision: a never-pushed branch with local-only commits is
    unrecoverable work — keep it."""
    ctx = _reap_ctx(tmp_path)
    dele = _run_reap(ctx, local_branches=["khivi/unpushed"], has_remote=False, ahead=2)
    dele.assert_not_called()


def test_reap_keeps_branch_with_remote_but_no_merged_pr(tmp_path):
    """Has a remote ref, not in the merged map (open elsewhere / pushed work) →
    keep."""
    ctx = _reap_ctx(tmp_path)
    dele = _run_reap(ctx, local_branches=["khivi/live"], has_remote=True)
    dele.assert_not_called()


def test_reap_skips_main_default_worktree_and_open_pr_branches(tmp_path):
    ctx = _reap_ctx(
        tmp_path,
        merged_deep={"main": "x", "khivi/has-wt": "x", "khivi/open": "x"},
        wts=[Worktree(path=tmp_path / "wt", branch="khivi/has-wt")],
        prs=[_pr("khivi/open", state="OPEN")],
    )
    dele = _run_reap(
        ctx, local_branches=["main", "khivi/has-wt", "khivi/open"], ahead=0
    )
    dele.assert_not_called()


def test_reap_dry_run_does_not_delete(tmp_path):
    ctx = _reap_ctx(tmp_path, merged_deep={"khivi/done": "abc123"}, dry=True)
    dele = _run_reap(ctx, local_branches=["khivi/done"], ahead=0)
    dele.assert_not_called()


# --- _check_plugin_update --------------------------------------------------


def test_check_plugin_update_logs_when_newer(capsys):
    pill_state: dict = {}
    with (
        patch.object(cycle.version, "running_version", return_value="0.27.74"),
        patch.object(cycle.version, "latest_version", return_value="0.27.80"),
    ):
        cycle._check_plugin_update({"check_update": True}, pill_state)
    out = capsys.readouterr().out
    assert "update available" in out
    assert "0.27.74 -> 0.27.80" in out
    assert pill_state["update-check:warned"] == "0.27.80"


def test_check_plugin_update_default_on_when_key_absent(capsys):
    with (
        patch.object(cycle.version, "running_version", return_value="0.27.74"),
        patch.object(cycle.version, "latest_version", return_value="0.27.80"),
    ):
        cycle._check_plugin_update({}, {})
    assert "update available" in capsys.readouterr().out


def test_check_plugin_update_skips_when_disabled(capsys):
    with patch.object(cycle.version, "latest_version") as latest:
        cycle._check_plugin_update({"check_update": False}, {})
    latest.assert_not_called()
    assert capsys.readouterr().out == ""


def test_check_plugin_update_silent_when_up_to_date(capsys):
    with (
        patch.object(cycle.version, "running_version", return_value="0.27.80"),
        patch.object(cycle.version, "latest_version", return_value="0.27.80"),
    ):
        cycle._check_plugin_update({"check_update": True}, {})
    assert capsys.readouterr().out == ""


def test_check_plugin_update_silent_on_fetch_failure(capsys):
    with (
        patch.object(cycle.version, "running_version", return_value="0.27.74"),
        patch.object(cycle.version, "latest_version", return_value=None),
    ):
        cycle._check_plugin_update({"check_update": True}, {})
    assert capsys.readouterr().out == ""


def test_check_plugin_update_ttl_throttles_second_call(capsys):
    pill_state: dict = {}
    with (
        patch.object(cycle.version, "running_version", return_value="0.27.74"),
        patch.object(cycle.version, "latest_version", return_value="0.27.80") as latest,
    ):
        cycle._check_plugin_update({"check_update": True}, pill_state)
        capsys.readouterr()  # drain the first notice
        cycle._check_plugin_update({"check_update": True}, pill_state)
    # Second call is inside the TTL window — no re-query, no re-log.
    assert latest.call_count == 1
    assert capsys.readouterr().out == ""


def test_check_plugin_update_warns_once_per_version(capsys):
    # A re-query (after the TTL) for the same newer version must not re-log.
    pill_state = {"update-check:warned": "0.27.80"}
    with (
        patch.object(cycle.version, "running_version", return_value="0.27.74"),
        patch.object(cycle.version, "latest_version", return_value="0.27.80"),
    ):
        cycle._check_plugin_update({"check_update": True}, pill_state)
    assert capsys.readouterr().out == ""


# --- cycle_all on_repo_done -------------------------------------------------


def _patch_cycle_all_collaborators():
    """Stub everything cycle_all touches except the per-repo loop, so a test can
    assert the on_repo_done callback fires once per repo without standing up
    `gh`/`git`/`cmux`."""
    return (
        patch.object(cycle, "ensure_state_dirs", lambda: None),
        patch.object(cycle, "_check_plugin_update", lambda *_a, **_k: None),
        patch.object(cycle, "_drain_close_requests", lambda *, dry: None),
        patch.object(cycle, "close_gone_cwd_workspaces", lambda *, dry: None),
        patch.object(cycle, "_reap_workspace_orphans", lambda *_a, **_k: None),
    )


def test_cycle_all_calls_on_repo_done_once_per_repo():
    cfg = {"repos": [{"name": "a", "path": "/a"}, {"name": "b", "path": "/b"}]}
    seen: list[str] = []
    patches = _patch_cycle_all_collaborators()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patch.object(
            cycle, "cycle_repo", lambda repo_entry, *_a, **_k: seen.append("repo")
        ),
    ):
        ticks: list[int] = []
        cycle.cycle_all(
            cfg,
            "khivi",
            dry=False,
            pr_cache={},
            pill_state={},
            on_repo_done=lambda: ticks.append(len(seen)),
        )
    # Fired after each repo, in order: once with 1 repo done, once with 2.
    assert ticks == [1, 2]


def test_cycle_all_on_repo_done_fires_even_when_a_repo_errors():
    cfg = {"repos": [{"name": "a", "path": "/a"}, {"name": "b", "path": "/b"}]}

    def _boom(repo_entry, *_a, **_k):
        if repo_entry["name"] == "a":
            raise RuntimeError("repo a blew up")

    calls: list[str] = []
    patches = _patch_cycle_all_collaborators()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patch.object(cycle, "cycle_repo", _boom),
    ):
        cycle.cycle_all(
            cfg,
            "khivi",
            dry=False,
            pr_cache={},
            pill_state={},
            on_repo_done=lambda: calls.append("x"),
        )
    # The error on repo a is caught; the callback still fires for both repos so
    # repo b surfaces and a's partial cells aren't stranded.
    assert calls == ["x", "x"]


def test_cycle_all_callback_error_does_not_abort_remaining_repos(capsys):
    cfg = {"repos": [{"name": "a", "path": "/a"}, {"name": "b", "path": "/b"}]}
    processed: list[str] = []
    patches = _patch_cycle_all_collaborators()

    def _publish():
        raise RuntimeError("render exploded")

    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patch.object(
            cycle,
            "cycle_repo",
            lambda repo_entry, *_a, **_k: processed.append(repo_entry["name"]),
        ),
    ):
        cycle.cycle_all(
            cfg,
            "khivi",
            dry=False,
            pr_cache={},
            pill_state={},
            on_repo_done=_publish,
        )
    # A failing callback is swallowed (logged to stderr) — every repo still runs.
    assert processed == ["a", "b"]
    assert "on_repo_done" in capsys.readouterr().err


# --- cycle_all only_repo scoping --------------------------------------------


def test_cycle_all_only_repo_reconciles_just_that_repo():
    # A TUI row keypress passes only_repo=<repo path> so the cycle reconciles
    # only that row's repo — skipping the `gh` round-trips for every other repo.
    cfg = {"repos": [{"name": "a", "path": "/a"}, {"name": "b", "path": "/b"}]}
    seen: list[str] = []
    drained: list[str] = []
    swept: list[str] = []
    with (
        patch.object(cycle, "ensure_state_dirs", lambda: None),
        patch.object(
            cycle,
            "_check_plugin_update",
            lambda *_a, **_k: swept.append("plugin-update"),
        ),
        patch.object(
            cycle, "_drain_close_requests", lambda *, dry: drained.append("drain")
        ),
        patch.object(
            cycle,
            "close_gone_cwd_workspaces",
            lambda *, dry: swept.append("gone-cwd"),
        ),
        patch.object(
            cycle, "_reap_workspace_orphans", lambda *_a, **_k: swept.append("reap")
        ),
        patch.object(
            cycle,
            "cycle_repo",
            lambda repo_entry, *_a, **_k: seen.append(repo_entry["name"]),
        ),
    ):
        cycle.cycle_all(
            cfg,
            "khivi",
            dry=False,
            pr_cache={},
            pill_state={},
            only_repo="/b",
        )
    # Only repo b reconciled.
    assert seen == ["b"]
    # The close queue is still drained — a `c`/`C` teardown lands there and the
    # keypress is waiting on it.
    assert drained == ["drain"]
    # The repo-spanning sweeps + plugin-update check are skipped for a scoped
    # run — the next full periodic tick handles that global housekeeping.
    assert swept == []


def test_cycle_all_only_repo_unknown_path_reconciles_nothing():
    cfg = {"repos": [{"name": "a", "path": "/a"}]}
    seen: list[str] = []
    with (
        patch.object(cycle, "ensure_state_dirs", lambda: None),
        patch.object(cycle, "_check_plugin_update", lambda *_a, **_k: None),
        patch.object(cycle, "_drain_close_requests", lambda *, dry: None),
        patch.object(cycle, "close_gone_cwd_workspaces", lambda *, dry: None),
        patch.object(cycle, "_reap_workspace_orphans", lambda *_a, **_k: None),
        patch.object(
            cycle,
            "cycle_repo",
            lambda repo_entry, *_a, **_k: seen.append(repo_entry["name"]),
        ),
    ):
        cycle.cycle_all(
            cfg,
            "khivi",
            dry=False,
            pr_cache={},
            pill_state={},
            only_repo="/nonexistent",
        )
    assert seen == []  # no repo matched the scoped path → nothing reconciled
