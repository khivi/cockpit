"""Tests for scripts/orchestrators/cycle.py.

Two sections:
  - _maybe_autoclose: ordering + dry/error guards (delegates to orchestrators.teardown).
  - _reap_workspace_orphans: gating logic for orphan-workspace cleanup
    (ownership derived from cwd vs registered repos).
"""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

import scripts.orchestrators.cycle as cycle
from scripts.lib.gh import PR
from scripts.lib.git import Worktree
from scripts.orchestrators import teardown as teardown_mod


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
    ):
        cycle._maybe_autoclose(
            cfg={"auto_cleanup_on_merge": True},
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
    ):
        cycle._maybe_autoclose(
            cfg={"auto_cleanup_on_merge": True},
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
    ):
        cycle._maybe_autoclose(
            cfg={"auto_cleanup_on_merge": True},
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
# Authoritative merge signal is `gh pr list --state merged` — the commit graph
# is not consulted, so squash- and rebase-merges work uniformly.
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
    ):
        cycle._maybe_autoclose(
            cfg={"auto_cleanup_on_merge": True},
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
    ):
        cycle._maybe_autoclose(
            cfg={"auto_cleanup_on_merge": True},
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            prs=[],
            dry=False,
        )

    close_mock.assert_called_once()


def test_autoclose_does_not_consult_commit_graph(tmp_path):
    """Regression: squash-merge + pull-main case.

    Before the smart-skip refactor, autoclose used `count_commits_since(wt,
    merged_head)` to gate teardown. After a squash-merge, that SHA stays
    reachable from the worktree (the branch tip itself was preserved), but
    pulling main on top moves HEAD forward — `count_commits_since` would
    return > 0 and the worktree would never autoclose. The fix is to trust
    `gh pr list --state merged` and never call into the commit graph.

    This test asserts the implementation does not call `count_commits_since`.
    """
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort"),
        patch.object(teardown_mod, "remove_worktree", return_value=(True, "")),
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(teardown_mod, "ff_default_branch_worktrees", return_value=[]),
        patch.object(cycle, "count_commits_since") as count_mock,
    ):
        cycle._maybe_autoclose(
            cfg={"auto_cleanup_on_merge": True},
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            prs=[_pr("khivi/feat")],
            dry=False,
        )

    count_mock.assert_not_called()


