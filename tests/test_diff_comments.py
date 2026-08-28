"""`lib/diff_comments.py` — reading cmux's diff-viewer comment store.

Leaf-module tests against a real store directory on `tmp_path`: the store is
plain JSON written by another process, so the thing worth testing is that we
survive whatever it contains.
"""

from __future__ import annotations

import json

import pytest

from cockpit.lib import diff_comments


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    """Point the module at a throwaway store + ledger.

    Both are module-level constants derived at import, so patching the
    attributes is the only isolation that holds — `$COCKPIT_RUNTIME_DIR` is
    read once, before any test sets it.
    """
    store = tmp_path / "diff-comments"
    store.mkdir()
    monkeypatch.setattr(diff_comments, "STORE_DIR", store)
    monkeypatch.setattr(diff_comments, "DELIVERED", tmp_path / "delivered.json")
    return store


def _write(store, name, root, comments):
    (store / f"{name}.json").write_text(
        json.dumps({"repoRoot": str(root), "comments": comments})
    )


def _comment(cid="c1", path="app/main.py", line=10, message="reduce comments"):
    return {
        "id": cid,
        "filePath": path,
        "startLine": line,
        "endLine": line,
        "message": message,
        "side": "additions",
        "submissionText": "Review comment on ...\n\n```diff\n+x\n```\n",
    }


def test_pending_finds_comments_filed_against_the_row(_store, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    _write(_store, "a", wt, [_comment()])

    got = diff_comments.pending([wt])

    assert [(c.file, c.line, c.message) for c in got] == [
        ("app/main.py", 10, "reduce comments")
    ]


def test_a_comment_filed_against_another_repo_is_not_delivered(_store, tmp_path):
    """The whole bug this feature exists for: comments pile up under whatever
    repo the daemon was launched in. Those must not leak onto an unrelated row."""
    wt, other = tmp_path / "wt", tmp_path / "dotfiles"
    wt.mkdir()
    other.mkdir()
    _write(_store, "a", other, [_comment()])

    assert diff_comments.pending([wt]) == []


def test_any_candidate_root_matches(_store, tmp_path):
    """Which root cmux records for a worktree is undocumented, so the caller
    offers both its worktree and its main checkout."""
    wt, repo = tmp_path / "wt", tmp_path / "repo"
    wt.mkdir()
    repo.mkdir()
    _write(_store, "a", repo, [_comment()])

    assert len(diff_comments.pending([wt, repo])) == 1


def test_delivered_comments_are_not_sent_twice(_store, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    _write(_store, "a", wt, [_comment(cid="c1"), _comment(cid="c2", line=20)])

    diff_comments.mark_delivered(["c1"])

    assert [c.id for c in diff_comments.pending([wt])] == ["c2"]


def test_mark_delivered_accumulates_across_calls(_store, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    _write(_store, "a", wt, [_comment(cid="c1"), _comment(cid="c2", line=20)])

    diff_comments.mark_delivered(["c1"])
    diff_comments.mark_delivered(["c2"])

    assert diff_comments.pending([wt]) == []


def test_an_empty_comment_is_nothing_to_deliver(_store, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    _write(_store, "a", wt, [_comment(message="   "), _comment(cid="", message="x")])

    assert diff_comments.pending([wt]) == []


def test_a_corrupt_store_file_fails_open(_store, tmp_path):
    """Another process owns these files; a half-written one must cost us the
    comments in it, never the send."""
    wt = tmp_path / "wt"
    wt.mkdir()
    (_store / "bad.json").write_text("{not json")
    _write(_store, "good", wt, [_comment()])

    assert len(diff_comments.pending([wt])) == 1


def test_a_missing_store_is_silent(_store, tmp_path, monkeypatch):
    monkeypatch.setattr(diff_comments, "STORE_DIR", tmp_path / "nope")

    assert diff_comments.pending([tmp_path]) == []


def test_an_unwritable_ledger_costs_a_repeat_not_a_crash(_store, tmp_path, monkeypatch):
    monkeypatch.setattr(
        diff_comments, "DELIVERED", tmp_path / "no-such-dir" / "x" / "d.json"
    )

    def _boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(diff_comments.Path, "mkdir", _boom)
    diff_comments.mark_delivered(["c1"])  # must not raise


def test_summarize_is_one_line_and_drops_the_fenced_excerpt(_store):
    """`cmux send` turns every newline into Enter, so the store's own
    multi-line `submissionText` cannot survive the trip — and the agent is in
    the worktree and can read the file itself. The anchor is what it needs."""
    line = diff_comments.summarize(
        [
            diff_comments.Comment("c1", "a.py", 3, "explain this"),
            diff_comments.Comment("c2", "b.py", 9, "reduce comments"),
        ]
    )

    assert "\n" not in line
    assert "```" not in line
    assert line == "a.py:3 — explain this; b.py:9 — reduce comments"
