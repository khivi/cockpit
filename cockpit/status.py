"""`cockpit status` — read-only listing of the state cockpit remembers.

Answers "why is this repo missing / why was nothing sent?" for the stores
`cockpit nudge list` does not cover: parked repos, pending seed retries,
queued asks and queued close requests. Reads each store through its own
module and writes nothing, so a stale marker shows its age instead of being
pruned. A marker's age is its file mtime, which `enqueue` sets once.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from .lib import ask_queue, daemon_signal, hidden, seed_queue


def _age(path: Path, now: float) -> str:
    try:
        secs = max(0, int(now - path.stat().st_mtime))
    except OSError:
        return "age unknown"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit}"
    return f"{secs}s"


def _stale(path: Path, now: float, limit: float) -> str:
    try:
        old = now - path.stat().st_mtime > limit
    except OSError:
        return ""
    return "  (stale: the daemon drops it)" if old else ""


def render(*, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    out: list[str] = []

    parked = sorted(hidden.load_hidden())
    out.append(f"parked repos ({len(parked)}): `h` on the row un-parks")
    out += [f"  {p}" for p in parked]

    for title, items, limit in (
        ("pending seed retries", seed_queue.iter_pending(), seed_queue.STALE_SECONDS),
        ("queued asks", ask_queue.iter_pending(), ask_queue.STALE_SECONDS),
    ):
        out.append(f"{title} ({len(items)})")
        for path, req in items:
            out.append(
                f"  {req.ref}  {req.cwd or '-'}  {_age(path, now)}"
                f"{_stale(path, now, limit)}"
            )

    closes = daemon_signal.iter_pending()
    out.append(f"queued close requests ({len(closes)})")
    for cpath, creq in closes:
        kind = "forced" if creq.forced else "close"
        out.append(
            f"  {creq.repo_name or '-'}/{creq.name or creq.ref}  {kind}  {_age(cpath, now)}"
            f"{_stale(cpath, now, daemon_signal.STALE_SECONDS)}"
        )
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv in (["-h"], ["--help"]):
        print("usage: cockpit status  (read-only: parked repos and queued retries)")
        return 0
    if argv:
        print(f"cockpit status takes no arguments: {argv}", file=sys.stderr)
        return 2
    print("\n".join(render()))
    return 0
