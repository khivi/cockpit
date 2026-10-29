"""Result markers for failed background spawns, against real files."""

from __future__ import annotations

from cockpit.lib import spawn_results


def test_failure_counts_consecutive_failures_and_success_clears(tmp_path):
    d = tmp_path / "r"
    assert spawn_results.record_failure("o/r", 7, 2, "boom", state_dir=d) == 1
    assert spawn_results.record_failure("o/r", 7, 1, "again", state_dir=d) == 2
    [res] = spawn_results.iter_pending(state_dir=d) or []
    assert (res.repo, res.pr, res.rc, res.error, res.failures) == (
        "o/r",
        7,
        1,
        "again",
        2,
    )
    spawn_results.clear("o/r", 7, state_dir=d)
    assert spawn_results.iter_pending(state_dir=d) == []


def test_same_pr_number_in_two_repos_is_two_markers(tmp_path):
    spawn_results.record_failure("o/a", 7, 1, "x", state_dir=tmp_path)
    spawn_results.record_failure("o/b", 7, 1, "y", state_dir=tmp_path)
    assert {r.repo for r in spawn_results.iter_pending(state_dir=tmp_path) or []} == {
        "o/a",
        "o/b",
    }


def test_missing_dir_is_empty_and_corrupt_marker_is_skipped(tmp_path):
    assert spawn_results.iter_pending(state_dir=tmp_path / "nope") == []
    (tmp_path / "bad__1.json").write_text("{not json")
    spawn_results.record_failure("o/r", 2, 1, "e", state_dir=tmp_path)
    assert [r.pr for r in spawn_results.iter_pending(state_dir=tmp_path) or []] == [2]
