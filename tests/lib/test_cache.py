"""Tests for cockpit/lib/cache.py — cockpit-cache writers and refreshers."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import cockpit.lib.cache as cache_mod
from cockpit.lib.gh import PR
from cockpit.lib.git import Worktree
from cockpit.lib.nudges import NudgePref


def _pr(**overrides) -> PR:
    base: dict = dict(
        number=1,
        title="t",
        branch="khivi/feature",
        url="https://example/pr/1",
        author="khivi",
        is_draft=False,
        review_decision="REVIEW_REQUIRED",
        mergeable="MERGEABLE",
        ci="passed",
        unaddressed=0,
        total_from_others=0,
        state="OPEN",
        updated_at="",
    )
    base.update(overrides)
    return PR(**base)


def _wt(
    branch: str = "khivi/feature",
    *,
    rebasing: bool = False,
    merging: bool = False,
    dirty: int = 0,
) -> Worktree:
    return Worktree(
        path=Path("/tmp/wt"),
        branch=branch,
        rebasing=rebasing,
        merging=merging,
        dirty_count=dirty,
    )


# ── write_branch_pr_cache (daemon-tick path, lib.cache) ────────────────────


def test_write_branch_pr_cache_resolves_state(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="APPROVED",
        number=17,
        title="Hello",
        ci_glyph="✓",
    )
    assert (cache_dir / "pr-state-khivi-feature").read_text() == "APPROVED"
    assert (cache_dir / "pr-num-khivi-feature").read_text() == "17"
    assert (cache_dir / "pr-title-khivi-feature").read_text() == "Hello"
    assert (cache_dir / "pr-checks-khivi-feature").read_text() == "✓"


def test_write_branch_pr_cache_draft_overrides_open(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=True,
        review_decision="",
        number=18,
        title="Draft",
    )
    assert (cache_dir / "pr-state-khivi-feature").read_text() == "DRAFT"


def test_write_branch_pr_cache_closed_state_preserved(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="MERGED",
        is_draft=False,
        review_decision="APPROVED",
        number=19,
        title="Done",
    )
    assert (cache_dir / "pr-state-khivi-feature").read_text() == "MERGED"


def test_write_branch_pr_cache_writes_comments(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="CHANGES_REQUESTED",
        number=20,
        title="Review me",
        comments=3,
    )
    assert (cache_dir / "pr-comments-khivi-feature").read_text() == "3"


def test_write_branch_pr_cache_zero_comments_writes_empty(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=21,
        title="Clean",
        comments=0,
    )
    assert (cache_dir / "pr-comments-khivi-feature").read_text() == ""


def test_write_branch_pr_cache_writes_comments_total(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=24,
        title="Threads",
        comments=2,
        total=5,
    )
    assert (cache_dir / "pr-comments-khivi-feature").read_text() == "2"
    assert (cache_dir / "pr-comments-total-khivi-feature").read_text() == "5"


def test_write_branch_pr_cache_zero_total_writes_empty(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=25,
        title="None",
        comments=0,
        total=0,
    )
    assert (cache_dir / "pr-comments-total-khivi-feature").read_text() == ""


def test_write_branch_pr_cache_writes_author(cache_dir):
    cache_mod.write_branch_pr_cache(
        "coworker/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=22,
        title="Theirs",
        author="octocat",
    )
    assert (cache_dir / "pr-author-coworker-feature").read_text() == "octocat"


def test_write_branch_pr_cache_default_author_empty(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=23,
        title="Mine",
    )
    assert (cache_dir / "pr-author-khivi-feature").read_text() == ""


def test_write_branch_pr_cache_writes_nudge(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=26,
        title="Failing",
        nudge="ci",
    )
    assert (cache_dir / "pr-nudge-khivi-feature").read_text() == "ci"


def test_write_branch_pr_cache_default_nudge_empty(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=27,
        title="Clean",
    )
    assert (cache_dir / "pr-nudge-khivi-feature").read_text() == ""


def test_write_branch_pr_cache_writes_ticket(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/fnox",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=28,
        title="Trello card",
        ticket_id="VfqsfqUd",
    )
    assert (cache_dir / "pr-ticket-khivi-fnox").read_text() == "VfqsfqUd"


def test_write_branch_pr_cache_default_ticket_empty(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=29,
        title="No ticket",
    )
    assert (cache_dir / "pr-ticket-khivi-feature").read_text() == ""


@pytest.mark.parametrize(
    "block,expected",
    [
        (None, ""),
        ({}, ""),
        ({"tickets": []}, ""),
        ({"tickets": [{"id": "PE-4608", "state": "Dev Done"}]}, "PE-4608"),
        ({"tickets": [{"id": "VfqsfqUd"}, {"id": "AbCdEf12"}]}, "VfqsfqUd"),
        ({"tickets": [{"state": "Done"}]}, ""),
        # Trello: the block carries its provider, so the pill shows the card
        # title (not the opaque short link); id fallback when the title is unset.
        (
            {
                "provider": "trello",
                "tickets": [{"id": "VfqsfqUd", "title": "Dockerize"}],
            },
            "Dockerize",
        ),
        ({"provider": "trello", "tickets": [{"id": "VfqsfqUd"}]}, "VfqsfqUd"),
        # A long Trello title is truncated so it can't widen the statusline pill.
        (
            {"provider": "trello", "tickets": [{"id": "x", "title": "y" * 60}]},
            "y" * 39 + "…",
        ),
    ],
)
def test_ticket_pill_id(block, expected):
    assert cache_mod.ticket_pill_id(block) == expected


@pytest.mark.parametrize(
    "t,provider,kwargs,expected",
    [
        ({"id": "PE-1"}, "linear", {}, "PE-1"),
        ({"id": "V1", "title": "Card"}, "trello", {}, "Card"),
        ({"id": "V1"}, "trello", {}, "V1"),
        ({}, "trello", {"missing": "?"}, "?"),
        ({"id": "V1", "title": "abcdef"}, "trello", {"max_len": 4}, "abc…"),
        # Truncation is Trello-only; a non-Trello id is returned as-is.
        ({"id": "PROJ-1234567"}, "jira", {"max_len": 4}, "PROJ-1234567"),
    ],
)
def test_ticket_display(t, provider, kwargs, expected):
    assert cache_mod.ticket_display(t, provider, **kwargs) == expected


def test_write_branch_pr_cache_no_branch_noop(cache_dir):
    cache_mod.write_branch_pr_cache(
        "",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=1,
        title="x",
    )
    assert not any(cache_dir.iterdir())


# ── refresh_pr_data / refresh_pr_checks read the per-PR JSON snapshot ──────


def test_refresh_pr_data_writes_no_pr_sentinel(cache_dir):
    with patch.object(cache_mod, "find_pr_payload", return_value=None):
        cache_mod.refresh_pr_data("khivi/foo")
    assert (cache_dir / "pr-state-khivi-foo").read_text() == ""
    assert (cache_dir / "pr-num-khivi-foo").read_text() == ""
    assert (cache_dir / "pr-title-khivi-foo").read_text() == ""
    assert (cache_dir / "pr-comments-khivi-foo").read_text() == ""
    assert (cache_dir / "pr-comments-total-khivi-foo").read_text() == ""
    assert (cache_dir / "pr-nudge-khivi-foo").read_text() == ""
    assert (cache_dir / "pr-ticket-khivi-foo").read_text() == ""


def test_refresh_pr_data_populates_ticket_from_json_snapshot(cache_dir):
    payload = {
        "state": "OPEN",
        "isDraft": False,
        "review": "",
        "number": 91,
        "title": "Linear work",
        "ticket": {"tickets": [{"id": "PE-4608", "state": "Doing"}]},
    }
    with patch.object(cache_mod, "find_pr_payload", return_value=payload):
        cache_mod.refresh_pr_data("khivi/fnox")
    assert (cache_dir / "pr-ticket-khivi-fnox").read_text() == "PE-4608"


def test_refresh_pr_data_trello_ticket_cell_is_title_not_short_link(cache_dir):
    # The statusline pill must show the human card title, not the opaque short
    # link — the block's `provider` drives `ticket_pill_id`'s Trello branch.
    payload = {
        "state": "OPEN",
        "isDraft": False,
        "review": "",
        "number": 91,
        "title": "Trello work",
        "ticket": {
            "provider": "trello",
            "tickets": [{"id": "VfqsfqUd", "state": "Doing", "title": "Dockerize"}],
        },
    }
    with patch.object(cache_mod, "find_pr_payload", return_value=payload):
        cache_mod.refresh_pr_data("khivi/fnox")
    assert (cache_dir / "pr-ticket-khivi-fnox").read_text() == "Dockerize"


def test_refresh_pr_data_populates_nudge_from_json_snapshot(cache_dir):
    payload = {
        "state": "OPEN",
        "isDraft": False,
        "review": "",
        "number": 88,
        "title": "Failing",
        "nudge": "ci",
    }
    with patch.object(cache_mod, "find_pr_payload", return_value=payload):
        cache_mod.refresh_pr_data("khivi/ci")
    assert (cache_dir / "pr-nudge-khivi-ci").read_text() == "ci"


def test_refresh_pr_data_populates_from_json_snapshot(cache_dir):
    payload = {
        "state": "OPEN",
        "isDraft": False,
        "review": "CHANGES_REQUESTED",
        "number": 99,
        "title": "Fix it",
        "unaddressed": 2,
        "total": 5,
    }
    with patch.object(cache_mod, "find_pr_payload", return_value=payload):
        cache_mod.refresh_pr_data("khivi/bar")
    assert (cache_dir / "pr-state-khivi-bar").read_text() == "CHANGES_REQUESTED"
    assert (cache_dir / "pr-num-khivi-bar").read_text() == "99"
    assert (cache_dir / "pr-title-khivi-bar").read_text() == "Fix it"
    assert (cache_dir / "pr-comments-khivi-bar").read_text() == "2"
    assert (cache_dir / "pr-comments-total-khivi-bar").read_text() == "5"


def test_refresh_pr_data_zero_unaddressed_writes_empty(cache_dir):
    payload = {
        "state": "OPEN",
        "isDraft": False,
        "review": "",
        "number": 5,
        "title": "t",
    }
    with patch.object(cache_mod, "find_pr_payload", return_value=payload):
        cache_mod.refresh_pr_data("khivi/clean")
    assert (cache_dir / "pr-comments-khivi-clean").read_text() == ""


def test_refresh_pr_data_resolves_draft(cache_dir):
    payload = {
        "state": "OPEN",
        "isDraft": True,
        "review": "",
        "number": 12,
        "title": "wip",
    }
    with patch.object(cache_mod, "find_pr_payload", return_value=payload):
        cache_mod.refresh_pr_data("khivi/draft")
    assert (cache_dir / "pr-state-khivi-draft").read_text() == "DRAFT"


def test_refresh_pr_checks_writes_no_pr_sentinel(cache_dir):
    with patch.object(cache_mod, "find_pr_payload", return_value=None):
        cache_mod.refresh_pr_checks("khivi/foo")
    assert (cache_dir / "pr-checks-khivi-foo").read_text() == ""


@pytest.mark.parametrize(
    "ci,expected",
    [
        ("passed", "✓"),
        ("pending", "•"),
        ("failed:lint", "✗"),
        ("none", ""),
        ("", ""),
    ],
    ids=["passed", "pending", "failed", "no-runs", "unknown"],
)
def test_refresh_pr_checks_derives_glyph_from_json(cache_dir, ci, expected):
    """Daemon-written JSON snapshot is the single source for both the cmux
    sidebar pill and the footer's pr-checks cell — same ci → same glyph."""
    with patch.object(cache_mod, "find_pr_payload", return_value={"ci": ci}):
        cache_mod.refresh_pr_checks("khivi/feat")
    assert (cache_dir / "pr-checks-khivi-feat").read_text() == expected


