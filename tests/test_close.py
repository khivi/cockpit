"""Tests for the `cockpit close` CLI entry point (cockpit/close.py).

Resolution runs against a real `git worktree` on tmp_path (the leaf layer —
stubbing git would test the stub). The PR-state lookup, blocker probe, and the
enqueue/kick IPC are mocked at the module boundary so gating + routing are
asserted without a network round-trip or a live daemon.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import cockpit.close as close_mod
from tests.conftest import _GIT_ENV_LEAKS
from tests.fixtures import make_git_repo, setup_cockpit_config


@pytest.fixture
def repo_cfg(tmp_path, monkeypatch):
    """A real repo on branch `khivi/foo` (prefix `khivi/`) wired into config,
    with the backend-name lookups and IPC stubbed out so only resolution +
    gating hit real code."""
    # Strip ambient GIT_* vars so `make_git_repo`'s `git -C tmp` commands target
    # the tmpdir, not the host repo. `git push` exports GIT_DIR/GIT_INDEX_FILE
    # into the pre-push hook, which would otherwise corrupt the outer index.
    for var in _GIT_ENV_LEAKS:
        monkeypatch.delenv(var, raising=False)
    repo = make_git_repo(tmp_path, branch="khivi/foo")
    setup_cockpit_config(
        tmp_path,
        monkeypatch,
        {"repos": [{"path": str(repo), "branch_prefix": "khivi/", "name": "myrepo"}]},
    )
    # `cockpit.close` imported `load_config` before the reload — repoint it.
    import cockpit.lib.config as cfg

    monkeypatch.setattr(close_mod, "load_config", cfg.load_config)
    monkeypatch.setattr(close_mod, "_workspace_ref", lambda wt: "workspace:ws1")
    monkeypatch.setattr(close_mod, "_workspace_name", lambda ref: "foo")
    # The PR cache is keyed by the git nwo name, not the config label — pin it to
    # a value deliberately != the "myrepo" label so a regression back to the
    # label is caught. `repo_nwo` shells out to `gh` (unavailable in tests).
    monkeypatch.setattr(close_mod, "repo_nwo", lambda p: ("acme", "beta"))
    return repo


@pytest.fixture
def captured(monkeypatch):
    """Capture enqueue() requests and stub kick_running() to report a daemon up."""
    reqs: list = []
    monkeypatch.setattr(close_mod, "enqueue", lambda req: reqs.append(req))
    monkeypatch.setattr(close_mod, "kick_running", lambda *, quiet=False: True)
    return reqs


def _no_blockers(monkeypatch, *, state="", number=None):
    monkeypatch.setattr(close_mod, "resolve_pr_state", lambda *a, **k: (state, number))
    monkeypatch.setattr(close_mod, "worktree_state_blockers", lambda *a, **k: [])


def test_resolves_cwd_worktree_and_enqueues(repo_cfg, captured, monkeypatch):
    monkeypatch.setattr(close_mod.Path, "cwd", classmethod(lambda cls: repo_cfg))
    _no_blockers(monkeypatch)
    assert close_mod.main([]) == 0
    assert len(captured) == 1
    req = captured[0]
    assert req.branch == "khivi/foo"
    # repo_name is the git nwo, NOT the config "myrepo" label — teardown keys the
    # PR cache by it (find_pr_payload / delete_pr_caches_for_branch).
    assert req.repo_name == "beta"
    assert req.forced is False


def test_repo_name_falls_back_to_basename_when_gh_fails(
    repo_cfg, captured, monkeypatch
):
    # `repo_nwo` failure (off-GitHub repo / gh unavailable) → the path basename,
    # not a crash.
    def boom(p):
        raise RuntimeError("gh repo view failed")

    monkeypatch.setattr(close_mod, "repo_nwo", boom)
    _no_blockers(monkeypatch)
    assert close_mod.main(["khivi/foo"]) == 0
    assert captured[0].repo_name == repo_cfg.name


def test_resolves_by_branch_query(repo_cfg, captured, monkeypatch):
    _no_blockers(monkeypatch)
    assert close_mod.main(["khivi/foo"]) == 0
    assert captured[0].branch == "khivi/foo"


def test_resolves_by_label_query(repo_cfg, captured, monkeypatch):
    # branch_label strips the `khivi/` prefix → label "foo".
    _no_blockers(monkeypatch)
    assert close_mod.main(["foo"]) == 0
    assert captured[0].branch == "khivi/foo"


def test_unknown_query_errors(repo_cfg, captured, monkeypatch):
    _no_blockers(monkeypatch)
    assert close_mod.main(["nope-not-a-branch"]) == 1
    assert captured == []


def test_hard_blocker_refuses_even_with_force(repo_cfg, captured, monkeypatch, capsys):
    monkeypatch.setattr(close_mod, "resolve_pr_state", lambda *a, **k: ("OPEN", 7))
    monkeypatch.setattr(
        close_mod, "worktree_state_blockers", lambda *a, **k: ["2 uncommitted file(s)"]
    )
    assert close_mod.main(["khivi/foo", "--force"]) == 1
    assert captured == []
    err = capsys.readouterr().err
    assert "uncommitted" in err
    assert "--force does not override" in err


def test_open_pr_soft_blocks_without_force(repo_cfg, captured, monkeypatch, capsys):
    monkeypatch.setattr(close_mod, "resolve_pr_state", lambda *a, **k: ("OPEN", 7))
    monkeypatch.setattr(close_mod, "worktree_state_blockers", lambda *a, **k: [])
    assert close_mod.main(["khivi/foo"]) == 1
    assert captured == []
    assert "PR #7 is OPEN" in capsys.readouterr().err


def test_force_overrides_open_pr(repo_cfg, captured, monkeypatch):
    monkeypatch.setattr(close_mod, "resolve_pr_state", lambda *a, **k: ("OPEN", 7))
    monkeypatch.setattr(close_mod, "worktree_state_blockers", lambda *a, **k: [])
    assert close_mod.main(["khivi/foo", "--force"]) == 0
    assert captured[0].forced is True


def test_merged_pr_sets_delete_branch(repo_cfg, captured, monkeypatch):
    # MERGED → unlanded gate skipped (asserted via the passed pr_merged flag),
    # and delete_branch opts in.
    seen = {}

    def blockers(path, *, branch, is_mine, pr_merged, is_primary=False):
        seen["pr_merged"] = pr_merged
        return []

    monkeypatch.setattr(close_mod, "resolve_pr_state", lambda *a, **k: ("MERGED", 7))
    monkeypatch.setattr(close_mod, "worktree_state_blockers", blockers)
    assert close_mod.main(["khivi/foo"]) == 0
    assert seen["pr_merged"] is True
    assert captured[0].delete_branch is True


def test_dry_run_does_not_enqueue(repo_cfg, captured, monkeypatch, capsys):
    _no_blockers(monkeypatch)
    assert close_mod.main(["khivi/foo", "--dry-run"]) == 0
    assert captured == []
    assert "dry-run" in capsys.readouterr().out


def test_no_daemon_still_queues_and_returns_zero(repo_cfg, monkeypatch, capsys):
    reqs: list = []
    monkeypatch.setattr(close_mod, "enqueue", lambda req: reqs.append(req))
    monkeypatch.setattr(close_mod, "kick_running", lambda *, quiet=False: False)
    _no_blockers(monkeypatch)
    assert close_mod.main(["khivi/foo"]) == 0
    assert len(reqs) == 1
    assert "no daemon running" in capsys.readouterr().err


def test_marker_ref_falls_back_to_branch_when_no_workspace(
    repo_cfg, captured, monkeypatch
):
    monkeypatch.setattr(close_mod, "_workspace_ref", lambda wt: None)
    monkeypatch.setattr(close_mod, "_workspace_name", lambda ref: "")
    _no_blockers(monkeypatch)
    assert close_mod.main(["khivi/foo"]) == 0
    assert captured[0].ref == "khivi/foo"


# --- _resolve_target: a repo whose worktrees() call raises is skipped ------


def test_resolve_target_skips_repos_whose_worktrees_call_raises(tmp_path, monkeypatch):
    """A `worktrees()` blowup on one configured repo (OSError or RuntimeError)
    must not abort resolution — later repos still get a chance to match."""
    oserror_repo = tmp_path / "oserror-repo"
    runtimeerror_repo = tmp_path / "runtimeerror-repo"
    good_repo = tmp_path / "good-repo"
    for d in (oserror_repo, runtimeerror_repo, good_repo):
        d.mkdir()

    setup_cockpit_config(
        tmp_path,
        monkeypatch,
        {
            "repos": [
                {"path": str(oserror_repo), "name": "oserror-repo"},
                {"path": str(runtimeerror_repo), "name": "runtimeerror-repo"},
                {"path": str(good_repo), "name": "good-repo"},
            ]
        },
    )
    import cockpit.lib.config as cfg

    monkeypatch.setattr(close_mod, "load_config", cfg.load_config)

    good_wt = close_mod.Worktree(path=good_repo / "wt", branch="feature")

    def fake_worktrees(rp, prefix):
        if rp == oserror_repo:
            raise OSError("disk went away")
        if rp == runtimeerror_repo:
            raise RuntimeError("git worktree list failed")
        assert rp == good_repo
        return [good_wt]

    monkeypatch.setattr(close_mod, "worktrees", fake_worktrees)

    resolved = close_mod._resolve_target("feature")
    assert resolved is not None
    repo, wt = resolved
    assert repo["name"] == "good-repo"
    assert wt is good_wt


# --- _workspace_ref -------------------------------------------------------


def test_workspace_ref_returns_ref_matching_worktree_cwd(monkeypatch):
    wt = close_mod.Worktree(path=Path("/tmp/cockpit-test-wt-match"), branch="b")
    monkeypatch.setattr(
        close_mod,
        "workspace_cwds",
        lambda *, include_self=False: {
            "workspace:1": Path("/tmp/cockpit-test-other"),
            "workspace:2": wt.path,
        },
    )
    assert close_mod._workspace_ref(wt) == "workspace:2"


def test_workspace_ref_returns_none_when_no_workspace_matches(monkeypatch):
    wt = close_mod.Worktree(path=Path("/tmp/cockpit-test-wt-nomatch"), branch="b")
    monkeypatch.setattr(
        close_mod,
        "workspace_cwds",
        lambda *, include_self=False: {"workspace:1": Path("/tmp/cockpit-test-other")},
    )
    assert close_mod._workspace_ref(wt) is None


def test_workspace_ref_falls_back_to_none_on_cmux_unavailable(monkeypatch):
    wt = close_mod.Worktree(path=Path("/tmp/cockpit-test-wt-unavail"), branch="b")

    def boom(*, include_self=False):
        raise close_mod.CmuxUnavailable("backend hiccup")

    monkeypatch.setattr(close_mod, "workspace_cwds", boom)
    assert close_mod._workspace_ref(wt) is None


def test_workspace_ref_queries_with_include_self_true(monkeypatch):
    # `cockpit close` is typically run from inside the worktree it's tearing
    # down, so the workspace to close IS the caller's own — the default
    # self-exclusion (`include_self=False`) would drop it.
    calls: list[bool] = []

    def fake(*, include_self=False):
        calls.append(include_self)
        return {}

    monkeypatch.setattr(close_mod, "workspace_cwds", fake)
    close_mod._workspace_ref(close_mod.Worktree(path=Path("/tmp/whatever"), branch="b"))
    assert calls == [True]


# --- _workspace_name -------------------------------------------------------


def test_workspace_name_empty_when_ref_is_none():
    assert close_mod._workspace_name(None) == ""


def test_workspace_name_returns_looked_up_name(monkeypatch):
    monkeypatch.setattr(
        close_mod, "workspace_names", lambda: {"workspace:1": "my-session"}
    )
    assert close_mod._workspace_name("workspace:1") == "my-session"


def test_workspace_name_empty_on_cmux_unavailable(monkeypatch):
    def boom():
        raise close_mod.CmuxUnavailable("backend hiccup")

    monkeypatch.setattr(close_mod, "workspace_names", boom)
    assert close_mod._workspace_name("workspace:1") == ""
