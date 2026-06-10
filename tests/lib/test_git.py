"""Tests for lib.git.

Covers branch-existence helpers (pinning down the suffix-match bug in
_fetch_remote_branch where a bare branch name like `cship` used to match
`refs/heads/*/cship` on `ls-remote --heads`), ahead/behind helpers, and
remove_worktree double-force + lock-reason logging.
"""

from __future__ import annotations

import subprocess

import pytest

import cockpit.lib.git as gitlib
from cockpit.lib.git import (
    Worktree,
    _fetch_remote_branch,
    _has_local_branch,
    _has_remote_branch,
    ahead_of_base,
    behind_of_base,
    branch_commits_ahead,
    branch_exists,
    branch_label,
    create_worktree,
    delete_local_branch,
    has_remote_branch,
    is_ancestor,
    list_local_branches,
    prune_worktrees,
    require_git,
    slugify,
    worktree_age_seconds,
    worktrees,
    worktrees_basic,
)


def _committer_env():
    import os

    return {
        **os.environ,
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_AUTHOR_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
    }


# ── branch_label / Worktree.label ────────────────────────────────────────────


@pytest.mark.parametrize(
    "branch, prefix, expected",
    [
        # Prefix stripped, then leading ticket dropped → human description.
        ("khivi/pe-4608-understand-dag-builder", "khivi/", "understand-dag-builder"),
        # Bare PR/issue number prefix is stripped too.
        ("khivi/123-fix-login-bug", "khivi/", "fix-login-bug"),
        # Bare ticket with no description keeps its id (never collapses to "").
        ("khivi/pe-4516", "khivi/", "pe-4516"),
        # A leading base-branch segment (`master/`) is dropped — it marks the
        # base, not identity.
        ("khivi/master/fnox-age", "khivi/", "fnox-age"),
        ("khivi/main/some-thing", "khivi/", "some-thing"),
        # A branch literally named after a main branch is NOT blanked (no
        # trailing `/` → not a base segment).
        ("khivi/master", "khivi/", "master"),
        # `main` must not false-match a longer word.
        ("khivi/mainframe-port", "khivi/", "mainframe-port"),
        # No configured prefix → slugified branch, user prefix left on.
        ("feature/thing", "", "feature-thing"),
        # Detached worktree (no branch) → empty label.
        ("", "khivi/", ""),
        # Prefix absent from the branch → not stripped; the surviving leading
        # `other` token isn't a `letters-digits` ticket, so it stays too.
        ("other/pe-9-do-x", "khivi/", "other-pe-9-do-x"),
        # A leading word that merely contains a digit (no `letters-digits`
        # boundary) is not a ticket and is preserved.
        ("khivi/v2-refactor", "khivi/", "v2-refactor"),
    ],
)
def test_branch_label_transforms(branch, prefix, expected):
    assert branch_label(branch, prefix) == expected


def test_branch_label_truncates_after_ticket_strip(tmp_path):
    """The 30-char cap is applied AFTER stripping the ticket, so a long
    description is not pre-truncated by the ticket token's width."""
    branch = "khivi/pe-4608-" + "a" * 40
    assert branch_label(branch, "khivi/") == "a" * 30


def test_slugify_no_trailing_dash_after_truncation():
    """The 30-char cap can land mid-separator; the result must not end in '-'
    (the pre-truncation strip can't see the cut introduced by the cap)."""
    # 29 chars then a separator-producing space at index 30 → cap would leave "-".
    s = slugify("a" * 29 + " tail")
    assert len(s) <= 30
    assert not s.endswith("-")


def test_worktree_label_uses_stored_prefix(tmp_path):
    """`Worktree.label` strips the prefix threaded in at construction; `short`
    stays the dir basename."""
    wt = Worktree(
        path=tmp_path / "pe-4516",
        branch="khivi/pe-4608-understand-dag-builder",
        branch_prefix="khivi/",
    )
    assert wt.short == "pe-4516"
    assert wt.label == "understand-dag-builder"


def test_worktree_label_primary_main_branch(tmp_path):
    """A primary checkout on `master` (no prefix match) labels as the branch
    slug; callers exempt it from renaming, but the property itself is pure."""
    wt = Worktree(
        path=tmp_path / "needl-ai",
        branch="master",
        is_primary=True,
        branch_prefix="khivi/",
    )
    assert wt.label == "master"


