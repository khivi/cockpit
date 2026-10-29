"""The accumulator the slow tick fills per repo and drains once."""

from __future__ import annotations

import pytest

from cockpit.lib import cache, spawn_results
from cockpit.orchestrators.spawn_failures import SpawnFailures, publish


def _acc(prs, branches=()):
    acc = SpawnFailures()
    acc.add(["o/r", "mine"], prs, set(branches))
    return acc


@pytest.mark.covers("spawn.failure-payload~1")
def test_open_pr_without_worktree_is_published_with_its_title():
    spawn_results.record_failure("o/r", 7, 2, "no remote")
    out = publish(_acc({7: ("Fix it", "b7")}))
    assert out is not None
    assert out == [
        {
            "pr": 7,
            "repo": "o/r",
            "title": "Fix it",
            "failures": 1,
            "ts": out[0]["ts"],
            "error": "no remote",
        }
    ]
    assert cache.load_spawn_failures() == out


@pytest.mark.covers("spawn.failure-payload~1")
def test_markers_retire_when_pr_closed_or_worktree_exists():
    spawn_results.record_failure("o/r", 7, 1, "x")  # PR gone
    spawn_results.record_failure("o/r", 8, 1, "y")  # now has a worktree
    out = publish(_acc({8: ("t", "b8")}, branches={"b8"}))
    assert out == []
    assert spawn_results.iter_pending() == []
    assert cache.load_spawn_failures() == []


@pytest.mark.covers("spawn.failure-payload~1")
def test_unreported_repo_keeps_its_marker_and_config_name_matches():
    spawn_results.record_failure("other/repo", 1, 1, "x")
    spawn_results.record_failure("mine", 7, 1, "fallback key")
    out = publish(_acc({7: ("t", "b7")}))
    assert [f["repo"] for f in out or []] == ["mine"]
    assert len(spawn_results.iter_pending() or []) == 2


@pytest.mark.covers("spawn.failure-payload~1")
def test_partial_cycle_writes_nothing_and_dry_deletes_nothing():
    spawn_results.record_failure("o/r", 7, 1, "x")
    cache.write_spawn_failures([{"pr": 99}])
    acc = _acc({7: ("t", "b7")})
    acc.partial = True
    publish(acc)
    assert cache.load_spawn_failures() == [{"pr": 99}]
    spawn_results.record_failure("o/r", 8, 1, "gone pr")
    publish(_acc({7: ("t", "b7")}), dry=True)
    assert len(spawn_results.iter_pending() or []) == 2
