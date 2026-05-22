"""Persistent close-request queue under `$COCKPIT_HOME/state/close-requests/`.

`scripts/close.py` writes one JSON marker per requested teardown; the daemon
drains the queue each cycle through `orchestrators.teardown.teardown`. The decoupling
keeps teardown logic in one place and lets the daemon own retry/refusal.

Layout:
    $COCKPIT_HOME/state/close-requests/<repo>/<ref>.json
    $COCKPIT_HOME/state/close-requests/_global/<ref>.json   # repo unknown

Marker schema mirrors `TeardownRequest` plus a `requested_at` timestamp.
Markers older than `STALE_SECONDS` are pruned silently — a long enough
window for a brief daemon outage, short enough that a reboot doesn't
auto-close worktrees the user has stopped caring about.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from orchestrators.teardown import TeardownRequest

from .config import COCKPIT_HOME

STATE_DIR = COCKPIT_HOME / "state" / "close-requests"
STALE_SECONDS = 3600


def _repo_dir(repo_name: str | None) -> Path:
    return STATE_DIR / (repo_name or "_global")


def _safe_filename(ref: str) -> str:
    return ref.replace("/", "_").replace(":", "_") + ".json"


def enqueue(req: TeardownRequest) -> Path:
    """Atomically write a close-request marker; returns the path written."""
    dest = _repo_dir(req.repo_name)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / _safe_filename(req.ref)
    payload = {
        "ref": req.ref,
        "name": req.name,
        "worktree_path": str(req.worktree_path) if req.worktree_path else None,
        "branch": req.branch,
        "repo_path": str(req.repo_path) if req.repo_path else None,
        "repo_name": req.repo_name,
        "forced": req.forced,
        "requested_at": time.time(),
    }
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)
    return path


def _read_marker(path: Path) -> TeardownRequest | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    wt_path = data.get("worktree_path")
    repo_path = data.get("repo_path")
    return TeardownRequest(
        ref=data["ref"],
        name=data.get("name", ""),
        worktree_path=Path(wt_path) if wt_path else None,
        branch=data.get("branch"),
        repo_path=Path(repo_path) if repo_path else None,
        repo_name=data.get("repo_name"),
        forced=bool(data.get("forced", False)),
    )


def iter_pending(repo_name: str | None = None) -> list[tuple[Path, TeardownRequest]]:
    """List pending markers; with `repo_name`, scope to that repo's subdir."""
    if not STATE_DIR.is_dir():
        return []
    if repo_name is not None:
        bases = [_repo_dir(repo_name)]
    else:
        bases = sorted(p for p in STATE_DIR.iterdir() if p.is_dir())
    out: list[tuple[Path, TeardownRequest]] = []
    for base in bases:
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.json")):
            req = _read_marker(path)
            if req is not None:
                out.append((path, req))
    return out


def pop(path: Path) -> None:
    """Delete a processed marker. Safe if already gone."""
    path.unlink(missing_ok=True)


def prune_stale(*, now: float | None = None) -> list[Path]:
    """Delete markers older than `STALE_SECONDS`; returns paths pruned."""
    cutoff = (now if now is not None else time.time()) - STALE_SECONDS
    pruned: list[Path] = []
    if not STATE_DIR.is_dir():
        return pruned
    for base in STATE_DIR.iterdir():
        if not base.is_dir():
            continue
        for path in base.glob("*.json"):
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("requested_at", 0) < cutoff:
                path.unlink(missing_ok=True)
                pruned.append(path)
    return pruned
