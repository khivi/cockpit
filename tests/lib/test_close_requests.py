"""Round-trip tests for the close-request queue under $COCKPIT_HOME."""

from __future__ import annotations

import importlib
import json
import time
from pathlib import Path

import pytest


@pytest.fixture
def queue(tmp_path, monkeypatch):
    """Isolate close-request state under tmp_path and reload the module."""
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import lib.config as cfg

    importlib.reload(cfg)
    import lib.close_requests as cr

    importlib.reload(cr)
    return cr


def test_enqueue_and_iter_round_trip(queue):
    from lib.teardown import TeardownRequest

    req = TeardownRequest(
        ref="workspace:7",
        name="feat-x",
        worktree_path=Path("/tmp/wt"),
        branch="khivi/feat-x",
        repo_path=Path("/tmp/repo"),
        repo_name="needl-ai",
        forced=True,
    )
    path = queue.enqueue(req)
    assert path.exists()

    pending = queue.iter_pending()
    assert len(pending) == 1
    got_path, got_req = pending[0]
    assert got_path == path
    assert got_req.ref == "workspace:7"
    assert got_req.name == "feat-x"
    assert got_req.worktree_path == Path("/tmp/wt")
    assert got_req.branch == "khivi/feat-x"
    assert got_req.repo_name == "needl-ai"
    assert got_req.forced is True


def test_pop_removes_marker(queue):
    from lib.teardown import TeardownRequest

    req = TeardownRequest(ref="workspace:1", repo_name="r")
    path = queue.enqueue(req)
    queue.pop(path)
    assert not path.exists()
    assert queue.iter_pending() == []


def test_iter_pending_scoped_by_repo(queue):
    from lib.teardown import TeardownRequest

    queue.enqueue(TeardownRequest(ref="workspace:1", repo_name="repo-a"))
    queue.enqueue(TeardownRequest(ref="workspace:2", repo_name="repo-b"))
    queue.enqueue(TeardownRequest(ref="workspace:3", repo_name=None))

    a_only = queue.iter_pending(repo_name="repo-a")
    assert [r.ref for _, r in a_only] == ["workspace:1"]

    global_only = queue.iter_pending(repo_name=None)
    refs = sorted(r.ref for _, r in global_only)
    assert refs == ["workspace:1", "workspace:2", "workspace:3"]


def test_prune_stale_removes_stale_requests(queue):
    from lib.teardown import TeardownRequest

    fresh = queue.enqueue(TeardownRequest(ref="workspace:fresh", repo_name="r"))
    stale = queue.enqueue(TeardownRequest(ref="workspace:stale", repo_name="r"))

    # Backdate the stale marker
    data = json.loads(stale.read_text())
    data["requested_at"] = time.time() - queue.STALE_SECONDS - 10
    stale.write_text(json.dumps(data))

    pruned = queue.prune_stale()
    assert stale in pruned
    assert fresh.exists()


def test_corrupt_marker_skipped(queue, tmp_path):
    from lib.teardown import TeardownRequest

    queue.enqueue(TeardownRequest(ref="workspace:1", repo_name="r"))
    (queue.STATE_DIR / "r" / "garbage.json").write_text("not json {")
    pending = queue.iter_pending()
    assert len(pending) == 1
    assert pending[0][1].ref == "workspace:1"
