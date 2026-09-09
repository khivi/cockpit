"""Undelivered seed prompts, queued for the daemon to re-deliver.

A spawn hands its seeded first-turn body to the workspace by *keystroke*
(`cmux.deliver_followup`), and a cold Claude Code cannot always take one — the
composer is not up yet. `deliver_followup` now detects that instead of assuming
it, which turns a silent loss into a warning nobody reads: the spawn is a
detached process whose stdout is `spawn.log`. This queue is the other half. A
body that could not be delivered is written here, and the daemon's fast tick
re-delivers it through `nudge_if_idle` — which only fires at a moment the
session provably accepts input, the exact condition missing at spawn time. Late
instead of lost.

Deliberately the same machine as the close queue (`daemon_signal`): a separate
process has something the daemon must act on, and durable JSON markers under
`$COCKPIT_RUNTIME_DIR` are how cockpit already answers that. **Not** under
`$COCKPIT_HOME` — a marker's `ref` is a cmux workspace id, meaningless on any
other machine, so a synced queue would have one machine typing another's
prompts. See `config.COCKPIT_RUNTIME_DIR`.

`STALE_SECONDS` is short, and that is correctness rather than tidiness. The
retry exists to cover a slow boot, which is seconds. A seed body opens with
"You are starting a fresh task in a new worktree" and instructs a rename and a
plan; delivering that into a session the user has since started working in is
worse than not delivering it at all. If a workspace has not come to rest within
the window, the user is already driving it and the body is stale by definition,
so the marker is dropped rather than held.

One marker per workspace, keyed by ref: a second spawn onto the same workspace
supersedes the first, which is what the user asked for by spawning again.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import COCKPIT_RUNTIME_DIR

STATE_DIR = COCKPIT_RUNTIME_DIR / "seed-requests"
STALE_SECONDS = 300


@dataclass(frozen=True)
class SeedRequest:
    """A first-turn body that never reached its workspace's composer."""

    ref: str
    text: str
    cwd: str | None = None


def _safe_filename(ref: str) -> str:
    return ref.replace("/", "_").replace(":", "_") + ".json"


def enqueue(req: SeedRequest) -> Path | None:
    """Atomically write a pending-seed marker; the path, or None if it couldn't
    be written.

    Fails open (None, no raise): this runs on the failure path of a delivery
    that has already gone wrong, and a read-only runtime dir must cost the retry,
    never the spawn — the worktree and workspace are both fine at this point.
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = STATE_DIR / _safe_filename(req.ref)
        payload = {
            "ref": req.ref,
            "text": req.text,
            "cwd": req.cwd,
            "requested_at": time.time(),
        }
        # pid-scoped temp, like `config._atomic_write_text`: `os.replace` is
        # atomic, so a fixed suffix never yields a torn file — it yields the
        # wrong one when two spawns race, since both write the same ref only
        # when one is superseding the other.
        tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(path)
    except OSError as e:
        print(f"cockpit: cannot queue seed retry for {req.ref}: {e}", file=sys.stderr)
        return None
    return path


def _read_marker(path: Path) -> SeedRequest | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"cockpit: skipping corrupt seed marker {path}: {e}", file=sys.stderr)
        return None
    ref, text = data.get("ref"), data.get("text")
    if not ref or not text:
        return None
    return SeedRequest(ref=str(ref), text=str(text), cwd=data.get("cwd"))


def iter_pending() -> list[tuple[Path, SeedRequest]]:
    """Every readable pending marker, oldest filename first."""
    if not STATE_DIR.is_dir():
        return []
    out: list[tuple[Path, SeedRequest]] = []
    for path in sorted(STATE_DIR.glob("*.json")):
        req = _read_marker(path)
        if req is not None:
            out.append((path, req))
    return out


def pop(path: Path) -> None:
    """Delete a retired marker. Safe if already gone."""
    path.unlink(missing_ok=True)


def prune_stale(*, now: float | None = None) -> list[Path]:
    """Delete markers past `STALE_SECONDS`; returns the paths pruned."""
    cutoff = (now if now is not None else time.time()) - STALE_SECONDS
    pruned: list[Path] = []
    if not STATE_DIR.is_dir():
        return pruned
    for path in STATE_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("requested_at", 0) < cutoff:
            path.unlink(missing_ok=True)
            pruned.append(path)
    return pruned