def test_worktrees_basic_threads_branch_prefix(cockpit_repo):
    """`worktrees_basic` stamps the passed prefix onto each Worktree so `label`
    strips it."""
    wts = worktrees_basic(cockpit_repo.repo, "khivi/")
    assert all(wt.branch_prefix == "khivi/" for wt in wts)


def test_has_remote_branch_exact_match(cockpit_repo, push_branch):
    push_branch("khivi/cship")
    assert _has_remote_branch(cockpit_repo.repo, "khivi/cship") is True


def test_has_remote_branch_returns_false_for_unrelated_suffix(
    cockpit_repo, push_branch
):
    """`refs/heads/khivi/foo/cship` exists on origin; querying for bare
    `cship` must return False."""
    push_branch("khivi/foo/cship")
    assert _has_remote_branch(cockpit_repo.repo, "cship") is False


def test_has_remote_branch_false_when_missing(cockpit_repo):
    assert _has_remote_branch(cockpit_repo.repo, "no-such-branch") is False


def test_fetch_remote_branch_does_not_match_suffix(cockpit_repo, push_branch):
    """The original bug: ls-remote suffix-matched `*/cship` then `fetch
    origin cship:cship` blew up. With the fix, this returns False cleanly."""
    push_branch("khivi/foo/cship")
    assert _fetch_remote_branch(cockpit_repo.repo, "cship") is False


def test_fetch_remote_branch_real_match(cockpit_repo, push_branch):
    push_branch("khivi/cship")
    assert _fetch_remote_branch(cockpit_repo.repo, "khivi/cship") is True
    assert _has_local_branch(cockpit_repo.repo, "khivi/cship") is True


def test_branch_exists_local(cockpit_repo):
    subprocess.run(
        ["git", "-C", str(cockpit_repo.repo), "branch", "local-only", "main"],
        check=True,
    )
    assert branch_exists(cockpit_repo.repo, "local-only") is True


def test_branch_exists_remote(cockpit_repo, push_branch):
    push_branch("remote-only")
    assert branch_exists(cockpit_repo.repo, "remote-only") is True


def test_branch_exists_neither(cockpit_repo):
    assert branch_exists(cockpit_repo.repo, "nope") is False


def test_behind_of_base_counts_commits(cockpit_repo, push_branch):
    """Branch carved at seed; main advances by 2 commits on origin. Branch
    must report behind_of_base == 2 after fetching origin/main."""
    import os

    repo = cockpit_repo.repo
    env = {
        **os.environ,
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_AUTHOR_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
    }

    def _git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env)

    push_branch("khivi/stale")  # carved off seed before main advances
    (repo / "a").write_text("a")
    _git("add", "a")
    _git("commit", "-q", "-m", "a")
    (repo / "b").write_text("b")
    _git("add", "b")
    _git("commit", "-q", "-m", "b")
    _git("push", "-q", "origin", "main")
    _git("fetch", "-q", "origin", "khivi/stale:khivi/stale")
    _git("checkout", "-q", "khivi/stale")
    assert behind_of_base(repo, "main") == 2


def test_behind_of_base_zero_when_no_base(cockpit_repo):
    assert behind_of_base(cockpit_repo.repo, "") == 0


def test_behind_of_base_zero_when_base_unknown(cockpit_repo):
    assert behind_of_base(cockpit_repo.repo, "no-such-base") == 0


def test_ahead_of_base_counts_commits(cockpit_repo, push_branch):
    """Branch carves off main, then adds 2 commits — must report
    ahead_of_base == 2 against origin/main."""
    import os

    repo = cockpit_repo.repo
    env = {
        **os.environ,
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_AUTHOR_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
    }

    def _git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env)

    push_branch("khivi/feat")
    _git("checkout", "-q", "khivi/feat")
    (repo / "x").write_text("x")
    _git("add", "x")
    _git("commit", "-q", "-m", "x")
    (repo / "y").write_text("y")
    _git("add", "y")
    _git("commit", "-q", "-m", "y")
    assert ahead_of_base(repo, "main") == 2