# ── write_base_distance / write_base_ahead (lib.cache) ─────────────────────


@pytest.mark.parametrize(
    "writer,cache_file",
    [
        (cache_mod.write_base_distance, "base-distance-khivi-feature"),
        (cache_mod.write_base_ahead, "base-ahead-khivi-feature"),
    ],
    ids=["write_base_distance", "write_base_ahead"],
)
@pytest.mark.parametrize(
    "branch,count,expected",
    [
        ("khivi/feature", 5, "5"),
        ("khivi/feature", -1, ""),
        ("khivi/feature", 0, "0"),
    ],
    ids=[
        "writes_payload",
        "empty_on_negative_count",
        "zero_count_is_valid",
    ],
)
def test_write_base_relative_payload(
    cache_dir, writer, cache_file, branch, count, expected
):
    """0 commits is a legitimate, fresh observation; the reader hides 0
    but the writer preserves it for staleness gating."""
    writer(branch, count)
    assert (cache_dir / cache_file).read_text() == expected


@pytest.mark.parametrize(
    "writer",
    [cache_mod.write_base_distance, cache_mod.write_base_ahead],
    ids=["write_base_distance", "write_base_ahead"],
)
def test_write_base_relative_no_branch_noop(cache_dir, writer):
    writer("", 3)
    assert not any(cache_dir.iterdir())


