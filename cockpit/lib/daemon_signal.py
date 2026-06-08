"""CLI → daemon signaling: SIGUSR1 kick, SIGTERM stop, and the close-request queue.

This is the *caller-side* IPC channel — everything `cockpit/close.py`,
`cockpit/sync.py`, `cockpit/spawn.py`, etc. use to talk *to* the daemon.
The daemon-side runtime (pidfile + watch loop) lives in `lib/daemon.py`.

Two channels live here because they're two halves of the same conversation:

  - **Signal**: `kick_running` (SIGUSR1 to wake), `stop_running` (SIGTERM to halt).
  - **Queue**: `enqueue` / `iter_pending` / `pop` / `prune_stale` — durable
    JSON markers under `$COCKPIT_HOME/state/close-requests/<repo>/<ref>.json`.
    `cockpit/close.py` writes a marker when the daemon is up; the daemon drains
    them each cycle through `orchestrators.teardown.teardown`. Markers older
    than `STALE_SECONDS` are pruned silently — long enough for a brief daemon
    outage, short enough that a reboot doesn't auto-close worktrees the user
    has stopped caring about.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path

from .config import COCKPIT_HOME, PID_FILE
from .teardown_types import TeardownRequest


def kick_running(*, quiet: bool = False) -> bool:
    """SIGUSR1 a running watcher. True if signalled, False otherwise.

    Differentiates failure modes so transient signal errors don't masquerade
    as "no daemon":

      - No pidfile → return False quietly.
      - Stale pidfile (ProcessLookupError) → unlink, return False quietly.
      - Corrupt pidfile (ValueError) → warn to stderr, return False.
      - Other OSError (e.g. EPERM) → surface the cause to stderr, return False.

    `quiet=True` suppresses the success print so callers (e.g. spawn.py) can
    keep their own stdout clean.
    """
    if not PID_FILE.exists():
        return False
    raw = PID_FILE.read_text().strip()
    try:
        pid = int(raw)
    except ValueError as e:
        print(f"cockpit: corrupt pidfile (raw={raw!r}): {e}", file=sys.stderr)
        return False
    try:
        os.kill(pid, signal.SIGUSR1)
    except ProcessLookupError:
        PID_FILE.unlink(missing_ok=True)
        return False
    except OSError as e:
        print(f"cockpit: cannot signal daemon pid={pid}: {e}", file=sys.stderr)
        return False
    if not quiet:
        print(f"kicked cockpit pid={pid}")
    return True


def sync(once_fn: Callable[[], int]) -> int:
    """USR1-kick a running watcher; if none, run `once_fn` inline."""
    return 0 if kick_running() else once_fn()


def stop_running() -> int:
    """SIGTERM the watcher and wait up to 5s for clean shutdown. Returns exit code."""
    if not PID_FILE.exists():
        print("no cockpit running (no pidfile)")
        return 0
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError) as e:
        print(f"unreadable pidfile: {e}", file=sys.stderr)
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        PID_FILE.unlink(missing_ok=True)
        print(f"cockpit pid={pid} was not running; removed stale pidfile")
        return 0
    deadline = time.time() + 5.0
    while time.time() < deadline and PID_FILE.exists():
        time.sleep(0.1)
    if PID_FILE.exists():
        print(
            f"sent SIGTERM to pid={pid} but pidfile still present after 5s",
            file=sys.stderr,
        )
        return 1
    print(f"stopped cockpit pid={pid}")
    return 0


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
    except (OSError, json.JSONDecodeError) as e:
        print(
            f"cockpit: skipping corrupt close-request marker {path}: {e}",
            file=sys.stderr,
        )
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