def test_ahead_of_base_zero_when_no_base(cockpit_repo):
    assert ahead_of_base(cockpit_repo.repo, "") == 0


def test_ahead_of_base_zero_when_base_unknown(cockpit_repo):
    assert ahead_of_base(cockpit_repo.repo, "no-such-base") == 0


# ────────────────────────────────────────────────────────────────────────────
# Branch-ref reaper leaves: list_local_branches / has_remote_branch /
# branch_commits_ahead (operate on branch refs, no checked-out worktree).
# ────────────────────────────────────────────────────────────────────────────


def test_list_local_branches_lists_heads(cockpit_repo):
    repo = cockpit_repo.repo
    subprocess.run(
        ["git", "-C", str(repo), "branch", "khivi/one", "main"],
        check=True,
        env=_committer_env(),
    )
    subprocess.run(
        ["git", "-C", str(repo), "branch", "khivi/two", "main"],
        check=True,
        env=_committer_env(),
    )
    assert sorted(list_local_branches(repo)) == ["khivi/one", "khivi/two", "main"]


def test_list_local_branches_empty_on_non_repo(tmp_path, monkeypatch):
    # Strip leaked GIT_* env (a pre-push hook sets GIT_DIR) so git can't resolve
    # the enclosing repo and genuinely fails on the non-repo path → [].
    for var in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
    ):
        monkeypatch.delenv(var, raising=False)
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()
    assert list_local_branches(non_repo) == []


def test_has_remote_branch_public_matches_private(cockpit_repo, push_branch):
    push_branch("khivi/pushed")
    assert has_remote_branch(cockpit_repo.repo, "khivi/pushed") is True
    assert has_remote_branch(cockpit_repo.repo, "khivi/never-pushed") is False


def test_branch_commits_ahead_counts_unique_commits(cockpit_repo):
    """A branch carved off main with 2 extra commits reports 2 commits ahead of
    origin/main — computed against the branch ref, with no worktree checkout."""
    repo = cockpit_repo.repo

    def _git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args], check=True, env=_committer_env()
        )

    _git("branch", "khivi/work", "main")
    _git("checkout", "-q", "khivi/work")
    (repo / "x").write_text("x")
    _git("add", "x")
    _git("commit", "-q", "-m", "x")
    (repo / "y").write_text("y")
    _git("add", "y")
    _git("commit", "-q", "-m", "y")
    # Switch off the branch so the count is purely ref-based, not HEAD-based.
    _git("checkout", "-q", "main")
    assert branch_commits_ahead(repo, "origin/main", "khivi/work") == 2


def test_branch_commits_ahead_zero_when_contained(cockpit_repo):
    """A branch pointing at the same commit as its base has nothing ahead."""
    repo = cockpit_repo.repo
    subprocess.run(
        ["git", "-C", str(repo), "branch", "khivi/at-base", "main"],
        check=True,
        env=_committer_env(),
    )
    assert branch_commits_ahead(repo, "origin/main", "khivi/at-base") == 0


def test_branch_commits_ahead_minus_one_on_unknown_ref(cockpit_repo):
    """Unknown base SHA / bad ref → -1 so callers refuse to delete."""
    assert branch_commits_ahead(cockpit_repo.repo, "deadbeef", "main") == -1


# ── create_worktree: attach existing prefixed branch (option A) ────────────


def test_create_worktree_attaches_existing_prefixed_branch(cockpit_repo):
    """Reproduces the worktree-gone-but-branch-survives case: a forced
    teardown left `khivi/todo` on disk. `create_worktree("todo", …,
    branch_prefix="khivi/")` must attach to it rather than die on `-b`."""
    repo = cockpit_repo.repo
    subprocess.run(["git", "-C", str(repo), "branch", "khivi/todo", "main"], check=True)
    wt_path = repo.parent / "todo"

    branch = create_worktree(repo, "todo", wt_path, base="main", branch_prefix="khivi/")
    assert branch == "khivi/todo"
    assert wt_path.exists()
    head = subprocess.check_output(
        ["git", "-C", str(wt_path), "rev-parse", "--abbrev-ref", "HEAD"],
        text=True,
    ).strip()
    assert head == "khivi/todo"


