"""Unit tests for the undelivered-seed queue.

The queue's whole reason to exist is that a spawn is a separate process from
the daemon, so these tests care about the marker surviving that gap intact and
about the two ways a marker must NOT outlive its usefulness — a workspace that
went away, and a body that got too old to send.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from cockpit.lib import seed_queue
from cockpit.lib.seed_queue import SeedRequest


def _req(ref="workspace:1", text="do the thing", cwd="/tmp/wt"):
    return SeedRequest(ref=ref, text=text, cwd=cwd)


def test_a_queued_body_survives_the_process_boundary_verbatim():
    """The spawn writes, the daemon reads — different processes, so the marker
    is the only thing carrying the body. A body that came back altered would be
    typed into the session altered."""
    body = "You are starting a fresh task — fetch PE-1234 and rename the branch"
    seed_queue.enqueue(_req(text=body))

    pending = seed_queue.iter_pending()
    assert len(pending) == 1
    _path, req = pending[0]
    assert req.ref == "workspace:1"
    assert req.text == body
    assert req.cwd == "/tmp/wt"


def test_enqueue_is_keyed_by_ref_so_a_respawn_supersedes():
    """One pending body per workspace. Spawning onto the same workspace again is
    the user replacing the request, not adding a second — two markers would type
    both bodies in."""
    seed_queue.enqueue(_req(text="first"))
    seed_queue.enqueue(_req(text="second"))

    pending = seed_queue.iter_pending()
    assert len(pending) == 1
    assert pending[0][1].text == "second"


def test_enqueue_fails_open_when_the_runtime_dir_is_unwritable(monkeypatch, capsys):
    """Queuing runs on a path where a delivery has already gone wrong. The
    worktree and workspace are both fine at that point, so a read-only runtime
    dir costs the retry, never the spawn."""
    monkeypatch.setattr(
        seed_queue.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("ro"))
    )
    assert seed_queue.enqueue(_req()) is None
    assert "cannot queue seed retry" in capsys.readouterr().err


def test_prune_stale_drops_a_body_too_old_to_be_a_first_turn():
    """A seed body says "you are starting a fresh task" and asks for a rename and
    a plan. Delivered into a session the user has since been working in, it is
    worse than not delivered — so an unsent marker expires rather than waiting
    for an idle window that now means something else."""
    seed_queue.enqueue(_req(ref="workspace:old"))
    seed_queue.enqueue(_req(ref="workspace:new"))
    old = seed_queue.STATE_DIR / "workspace_old.json"
    data = json.loads(old.read_text())
    data["requested_at"] = time.time() - seed_queue.STALE_SECONDS - 1
    old.write_text(json.dumps(data))

    pruned = seed_queue.prune_stale()

    assert pruned == [old]
    assert [r.ref for _p, r in seed_queue.iter_pending()] == ["workspace:new"]


def test_pop_is_safe_on_an_already_deleted_marker():
    """Two drains can race a marker — the fast tick and a resumed one — and a
    retire that raised would take the tick down with it."""
    path = seed_queue.enqueue(_req())
    assert path is not None
    seed_queue.pop(path)
    seed_queue.pop(path)
    assert seed_queue.iter_pending() == []


def test_a_corrupt_or_truncated_marker_is_skipped_not_fatal(capsys):
    """The writer is a separate process that can die mid-write. One unreadable
    marker must not strand every other pending body."""
    seed_queue.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (seed_queue.STATE_DIR / "broken.json").write_text("{not json")
    (seed_queue.STATE_DIR / "empty.json").write_text('{"ref": "workspace:9"}')
    seed_queue.enqueue(_req(ref="workspace:good"))

    pending = seed_queue.iter_pending()

    assert [r.ref for _p, r in pending] == ["workspace:good"]
    assert "skipping corrupt seed marker" in capsys.readouterr().err


def test_iter_pending_is_empty_before_anything_is_queued():
    """The ordinary case makes no directory and no read — a queue nobody has
    used costs the fast tick nothing."""
    assert not seed_queue.STATE_DIR.exists()
    assert seed_queue.iter_pending() == []
    assert seed_queue.prune_stale() == []


def test_the_queue_lives_under_the_runtime_dir_not_cockpit_home():
    """A marker's `ref` is a cmux workspace id, meaningless on another machine.
    Under a synced `$COCKPIT_HOME` one machine would type another's prompts into
    whatever workspace happens to hold that ref."""
    from cockpit.lib import config

    assert Path(config.COCKPIT_RUNTIME_DIR) in Path(seed_queue.STATE_DIR).parents