# ── atomic_write (lib.cache) ────────────────────────────────────────────────


def test_atomic_write_tmp_name_embeds_pid(cache_dir, monkeypatch):
    """The tmp filename must embed the writer's pid so two concurrent writers
    (daemon + a renderer-spawned `warm`) never race on the same tmp path —
    mirrors `_atomic_write_json`'s already-fixed pattern."""
    seen_tmp: list[Path] = []
    real_replace = os.replace

    def _spy_replace(src, dst):
        seen_tmp.append(Path(src))
        real_replace(src, dst)

    monkeypatch.setattr(cache_mod.os, "replace", _spy_replace)
    target = cache_dir / "pr-state-khivi-feature"
    cache_mod.atomic_write(target, "hello")

    assert len(seen_tmp) == 1
    assert seen_tmp[0].name == f"pr-state-khivi-feature.tmp.{os.getpid()}"
    assert target.read_text() == "hello"
    # No stray tmp file left behind after the rename.
    assert list(cache_dir.iterdir()) == [target]


def test_atomic_write_sequential_writers_leave_no_stray_tmp(cache_dir):
    """Two sequential writes (simulating daemon then `warm`) each clean up
    their own pid-suffixed tmp file and the final content wins."""
    target = cache_dir / "pr-state-khivi-feature"
    cache_mod.atomic_write(target, "first")
    cache_mod.atomic_write(target, "second")
    assert target.read_text() == "second"
    assert list(cache_dir.iterdir()) == [target]


def test_cwd_key_slug_shape():
    """Slug must be filesystem-safe and unambiguous across cwds."""
    from pathlib import Path as _P

    a = cache_mod._cwd_key("/tmp/foo/repo")
    b = cache_mod._cwd_key("/tmp/foo/repo2")
    assert a != b
    assert "/" not in a
    assert not a.startswith("-")
    # Path / string inputs produce the same slug.
    assert cache_mod._cwd_key(_P("/tmp/foo/repo")) == a


def test_write_git_state_cache_in_real_repo(_clean_git_env, cache_dir, tmp_path):
    from tests.fixtures import make_git_repo

    repo = make_git_repo(tmp_path, branch="main")
    cache_mod.write_git_state_cache(repo)
    slug = cache_mod._cwd_key(repo)
    assert (cache_dir / f"git-branch-{slug}").read_text() == "main"
    assert (cache_dir / f"git-status-{slug}").read_text() == "0 0 0"
    assert (cache_dir / f"git-sync-{slug}").read_text() == "0 0"


def test_write_git_state_cache_writes_status_counts(
    _clean_git_env, cache_dir, tmp_path
):
    from tests.fixtures import make_git_repo

    repo = make_git_repo(tmp_path, branch="main", status=(2, 0, 3))
    cache_mod.write_git_state_cache(repo)
    slug = cache_mod._cwd_key(repo)
    assert (cache_dir / f"git-status-{slug}").read_text() == "2 0 3"


def test_write_git_state_cache_writes_ahead(_clean_git_env, cache_dir, tmp_path):
    from tests.fixtures import make_git_repo

    repo = make_git_repo(tmp_path, ahead=3)
    cache_mod.write_git_state_cache(repo)
    slug = cache_mod._cwd_key(repo)
    assert (cache_dir / f"git-sync-{slug}").read_text() == "3 0"


def test_write_git_state_cache_writes_repo_name(_clean_git_env, cache_dir, tmp_path):
    """The owning repo name rides the git-state writer into the git-repo cell,
    even outside a repo (so a stale value can't survive a cwd leaving a repo)."""
    from tests.fixtures import make_git_repo

    repo = make_git_repo(tmp_path, branch="main")
    cache_mod.write_git_state_cache(repo, "cockpit")
    slug = cache_mod._cwd_key(repo)
    assert (cache_dir / f"git-repo-{slug}").read_text() == "cockpit"
    # Empty repo_name clears the cell.
    cache_mod.write_git_state_cache(repo, "")
    assert (cache_dir / f"git-repo-{slug}").read_text() == ""


