"""Result markers for background `cockpit new --pr` spawns that failed.

The daemon launches those children detached (`cycle._bg_spawn_pr`), so a failure
lands in `spawn.log` and nowhere a user looks: the PR has no worktree, hence no
table row, hence nothing to be unhappy about. The child therefore writes its own
verdict here -- one JSON marker per (repo, PR) under `$COCKPIT_RUNTIME_DIR` --
and the daemon folds the markers into a cache payload the TUI shows
(`orchestrators/spawn_failures.py`).

Machine-local runtime state, like `seed_queue`: a synced `$COCKPIT_HOME` would
show one machine's failures on another. Keyed by the repo's nwo name plus the PR
number, matching `NudgePref`: a bare number is not an identity.

The child writes and clears; only the daemon retires a marker whose PR has gone.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import COCKPIT_RUNTIME_DIR

STATE_DIR = COCKPIT_RUNTIME_DIR / "spawn-results"


@dataclass(frozen=True)
class SpawnResult:
    repo: str
    pr: int
    rc: int
    ts: float
    error: str
    failures: int


def _path(repo: str, pr: int, state_dir: Path | None = None) -> Path:
    return (state_dir or STATE_DIR) / f"{repo.replace('/', '_')}__{pr}.json"


def _read(path: Path) -> SpawnResult | None:
    try:
        d = json.loads(path.read_text())
        return SpawnResult(
            repo=str(d["repo"]),
            pr=int(d["pr"]),
            rc=int(d["rc"]),
            ts=float(d["ts"]),
            error=str(d.get("error") or ""),
            failures=int(d.get("failures") or 1),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def record_failure(
    repo: str, pr: int, rc: int, error: str, *, state_dir: Path | None = None
) -> int:
    """Write (or overwrite) the marker, bumping the consecutive-failure count.

    Returns the new count, or 0 if it couldn't be written. Fails open: this runs
    on a path that has already failed, and must not mask the real exit code.
    """
    path = _path(repo, pr, state_dir)
    prior = _read(path)
    failures = (prior.failures if prior else 0) + 1
    payload = {
        "repo": repo,
        "pr": pr,
        "rc": rc,
        "ts": time.time(),
        "error": error,
        "failures": failures,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # pid-scoped temp: see `seed_queue.enqueue`.
        tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(path)
    except OSError as e:
        print(
            f"cockpit: cannot record spawn result for {repo}#{pr}: {e}", file=sys.stderr
        )
        return 0
    return failures


def clear(repo: str, pr: int, *, state_dir: Path | None = None) -> None:
    """Delete the marker -- the spawn succeeded, or the daemon retired it."""
    _path(repo, pr, state_dir).unlink(missing_ok=True)


def iter_pending(*, state_dir: Path | None = None) -> list[SpawnResult] | None:
    """Every readable marker, oldest first; `[]` for none, None when the
    directory exists but can't be listed ("couldn't ask", not "nothing")."""
    root = state_dir or STATE_DIR
    if not root.exists():
        return []
    try:
        paths = sorted(root.glob("*.json"))
    except OSError:
        return None
    out = [r for p in paths if (r := _read(p)) is not None]
    return sorted(out, key=lambda r: r.ts)