def test_create_worktree_creates_fresh_when_no_prefixed_branch(cockpit_repo):
    """Sanity: when neither short nor prefixed exists, the new-branch path
    still fires and applies the prefix."""
    repo = cockpit_repo.repo
    wt_path = repo.parent / "freshfeat"

    branch = create_worktree(
        repo, "freshfeat", wt_path, base="main", branch_prefix="khivi/"
    )
    assert branch == "khivi/freshfeat"
    assert wt_path.exists()
    assert _has_local_branch(repo, "khivi/freshfeat") is True


# ── remove_worktree: double-force + lock-reason logging ────────────────────


def test_remove_worktree_force_removes_locked_worktree(cockpit_repo) -> None:
    """`force=True` passes `--force --force`, which is what lets git override
    its refusal to remove a locked worktree."""
    repo = cockpit_repo.repo
    wt = repo.parent / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "wtbr", str(wt), "main"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "lock", "--reason", "test", str(wt)],
        check=True,
    )

    ok, _ = gitlib.remove_worktree(repo, wt, force=True)

    assert ok is True
    assert not wt.exists()


def test_remove_worktree_no_force_removes_clean_worktree(cockpit_repo) -> None:
    repo = cockpit_repo.repo
    wt = repo.parent / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "wtbr", str(wt), "main"],
        check=True,
    )

    ok, _ = gitlib.remove_worktree(repo, wt, force=False)

    assert ok is True
    assert not wt.exists()


def test_remove_worktree_no_force_fails_on_locked_worktree(cockpit_repo) -> None:
    """Without `--force`, git refuses to remove a locked worktree — proves
    the force flag is what's doing the work in the test above."""
    repo = cockpit_repo.repo
    wt = repo.parent / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "wtbr", str(wt), "main"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "lock", "--reason", "test", str(wt)],
        check=True,
    )

    ok, _ = gitlib.remove_worktree(repo, wt, force=False)

    assert ok is False
    assert wt.exists()


def test_remove_worktree_force_logs_lock_reason(cockpit_repo, capsys) -> None:
    repo = cockpit_repo.repo
    wt = repo.parent / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "wtbr", str(wt), "main"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "lock",
            "--reason",
            "checkout in progress",
            str(wt),
        ],
        check=True,
    )

    gitlib.remove_worktree(repo, wt, force=True)

    captured = capsys.readouterr()
    assert "preempting checkout in progress" in captured.err


# ── worktrees(): is_primary tagging ────────────────────────────────────────


def test_worktrees_primary_is_repo_dir(cockpit_repo) -> None:
    """The worktree whose path equals the repo dir passed to `worktrees()`
    is the trunk; any sibling added later must not be flagged primary."""
    repo = cockpit_repo.repo
    sibling = repo.parent / "sibling"
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-b",
            "khivi/feat",
            str(sibling),
            "main",
        ],
        check=True,
    )

    wts = gitlib.worktrees(repo)
    by_branch = {w.branch: w for w in wts}
    assert by_branch["main"].is_primary is True
    assert by_branch["khivi/feat"].is_primary is False


def test_worktrees_bare_repo_sibling_on_main_is_not_primary(
    tmp_path, monkeypatch
) -> None:
    """Bare-repo case (the real-world Cockpit setup): the bare dir is the
    primary, and a sibling checkout on `main` reports is_primary=False.

    Stub `run()` because building a full bare repo with two worktrees on
    `main` requires the rest of the test scaffolding; the parser is what
    matters here."""
    bare = tmp_path / "Cockpit"
    sibling = tmp_path / "ex-feat"
    bare.mkdir()
    sibling.mkdir()
    (sibling / ".git").write_text(f"gitdir: {bare}/worktrees/ex-feat\n")
    porcelain = (
        f"worktree {bare}\nbare\n\n"
        f"worktree {sibling}\nHEAD aa10472\nbranch refs/heads/main\n"
    )
    monkeypatch.setattr(gitlib, "run", lambda *_a, **_kw: porcelain)
    # Stub the per-worktree stat callouts so they don't shell out.
    monkeypatch.setattr(gitlib, "count_dirty", lambda *_a, **_kw: 0)
    monkeypatch.setattr(gitlib, "_count_unpushed", lambda *_a, **_kw: 0)

    wts = gitlib.worktrees(bare)

    assert len(wts) == 1, "bare entry has no branch and is skipped"
    assert wts[0].branch == "main"
    assert wts[0].is_primary is False