def test_autoclose_skips_dirty_even_with_clean_pr(tmp_path):
    """Uncommitted local work still wins over a clean merged PR."""
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=3)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as remove_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
    ):
        cycle._maybe_autoclose(
            cfg={"auto_cleanup_on_merge": True},
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
            cfg={"auto_cleanup_on_merge": True},
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
    """Same setup as orphan_main_sibling_clean but with uncommitted work — skip."""
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
    ):
        cycle._maybe_autoclose(
            cfg={"auto_cleanup_on_merge": True},
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
            cfg={"auto_cleanup_on_merge": True},
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
            cfg={"auto_cleanup_on_merge": True},
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
            cfg={"auto_cleanup_on_merge": True},
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
    import scripts.lib.config as cfg

    importlib.reload(cfg)
    import scripts.lib.daemon_signal as cr

    importlib.reload(cr)
    import scripts.orchestrators.cycle as cycle_mod

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
        patch.object(cycle_mod, "worktrees", return_value=[wt]),
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
        patch.object(cycle_mod, "worktrees", return_value=[wt]),
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
        patch.object(cycle_mod, "worktrees", return_value=[wt]),
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
        patch.object(cycle_mod, "worktrees", return_value=[wt]),
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
        patch.object(cycle_mod, "worktrees", return_value=[wt]),
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
        patch.object(cycle_mod, "worktrees", return_value=[wt]),
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
    from scripts.lib.cmux import CmuxUnavailable

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo_entry = {"path": str(repo_path), "name": "repo"}

    monkeypatch.setattr(cycle, "repo_nwo", lambda _p: ("ai-needl", "repo"))
    monkeypatch.setattr(cycle, "worktrees", lambda _p: [])
    monkeypatch.setattr(cycle, "fetch_merged_branches", lambda _p: {})
    monkeypatch.setattr(cycle, "_resolve_tool", lambda: "cmux")

    def _boom() -> tuple[dict, dict]:
        raise CmuxUnavailable("backend offline")

    monkeypatch.setattr(cycle, "workspace_state", _boom)

    result = cycle._prepare_cycle(
        repo_entry,
        "khivi",
        cfg={},
        pr_cache={},
        pill_state={},
        nudge_state={},
        keep_stale=False,
        no_spawn=False,
        dry=False,
        verbose=False,
    )

    assert result is None
    out = capsys.readouterr().out
    assert "skip" in out
    assert "cmux unavailable" in out
    assert "backend offline" in out


def test_refresh_base_distance_short_circuits_when_no_feature_worktrees(tmp_path):
    from scripts.lib.git import Worktree

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    primary = Worktree(path=repo_path, branch="main", is_primary=True)

    with (
        patch.object(cycle, "origin_head_branch") as ohb,
        patch.object(cycle.subprocess, "run") as run,
        patch.object(cycle, "write_base_distance") as wbd,
        patch.object(cycle, "write_base_ahead") as wba,
    ):
        distances = cycle._refresh_base_distance(repo_path, [primary])

    assert distances == {}
    ohb.assert_not_called()
    run.assert_not_called()
    wbd.assert_not_called()
    wba.assert_not_called()


def test_refresh_base_distance_invalidates_on_fetch_nonzero(tmp_path, capsys):
    from scripts.lib.git import Worktree

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
        distances = cycle._refresh_base_distance(repo_path, [wt])

    assert distances == {}
    wbd.assert_called_once_with("khivi/feat", -1, 0)
    wba.assert_called_once_with("khivi/feat", -1, 0)
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
        pill_state={},
        nudge_state={},
        keep_stale=False,
        no_spawn=False,
        dry=False,
        verbose=False,
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
            side_effect=lambda *_a, **_kw: (calls.append("dedupe") or set()),
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
            "_spawn_missing_workspaces",
            side_effect=lambda *_a, **_kw: calls.append("spawn_missing"),
        ),
        patch.object(
            cycle,
            "_maybe_autoclose",
            side_effect=lambda *_a, **_kw: calls.append("autoclose"),
        ),
        patch.object(cycle, "log_ff_advances"),
        patch.object(cycle, "ff_default_branch_worktrees", return_value=[]),
    ]


def _run_cycle_repo(no_spawn=False):
    cycle.cycle_repo(
        repo_entry={"name": "n", "path": "/tmp"},
        self_user="khivi",
        keep_stale=False,
        no_spawn=no_spawn,
        dry=False,
        pr_cache={},
        nudge_state={},
        pill_state={},
        verbose=False,
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
        "spawn_missing",
        "autoclose",
    ]


def test_cycle_repo_headless_skips_workspace_phases(tmp_path):
    """When ctx.headless is True (cache_only backend), cycle_repo returns
    after writing PR caches — none of the 5 workspace phases run."""
    calls: list[str] = []
    with _enter_all(_cycle_patches(tmp_path, calls, headless=True)):
        _run_cycle_repo()
    assert calls == []


def test_cycle_repo_no_spawn_skips_spawn_phase(tmp_path):
    calls: list[str] = []
    with _enter_all(_cycle_patches(tmp_path, calls)):
        _run_cycle_repo(no_spawn=True)
    assert "spawn_missing" not in calls
    assert calls == ["dedupe", "refresh_pills", "handle_orphans", "autoclose"]


# ── _drain_close_requests composition: real queue + mocked teardown ──────────


@pytest.fixture
def drain_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    import scripts.lib.config as cfg

    importlib.reload(cfg)
    import scripts.lib.daemon_signal as ds

    importlib.reload(ds)
    importlib.reload(cycle)
    return cycle, ds


def _enqueue_marker(ds_mod, repo_name="repo", ref="workspace:1"):
    from scripts.orchestrators.teardown import TeardownRequest

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

    with (
        patch.object(
            cycle_mod.daemon_signal,
            "prune_stale",
            side_effect=lambda *_a, **_kw: order.append("prune") or [],
        ),
        patch.object(
            cycle_mod.daemon_signal,
            "iter_pending",
            side_effect=lambda *_a, **_kw: (
                order.append("iter") or real_iter_pending(*_a, **_kw)
            ),
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