def test_republish_pr_caches_from_disk_rewrites_flat_cells(tmp_path, monkeypatch):
    """Daemon-side fast-tick republisher: walks the per-PR JSON snapshots and
    re-writes pr-state / pr-num / pr-title / pr-muted / pr-checks. Replaces
    the old renderer-spawned `*-refresh` path."""
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    # Write a PR JSON snapshot first (daemon side).
    pr = _pr(
        ci="failed:lint",
        review_decision="APPROVED",
        number=42,
        title="Fix it",
        unaddressed=2,
        total_from_others=5,
    )
    wt = _wt()
    pref = NudgePref(muted=True)
    cache_mod.write_pr_cache("testrepo", pr, wt, pref)

    # Wipe the flat cells to simulate an OS tmpdir cleanup, then republish.
    for stem in (
        "pr-state",
        "pr-num",
        "pr-title",
        "pr-muted",
        "pr-checks",
        "pr-comments",
        "pr-comments-total",
        "pr-nudge",
    ):
        cache_mod.branch_cache(stem, "khivi/feature").unlink(missing_ok=True)
    cache_mod.republish_pr_caches_from_disk()

    flat = cache_mod.FLAT_CACHE_DIR
    assert (flat / "pr-state-khivi-feature").read_text() == "APPROVED"
    assert (flat / "pr-num-khivi-feature").read_text() == "42"
    assert (flat / "pr-title-khivi-feature").read_text() == "Fix it"
    assert (flat / "pr-muted-khivi-feature").read_text() == "muted"
    assert (flat / "pr-checks-khivi-feature").read_text() == "✗"
    assert (flat / "pr-comments-khivi-feature").read_text() == "2"
    assert (flat / "pr-comments-total-khivi-feature").read_text() == "5"
    # unaddressed=2 → primary_issue "comments" → nudge_issue "comments".
    assert (flat / "pr-nudge-khivi-feature").read_text() == "comments"


def test_pr_payload_carries_base_for_the_stack_indent(tmp_path, monkeypatch):
    # The TUI indents a stacked row off the `pr-base` cell, so the base has to
    # survive in the JSON snapshot the fast tick republishes from.
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    payload = cache_mod.write_pr_cache("testrepo", _pr(base="khivi/root"), _wt())
    assert payload["base"] == "khivi/root"

    cache_mod.branch_cache("pr-base", "khivi/feature").unlink(missing_ok=True)
    cache_mod.republish_pr_caches_from_disk()
    assert (
        cache_mod.branch_cache("pr-base", "khivi/feature").read_text() == "khivi/root"
    )


def test_republish_pr_caches_no_cache_dir_is_noop(tmp_path, monkeypatch):
    """No JSON snapshots → republisher is a no-op (doesn't crash)."""
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path / "nope"))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)
    cache_mod.republish_pr_caches_from_disk()


def test_write_git_state_cache_outside_repo_writes_empty(
    _clean_git_env, cache_dir, tmp_path
):
    """Empty branch (not a repo) must write empty cells, not skip — so a
    cached value from a previous cwd state can't survive."""
    slug = cache_mod._cwd_key(tmp_path)
    # Pre-seed stale data so we can assert it gets cleared.
    (cache_dir / f"git-branch-{slug}").write_text("stale-branch")
    (cache_dir / f"git-status-{slug}").write_text("9 9 9")
    (cache_dir / f"git-sync-{slug}").write_text("9 9")
    cache_mod.write_git_state_cache(tmp_path)
    assert (cache_dir / f"git-branch-{slug}").read_text() == ""
    assert (cache_dir / f"git-status-{slug}").read_text() == ""
    assert (cache_dir / f"git-sync-{slug}").read_text() == ""


# ── write_pr_cache pill round-trip (lib.cache) ─────────────────────────────


def test_write_pr_cache_includes_pills(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    pr = _pr(ci="failed:lint", review_decision="APPROVED")
    wt = _wt(dirty=2)
    payload = cache_mod.write_pr_cache("testrepo", pr, wt)

    assert "pills" in payload
    kinds = [p["kind"] for p in payload["pills"]]
    assert kinds == ["wip", "ci_failed", "approved"]

    on_disk = cache_mod.find_pr_payload("khivi/feature", repo_name="testrepo")
    assert on_disk is not None
    assert [p["kind"] for p in on_disk["pills"]] == kinds


# ── muted (pr-muted flat cell + JSON field) ────────────────────────────────


def test_muted_payload_helper_serializes_pref():
    assert cache_mod.muted_payload(None) == ""
    assert cache_mod.muted_payload(NudgePref()) == ""
    assert cache_mod.muted_payload(NudgePref(muted=True)) == "muted"


def test_write_branch_pr_cache_writes_muted_cell(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=1,
        title="t",
        muted="muted",
    )
    assert (cache_dir / "pr-muted-khivi-feature").read_text() == "muted"


def test_write_branch_pr_cache_unmute_clears_cell(cache_dir):
    # First write a muted state, then an unmuted one — cell must clear.
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=1,
        title="t",
        muted="ci,comments",
    )
    assert (cache_dir / "pr-muted-khivi-feature").read_text() == "ci,comments"
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=1,
        title="t",
    )
    assert (cache_dir / "pr-muted-khivi-feature").read_text() == ""


def test_refresh_pr_data_copies_muted_from_json(cache_dir):
    payload = {
        "state": "OPEN",
        "isDraft": False,
        "review": "",
        "number": 7,
        "title": "x",
        "muted": "muted",
    }
    with patch.object(cache_mod, "find_pr_payload", return_value=payload):
        cache_mod.refresh_pr_data("khivi/feat")
    assert (cache_dir / "pr-muted-khivi-feat").read_text() == "muted"


def test_refresh_pr_data_clears_muted_on_no_pr(cache_dir):
    # Pre-seed a muted cell to ensure the no-PR branch wipes it.
    (cache_dir / "pr-muted-khivi-gone").write_text("muted")
    with patch.object(cache_mod, "find_pr_payload", return_value=None):
        cache_mod.refresh_pr_data("khivi/gone")
    assert (cache_dir / "pr-muted-khivi-gone").read_text() == ""