def test_remove_worktree_force_no_lock_file_is_quiet(cockpit_repo, capsys) -> None:
    repo = cockpit_repo.repo
    wt = repo.parent / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "wtbr", str(wt), "main"],
        check=True,
    )

    ok, _ = gitlib.remove_worktree(repo, wt, force=True)

    assert ok is True
    assert not wt.exists()
    captured = capsys.readouterr()
    assert "preempting" not in captured.err


def test_worktrees_basic_skips_dirty_unpushed_stats(cockpit_repo) -> None:
    """`worktrees_basic` reports identity but never runs the per-worktree
    `count_dirty` / `_count_unpushed` forks — even a dirty worktree reads
    dirty_count==0, where `worktrees()` would report the real count."""
    repo = cockpit_repo.repo
    wt = repo.parent / "wt-dirty"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "feat", str(wt), "main"],
        check=True,
    )
    (wt / "scratch.txt").write_text("uncommitted\n")  # untracked → dirty

    basic = {w.short: w for w in worktrees_basic(repo)}
    full = {w.short: w for w in worktrees(repo)}

    assert basic["wt-dirty"].branch == "feat"
    assert basic["wt-dirty"].dirty_count == 0, "basic must not stat the worktree"
    assert full["wt-dirty"].dirty_count > 0, "full listing does stat it"


# ── is_ancestor: reachability gate for autoclose ───────────────────────────


def test_is_ancestor_true_for_head_itself(cockpit_repo) -> None:
    """A commit is reachable from itself — the merged-and-untouched case."""
    repo = cockpit_repo.repo
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    assert is_ancestor(repo, head) is True


def test_is_ancestor_true_for_older_commit(cockpit_repo) -> None:
    """The merge head stays reachable after the branch advances on top of it —
    the squash-merge + pull-main case that must still reap."""
    repo = cockpit_repo.repo
    old_head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    env = _committer_env()
    (repo / "more").write_text("more")
    subprocess.run(["git", "-C", str(repo), "add", "more"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "more"], check=True, env=env
    )
    assert is_ancestor(repo, old_head) is True


def test_is_ancestor_false_for_divergent_lineage(cockpit_repo) -> None:
    """The reused-branch nuke (#81 → todo): the old merge head lives on a
    lineage HEAD no longer descends from, so it is NOT an ancestor."""
    repo = cockpit_repo.repo
    env = _committer_env()
    # Commit on a side branch carved at seed, capture its SHA, then discard it
    # from HEAD's history by returning to main and advancing separately.
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True, env=env
    )
    (repo / "side").write_text("side")
    subprocess.run(["git", "-C", str(repo), "add", "side"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "side"], check=True, env=env
    )
    side_head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", "main"], check=True, env=env
    )
    assert is_ancestor(repo, side_head) is False


def test_is_ancestor_false_for_unknown_sha(cockpit_repo) -> None:
    """An unresolvable SHA (e.g. a coworker's merge head never fetched) returns
    False so it never triggers a teardown."""
    assert (
        is_ancestor(cockpit_repo.repo, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
        is False
    )


def test_require_git_exits_when_missing(monkeypatch, capsys):
    """A missing `git` binary surfaces a structured install hint and exit code 2,
    not a bare FileNotFoundError deep inside a daemon cycle.
    """

    def _raise_fnf(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("cockpit.lib.git.subprocess.run", _raise_fnf)
    with pytest.raises(SystemExit) as excinfo:
        require_git()
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "git" in err
    assert "https://git-scm.com" in err


def test_require_git_returns_when_present(monkeypatch):
    monkeypatch.setattr("cockpit.lib.git.subprocess.run", lambda *_a, **_kw: None)
    require_git()


# ── prune_worktrees: drop admin entries for deleted dirs ───────────────────


def _branch_names(repo) -> set[str]:
    return {wt.branch for wt in worktrees(repo)}


def test_prune_worktrees_removes_stale_entry(cockpit_repo) -> None:
    """A worktree dir deleted on disk is still listed until prune runs."""
    import shutil

    repo = cockpit_repo.repo
    wt = repo.parent / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "wtbr", str(wt), "main"],
        check=True,
    )
    shutil.rmtree(wt)  # delete out-of-band, leaving stale .git/worktrees entry

    assert "wtbr" in _branch_names(repo), "stale entry should linger pre-prune"
    prune_worktrees(repo)
    assert "wtbr" not in _branch_names(repo), "prune should drop the stale entry"


