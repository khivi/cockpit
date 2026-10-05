"""`lib/ask_queue.py` — refused `a` lines held for the fast tick."""

from __future__ import annotations

import json
import time

import pytest

from cockpit.lib import ask_queue, seed_queue
from cockpit.lib.seed_queue import SeedRequest


def test_a_second_ask_to_the_same_ref_replaces_the_first():
    ask_queue.enqueue("workspace:1", "first", "/w")
    ask_queue.enqueue("workspace:1", "second", "/w")

    assert [r.text for _p, r in ask_queue.iter_pending()] == ["second"]


@pytest.mark.covers("ask-queue.separate~1")
def test_an_ask_never_supersedes_a_pending_seed_for_the_same_ref():
    """Both queues key by ref; one directory would let the ask overwrite the
    seed body, which has to land first."""
    seed_queue.enqueue(SeedRequest(ref="workspace:1", text="seed body"))
    ask_queue.enqueue("workspace:1", "typed line", "/w")

    assert [r.text for _p, r in seed_queue.iter_pending()] == ["seed body"]
    assert [r.text for _p, r in ask_queue.iter_pending()] == ["typed line"]


@pytest.mark.covers("ask-queue.stale~1")
def test_prune_drops_only_asks_past_the_window():
    ask_queue.enqueue("workspace:old", "old", "/w")
    ask_queue.enqueue("workspace:new", "new", "/w")
    old = ask_queue.STATE_DIR / "workspace_old.json"
    data = json.loads(old.read_text())
    data["requested_at"] = time.time() - ask_queue.STALE_SECONDS - 1
    old.write_text(json.dumps(data))

    assert ask_queue.prune_stale() == [old]
    assert [r.ref for _p, r in ask_queue.iter_pending()] == ["workspace:new"]


def test_the_ask_window_is_its_own_not_the_seed_one():
    """A seed past its 300s window is stale; a typed line is not yet."""
    ask_queue.enqueue("workspace:1", "line", "/w")
    path = ask_queue.STATE_DIR / "workspace_1.json"
    data = json.loads(path.read_text())
    data["requested_at"] = time.time() - seed_queue.STALE_SECONDS - 1
    path.write_text(json.dumps(data))

    assert ask_queue.prune_stale() == []
