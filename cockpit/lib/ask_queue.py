"""`a`/`A` lines refused by a busy session, held for the daemon to deliver.

`a` sends through `nudge_if_idle`, which refuses a session that is mid-turn or
not provably at rest. Before this queue the refusal kept the draft and asked
you to press `a` again later — which means watching the row for the turn to
end, the thing the dashboard exists to do for you. A refusal that is only
*waiting* (`cmux.rest_pending`) now lands here instead, and the fast tick
re-offers it through the same gate until one is accepted.

The same machine as `seed_queue`, run against its own directory: a durable
marker per workspace under `$COCKPIT_RUNTIME_DIR`, keyed by ref, retired only
on an accepted send, dropped when the ref leaves cmux. A **separate directory**
because both are keyed by ref — sharing one would let a queued ask overwrite a
pending seed body for the same workspace, and the seed must land first.

The marker carries the worktree `cwd` and the drain checks it against cmux's
live listing, since cmux reuses refs and this window is longer than the seed
one: a line typed for one worktree must never reach whatever now holds its ref.

`STALE_SECONDS` bounds how late a line may arrive. A turn running past it has
probably already answered or overtaken what you typed, so the marker is
dropped and logged rather than delivered into a conversation that moved on.
A second `a` to the same workspace replaces the first, as a respawn does.
"""

from __future__ import annotations

from pathlib import Path

from . import seed_queue
from .config import COCKPIT_RUNTIME_DIR
from .seed_queue import SeedRequest

STATE_DIR = COCKPIT_RUNTIME_DIR / "ask-requests"
STALE_SECONDS = 600


def enqueue(ref: str, text: str, cwd: str) -> Path | None:
    return seed_queue.enqueue(
        SeedRequest(ref=ref, text=text, cwd=cwd), state_dir=STATE_DIR
    )


def iter_pending() -> list[tuple[Path, SeedRequest]]:
    return seed_queue.iter_pending(state_dir=STATE_DIR)


def pop(path: Path) -> None:
    seed_queue.pop(path)


def prune_stale(*, now: float | None = None) -> list[Path]:
    return seed_queue.prune_stale(
        now=now, state_dir=STATE_DIR, stale_seconds=STALE_SECONDS
    )