# ── delete_local_branch: -D force-delete ───────────────────────────────────


def _local_branches(repo) -> set[str]:
    res = subprocess.run(
        ["git", "-C", str(repo), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
    )
    return {ln.strip() for ln in res.stdout.splitlines() if ln.strip()}


def test_delete_local_branch_success(cockpit_repo) -> None:
    repo = cockpit_repo.repo
    subprocess.run(["git", "-C", str(repo), "branch", "feature"], check=True)
    assert "feature" in _local_branches(repo)

    ok, err = delete_local_branch(repo, "feature")

    assert ok is True
    assert err == ""
    assert "feature" not in _local_branches(repo)


def test_delete_local_branch_force_deletes_unmerged(cockpit_repo) -> None:
    """`-D` removes a branch with commits not on the default branch — the
    squash-merge case `-d` would refuse. Proven here with genuinely unmerged
    work, which is the strongest form of "not merged into HEAD"."""
    repo = cockpit_repo.repo
    env = _committer_env()
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", "feature"], check=True, env=env
    )
    (repo / "f.txt").write_text("work\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "unmerged work"], check=True, env=env
    )
    subprocess.run(["git", "-C", str(repo), "checkout", "main"], check=True, env=env)

    ok, _ = delete_local_branch(repo, "feature")

    assert ok is True
    assert "feature" not in _local_branches(repo)


def test_delete_local_branch_failure_on_missing(cockpit_repo) -> None:
    repo = cockpit_repo.repo

    ok, err = delete_local_branch(repo, "no-such-branch")

    assert ok is False
    assert err != ""


def test_prune_worktrees_keeps_live_worktree(cockpit_repo) -> None:
    """Prune only removes entries whose dir is gone — never a live worktree."""
    repo = cockpit_repo.repo
    wt = repo.parent / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "wtbr", str(wt), "main"],
        check=True,
    )

    prune_worktrees(repo)

    assert wt.is_dir()
    assert "wtbr" in _branch_names(repo)


def test_prune_worktrees_warns_not_raises_on_failure(
    tmp_path, capsys, _clean_git_env
) -> None:
    """A non-repo path makes git exit non-zero; prune warns, never raises.

    `_clean_git_env` strips ambient GIT_DIR (pre-commit exports it), so git
    targets the tmpdir and fails cleanly instead of finding the host repo.
    """
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()

    prune_worktrees(not_a_repo)  # must not raise

    assert "worktree prune failed" in capsys.readouterr().err


# ── worktree_age_seconds ────────────────────────────────────────────────────


def test_worktree_age_seconds_uses_now_relative_to_creation(tmp_path):
    """Age is `now - creation`; a `now` well past creation yields that delta."""
    wt = tmp_path / "fresh"
    wt.mkdir()
    created = wt.stat().st_birthtime if hasattr(wt.stat(), "st_birthtime") else None
    base = created if created is not None else wt.stat().st_ctime
    # 2 hours after creation.
    assert worktree_age_seconds(wt, now=base + 7200) == pytest.approx(7200, abs=1)


def test_worktree_age_seconds_never_negative(tmp_path):
    """A `now` before the creation stamp clamps to 0 rather than going negative."""
    wt = tmp_path / "fresh"
    wt.mkdir()
    assert worktree_age_seconds(wt, now=0) == 0.0


def test_worktree_age_seconds_missing_path_fails_open(tmp_path):
    """An un-stat-able path returns inf so the orphan nudge isn't silently muted."""
    assert worktree_age_seconds(tmp_path / "nope") == float("inf")