def test_write_pr_cache_bakes_muted_into_json(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    pr = _pr()
    wt = _wt()
    pref = NudgePref(muted=True)
    payload = cache_mod.write_pr_cache("testrepo", pr, wt, pref)
    assert payload["muted"] == "muted"
    assert payload["pills"][0]["kind"] == "muted"


def test_write_pr_cache_bakes_total_into_json(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    pr = _pr(unaddressed=2, total_from_others=5)
    payload = cache_mod.write_pr_cache("testrepo", pr)
    assert payload["unaddressed"] == 2
    assert payload["total"] == 5


def test_write_pr_cache_bakes_nudge_issue_into_json(tmp_path, monkeypatch):
    """The JSON snapshot carries `PR.nudge_issue` so the fast-tick republish can
    repopulate `pr-nudge` without recomputing the model's issue logic."""
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    # OPEN + failing CI (no comments/conflicts) → actionable "ci".
    failing = cache_mod.write_pr_cache("testrepo", _pr(ci="failed:lint"))
    assert failing["nudge"] == "ci"

    # MERGED + failing CI → not actionable (state gate), so "".
    merged = cache_mod.write_pr_cache(
        "testrepo",
        _pr(number=2, branch="khivi/merged", ci="failed:lint", state="MERGED"),
    )
    assert merged["nudge"] == ""

    # A coworker's PR is never actionable for me, so the 🔔 cell stays empty and
    # can't disagree with the (suppressed) nudge.
    coworker = cache_mod.write_pr_cache(
        "testrepo",
        _pr(number=3, branch="alice/feat", ci="failed:lint", mine=False),
        other_author="alice",
    )
    assert coworker["nudge"] == ""


def test_write_pr_cache_without_worktree(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    pr = _pr(ci="failed:lint")
    payload = cache_mod.write_pr_cache("testrepo", pr)

    assert "pills" in payload
    # Without wt, no rebase/merge/wip pills appear.
    kinds = [p["kind"] for p in payload["pills"]]
    assert "wip" not in kinds
    assert "ci_failed" in kinds


# ── reused-branch dedup (find_pr_payload / republish / prune) ──────────────
#
# A branch reused across PRs (old PR merged, new PR opened from the same head)
# leaves two `{repo}__pr-{N}.json` files carrying the same `branch`. The flat
# render cells are keyed by branch only, and `_iter_cache` glob order is
# undefined — so the footer would otherwise show whichever number the
# filesystem yielded first. These tests pin the deterministic winner.


@pytest.fixture
def json_cache(tmp_path, monkeypatch):
    """Redirect both the per-PR JSON cache (COCKPIT_HOME/cache) and the flat
    cache to tmpdirs; yield the JSON cache dir. Mirrors the reload pattern the
    other COCKPIT_HOME tests use."""
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)
    cache_mod.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    flat = tmp_path / "flat"
    flat.mkdir()
    monkeypatch.setattr(cache_mod, "FLAT_CACHE_DIR", flat)
    return cache_mod.CACHE_DIR


def _snapshot(json_dir: Path, repo: str, number: int, branch: str, **fields) -> Path:
    import json

    payload = {
        "number": number,
        "branch": branch,
        "state": "OPEN",
        "isDraft": False,
        "review": "",
        "ci": "passed",
        "title": "t",
        "updatedAt": "",
        "unaddressed": 0,
        "muted": "",
    }
    payload.update(fields)
    path = json_dir / f"{repo.replace('/', '_')}__pr-{number}.json"
    path.write_text(json.dumps(payload))
    return path


def test_pr_payload_rank_orders_open_then_recency_then_number():
    open_old = {"state": "OPEN", "updatedAt": "2024-01-01", "number": 5}
    open_new = {"state": "OPEN", "updatedAt": "2024-06-01", "number": 3}
    merged_new = {"state": "MERGED", "updatedAt": "2025-01-01", "number": 99}
    rank = cache_mod._pr_payload_rank
    # OPEN beats MERGED even when MERGED is newer / higher-numbered.
    assert rank(open_old) > rank(merged_new)
    # Among OPEN, newer updatedAt wins.
    assert rank(open_new) > rank(open_old)


def test_find_pr_payload_prefers_open_over_merged(json_cache):
    # MERGED #91 and OPEN #126 share the branch; OPEN must win regardless of
    # which file the glob yields first.
    _snapshot(json_cache, "cockpit", 91, "khivi/side", state="MERGED")
    _snapshot(json_cache, "cockpit", 126, "khivi/side", state="OPEN")
    payload = cache_mod.find_pr_payload("khivi/side", repo_name="cockpit")
    assert payload is not None
    assert payload["number"] == 126


def test_find_pr_payload_prefers_newer_when_same_state(json_cache):
    _snapshot(json_cache, "cockpit", 5, "khivi/side", updatedAt="2025-03-01")
    _snapshot(json_cache, "cockpit", 6, "khivi/side", updatedAt="2025-05-01")
    _snapshot(json_cache, "cockpit", 7, "khivi/side", updatedAt="2025-01-01")
    payload = cache_mod.find_pr_payload("khivi/side", repo_name="cockpit")
    assert payload is not None
    assert payload["number"] == 6  # newest updatedAt


def test_load_pr_payloads_by_branch_matches_find_pr_payload(json_cache):
    """The one-pass map must pick the same per-branch winner find_pr_payload
    does — OPEN over MERGED on a reused branch — across multiple branches, and
    must scope to the requested repo only."""
    _snapshot(json_cache, "cockpit", 91, "khivi/side", state="MERGED")
    _snapshot(json_cache, "cockpit", 126, "khivi/side", state="OPEN")
    _snapshot(json_cache, "cockpit", 200, "khivi/other", state="OPEN")
    _snapshot(json_cache, "elsewhere", 1, "khivi/side", state="OPEN")

    by_branch = cache_mod.load_pr_payloads_by_branch("cockpit")

    assert set(by_branch) == {"khivi/side", "khivi/other"}
    assert by_branch["khivi/side"]["number"] == 126  # OPEN beats MERGED #91
    assert by_branch["khivi/other"]["number"] == 200
    # Per-branch winners agree with the per-call lookup.
    for branch, payload in by_branch.items():
        assert payload == cache_mod.find_pr_payload(branch, repo_name="cockpit")


def test_republish_picks_winner_for_reused_branch(json_cache):
    _snapshot(json_cache, "cockpit", 91, "khivi/side", state="MERGED")
    _snapshot(json_cache, "cockpit", 126, "khivi/side", state="OPEN", review="APPROVED")
    cache_mod.republish_pr_caches_from_disk()
    flat = cache_mod.FLAT_CACHE_DIR
    assert (flat / "pr-num-khivi-side").read_text() == "126"
    assert (flat / "pr-state-khivi-side").read_text() == "APPROVED"


def test_prune_superseded_drops_loser_keeps_winner(json_cache):
    merged = _snapshot(json_cache, "cockpit", 91, "khivi/side", state="MERGED")
    live = _snapshot(json_cache, "cockpit", 126, "khivi/side", state="OPEN")
    pruned = cache_mod.prune_superseded_pr_caches("cockpit")
    assert pruned == [merged]
    assert not merged.exists()
    assert live.exists()


def test_prune_superseded_keeps_lone_snapshot(json_cache):
    # A merged PR with no reused-branch sibling must survive — find_pr_payload
    # still serves it until the worktree tears down.
    only = _snapshot(json_cache, "cockpit", 91, "khivi/side", state="MERGED")
    assert cache_mod.prune_superseded_pr_caches("cockpit") == []
    assert only.exists()


def test_prune_superseded_scoped_to_repo(json_cache):
    # Two repos with the same branch name must not cross-prune.
    a = _snapshot(json_cache, "repoA", 1, "khivi/side", state="MERGED")
    _snapshot(json_cache, "repoA", 2, "khivi/side", state="OPEN")
    b = _snapshot(json_cache, "repoB", 1, "khivi/side", state="MERGED")
    cache_mod.prune_superseded_pr_caches("repoA")
    assert not a.exists()  # superseded within repoA
    assert b.exists()  # repoB untouched (lone snapshot there)


# ── reused-branch suppression (reusedBranch flag → blank PR cells) ─────────
#
# When a merged/closed PR's branch is reused for new local work, the daemon's
# slow tick stamps `reusedBranch: true` on the snapshot (the one place that
# holds the worktree — see cycle._is_reused_branch_merge). Every git-free read
# path trusts the persisted flag and shows no PR.


def test_write_pr_cache_persists_reused_branch_and_head_oid(json_cache):
    pr = _pr(state="MERGED", head_oid="deadbeef")
    payload = cache_mod.write_pr_cache("testrepo", pr, reused_branch=True)
    assert payload["reusedBranch"] is True
    assert payload["headRefOid"] == "deadbeef"
    on_disk = cache_mod.find_pr_payload("khivi/feature", repo_name="testrepo")
    assert on_disk is not None and on_disk["reusedBranch"] is True


def test_write_pr_cache_defaults_reused_branch_false(json_cache):
    payload = cache_mod.write_pr_cache("testrepo", _pr(head_oid="abc"))
    assert payload["reusedBranch"] is False
    assert payload["headRefOid"] == "abc"


def test_write_pr_cache_bakes_other_author(json_cache):
    payload = cache_mod.write_pr_cache("testrepo", _pr(), other_author="octocat")
    assert payload["author"] == "octocat"
    on_disk = cache_mod.find_pr_payload("khivi/feature", repo_name="testrepo")
    assert on_disk is not None and on_disk["author"] == "octocat"


def test_write_pr_cache_defaults_other_author_empty(json_cache):
    # Self-authored PRs (the daemon passes "") leave the field blank.
    assert cache_mod.write_pr_cache("testrepo", _pr())["author"] == ""


def test_clear_branch_pr_cache_empties_all_cells(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="APPROVED",
        number=17,
        title="Hello",
        ci_glyph="✓",
        comments=3,
    )
    cache_mod.clear_branch_pr_cache("khivi/feature")
    for stem in cache_mod._BRANCH_PR_CELLS:
        assert (cache_dir / f"{stem}-khivi-feature").read_text() == ""


def test_refresh_pr_data_blanks_reused_branch(json_cache):
    _snapshot(
        json_cache, "cockpit", 86, "khivi/side", state="MERGED", reusedBranch=True
    )
    cache_mod.refresh_pr_data("khivi/side")
    flat = cache_mod.FLAT_CACHE_DIR
    assert (flat / "pr-state-khivi-side").read_text() == ""
    assert (flat / "pr-num-khivi-side").read_text() == ""


def test_refresh_pr_checks_blanks_reused_branch(json_cache):
    _snapshot(
        json_cache,
        "cockpit",
        86,
        "khivi/side",
        state="MERGED",
        ci="failed:1",
        reusedBranch=True,
    )
    cache_mod.refresh_pr_checks("khivi/side")
    assert (cache_mod.FLAT_CACHE_DIR / "pr-checks-khivi-side").read_text() == ""


def test_republish_blanks_reused_branch(json_cache):
    # The lone snapshot for the branch is a reused-branch merge → all cells blank,
    # so an OS-tmpdir-wipe recovery never resurrects the merged state.
    _snapshot(
        json_cache, "cockpit", 86, "khivi/side", state="MERGED", reusedBranch=True
    )
    cache_mod.republish_pr_caches_from_disk()
    flat = cache_mod.FLAT_CACHE_DIR
    assert (flat / "pr-num-khivi-side").read_text() == ""
    assert (flat / "pr-state-khivi-side").read_text() == ""


def test_republish_open_pr_wins_over_reused_merged_sibling(json_cache):
    # Reused merged #86 alongside a live OPEN #99 on the same branch: the OPEN
    # snapshot outranks the merged one, so the card shows the open PR, not blank.
    _snapshot(
        json_cache, "cockpit", 86, "khivi/side", state="MERGED", reusedBranch=True
    )
    _snapshot(json_cache, "cockpit", 99, "khivi/side", state="OPEN", review="APPROVED")
    cache_mod.republish_pr_caches_from_disk()
    flat = cache_mod.FLAT_CACHE_DIR
    assert (flat / "pr-num-khivi-side").read_text() == "99"
    assert (flat / "pr-state-khivi-side").read_text() == "APPROVED"


# ── pr-base cell (the stack link the TUI indents by) ────────────────────────


def test_write_branch_pr_cache_writes_base(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/child",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=5,
        title="",
        base="khivi/root",
    )
    assert (cache_dir / "pr-base-khivi-child").read_text() == "khivi/root"


def test_write_branch_pr_cache_default_base_empty(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/solo",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=5,
        title="",
    )
    assert (cache_dir / "pr-base-khivi-solo").read_text() == ""


# ── snoozed (pr-snoozed flat cell + JSON field) ──────────────────────────────


def test_snoozed_payload_helper_serializes_pref():
    assert cache_mod.snoozed_payload(None) == ""
    assert cache_mod.snoozed_payload(NudgePref()) == ""
    assert cache_mod.snoozed_payload(NudgePref(snoozed=True)) == "snoozed"
    # Mute and snooze are separate cells — a mute must not populate this one.
    assert cache_mod.snoozed_payload(NudgePref(muted=True)) == ""


def test_write_branch_pr_cache_writes_and_clears_snoozed_cell(cache_dir):
    def write(**kw):
        cache_mod.write_branch_pr_cache(
            "khivi/nap",
            state="OPEN",
            is_draft=False,
            review_decision="",
            number=1,
            title="t",
            **kw,
        )

    write(snoozed="snoozed")
    assert (cache_dir / "pr-snoozed-khivi-nap").read_text() == "snoozed"
    # The daemon's auto-wake clears it on the next tick with no separate path.
    write()
    assert (cache_dir / "pr-snoozed-khivi-nap").read_text() == ""


def test_refresh_pr_data_copies_snoozed_from_json(cache_dir):
    payload = {
        "state": "OPEN",
        "isDraft": False,
        "review": "",
        "number": 7,
        "title": "x",
        "snoozed": "snoozed",
    }
    with patch.object(cache_mod, "find_pr_payload", return_value=payload):
        cache_mod.refresh_pr_data("khivi/feat")
    assert (cache_dir / "pr-snoozed-khivi-feat").read_text() == "snoozed"


def test_refresh_pr_data_clears_snoozed_on_no_pr(cache_dir):
    (cache_dir / "pr-snoozed-khivi-gone").write_text("snoozed")
    with patch.object(cache_mod, "find_pr_payload", return_value=None):
        cache_mod.refresh_pr_data("khivi/gone")
    assert (cache_dir / "pr-snoozed-khivi-gone").read_text() == ""


# ── restamp_pref: the TUI's `m`/`z` keypress lands without a `gh` round-trip ──


def test_restamp_pref_writes_the_cells_and_survives_a_republish(json_cache):
    """Both halves matter: the flat cells so the row repaints on the keypress,
    and the snapshot so the next fast tick's republish doesn't revert them."""
    import json

    flat = cache_mod.FLAT_CACHE_DIR
    _snapshot(json_cache, "cockpit", 7, "khivi/nap")
    cache_mod.restamp_pref("cockpit", 7, "khivi/nap", NudgePref(snoozed=True))

    assert (flat / "pr-snoozed-khivi-nap").read_text() == "snoozed"
    payload = json.loads((json_cache / "cockpit__pr-7.json").read_text())
    assert payload["snoozed"] == "snoozed"

    cache_mod.republish_pr_caches_from_disk()
    assert (flat / "pr-snoozed-khivi-nap").read_text() == "snoozed"


def test_restamp_pref_clears_both_fields_on_wake(json_cache):
    # A snooze supersedes a mute, so waking must clear the pair — leaving 🔇
    # behind would silence the row the wake just un-silenced.
    flat = cache_mod.FLAT_CACHE_DIR
    _snapshot(json_cache, "cockpit", 7, "khivi/nap", muted="muted", snoozed="snoozed")
    cache_mod.restamp_pref("cockpit", 7, "khivi/nap", NudgePref())
    assert (flat / "pr-snoozed-khivi-nap").read_text() == ""
    assert (flat / "pr-muted-khivi-nap").read_text() == ""


def test_restamp_pref_without_a_snapshot_is_a_noop(json_cache):
    # Nothing to stamp; the kicked cycle builds the snapshot.
    cache_mod.restamp_pref("cockpit", 7, "khivi/nap", NudgePref(snoozed=True))
    assert not (cache_mod.FLAT_CACHE_DIR / "pr-snoozed-khivi-nap").exists()


# ── Per-worktree session cost ───────────────────────────────────────────────


@pytest.fixture
def projects_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect `~/.claude/projects` to a tmpdir for one test."""
    pdir = tmp_path / "claude-projects"
    pdir.mkdir()
    monkeypatch.setattr(cache_mod, "CLAUDE_PROJECTS_DIR", pdir)
    return pdir


def _seed_sessions(projects_dir: Path, cache_dir: Path, cwd: Path, costs: dict) -> None:
    """Give `cwd` a Claude Code project dir holding one transcript per sid, and
    a matching `cost-<sid>` cell for each."""
    proj = projects_dir / cache_mod._claude_project_slug(cwd)
    proj.mkdir(parents=True, exist_ok=True)
    for sid, usd in costs.items():
        (proj / f"{sid}.jsonl").write_text("")
        if usd is not None:
            (cache_dir / f"cost-{sid}").write_text(str(usd))


@pytest.mark.parametrize(
    "path,slug",
    [
        # `/` and `.` both collapse to `-`, so a bare repo's dir doubles the dash.
        ("/opt/dev/repo/wt", "-opt-dev-repo-wt"),
        ("/opt/dev/repo/.bare", "-opt-dev-repo--bare"),
        ("/opt/dev/.claude/plugins", "-opt-dev--claude-plugins"),
        # Case is preserved — `Cockpit` and `cockpit` are separate project dirs.
        ("/opt/dev/git/Cockpit", "-opt-dev-git-Cockpit"),
        # Anything else non-alphanumeric collapses too, so an underscore or a
        # space can't leave a character the real directory name doesn't have.
        ("/opt/dev/git/my_repo v2", "-opt-dev-git-my-repo-v2"),
    ],
)
def test_claude_project_slug_matches_claude_codes_layout(path, slug):
    assert cache_mod._claude_project_slug(path) == slug


def test_worktree_cost_sums_every_session_in_the_worktree(
    projects_dir, cache_dir, tmp_path
):
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_sessions(projects_dir, cache_dir, wt, {"aaa": 1.5, "bbb": 2.25, "ccc": 0.25})
    # The worktree's total, not just its newest session's.
    assert cache_mod.worktree_cost(wt) == pytest.approx(4.0)


def test_worktree_cost_ignores_sessions_from_other_worktrees(
    projects_dir, cache_dir, tmp_path
):
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    mine.mkdir()
    theirs.mkdir()
    _seed_sessions(projects_dir, cache_dir, mine, {"aaa": 1.0})
    _seed_sessions(projects_dir, cache_dir, theirs, {"bbb": 99.0})
    assert cache_mod.worktree_cost(mine) == pytest.approx(1.0)


def test_worktree_cost_is_zero_with_no_project_dir(projects_dir, cache_dir, tmp_path):
    """A worktree no session ever ran in — the common case for a fresh spawn."""
    assert cache_mod.worktree_cost(tmp_path / "never-used") == 0.0


def test_worktree_cost_skips_sessions_with_no_or_bad_cost_cell(
    projects_dir, cache_dir, tmp_path
):
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_sessions(projects_dir, cache_dir, wt, {"aaa": 2.0, "bbb": None})
    (cache_dir / "cost-ccc").write_text("not-a-number")
    (projects_dir / cache_mod._claude_project_slug(wt) / "ccc.jsonl").write_text("")
    assert cache_mod.worktree_cost(wt) == pytest.approx(2.0)


def test_write_worktree_cost_cache_round_trips(projects_dir, cache_dir, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_sessions(projects_dir, cache_dir, wt, {"aaa": 31.5146})
    cache_mod.write_worktree_cost_cache(wt)
    assert cache_mod.read_worktree_cost(wt) == pytest.approx(31.5146)


def test_write_worktree_cost_cache_writes_blank_not_zero(
    projects_dir, cache_dir, tmp_path
):
    """A costless worktree gets "" so the reader can't mistake "never reported"
    for a real $0 — the renderer blanks the cell either way."""
    wt = tmp_path / "wt"
    wt.mkdir()
    cache_mod.write_worktree_cost_cache(wt)
    assert cache_mod.cwd_cache("wt-cost", wt).read_text() == ""
    assert cache_mod.read_worktree_cost(wt) == 0.0


def test_worktree_cost_cell_does_not_collide_with_session_cost_cells(
    projects_dir, cache_dir, tmp_path
):
    """`wt-cost-<path>` and `cost-<sid>` share a directory; the stems must not
    let a `cost-*` glob match a worktree total (or vice versa)."""
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_sessions(projects_dir, cache_dir, wt, {"aaa": 5.0})
    cache_mod.write_worktree_cost_cache(wt)
    assert [p.name for p in cache_dir.glob("cost-*")] == ["cost-aaa"]
    assert len(list(cache_dir.glob("wt-cost-*"))) == 1


def test_read_worktree_cost_survives_a_corrupt_cell(cache_dir, tmp_path):
    cache_mod.atomic_write(cache_mod.cwd_cache("wt-cost", tmp_path), "garbage")
    assert cache_mod.read_worktree_cost(tmp_path) == 0.0


def test_cost_reporting_available_is_false_with_no_cells(cache_dir):
    assert cache_mod.cost_reporting_available() is False


def test_cost_reporting_available_is_false_when_every_session_reports_zero(cache_dir):
    """The gate for a plan/build that writes `total_cost_usd: 0` — the `$`
    column must not appear just because the cells exist."""
    (cache_dir / "cost-aaa").write_text("0.0000")
    (cache_dir / "cost-bbb").write_text("0.0000")
    assert cache_mod.cost_reporting_available() is False


def test_cost_reporting_available_is_true_on_one_real_number(cache_dir):
    (cache_dir / "cost-aaa").write_text("0.0000")
    (cache_dir / "cost-bbb").write_text("2.9009")
    assert cache_mod.cost_reporting_available() is True


def test_cost_reporting_available_ignores_worktree_totals(cache_dir, tmp_path):
    """A `wt-cost` cell is cockpit's own derived value; it can't be the evidence
    that Claude Code reports cost, or the gate would latch itself on."""
    cache_mod.atomic_write(cache_mod.cwd_cache("wt-cost", tmp_path), "12.0")
    assert cache_mod.cost_reporting_available() is False
