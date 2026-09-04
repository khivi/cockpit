"""cmux diff-viewer review comments, collected so they can actually be delivered.

cmux's diff viewer (what `cockpit diff` opens) lets you leave line-anchored
comments, and its own composer folds them into the next message you submit —
"Diff review comments are included when you submit". A cockpit-spawned workspace
has no such composer: it is a `type: terminal` surface running Claude Code's own
TUI, whose input belongs to Claude, not to cmux. So the comments were written and
nothing ever read them. This module is the missing collector.

`cockpit diff --comments` prints them for the session standing in the worktree,
and `cockpit diff --ack` retires them once they are addressed. Reading and
retiring are separate calls on purpose: a turn that dies between the two leaves
the notes pending rather than losing review feedback that exists nowhere else.

**The store is keyed by repo root**, at
`~/Library/Application Support/cmux/diff-comments/<sha256(repoRoot)[:24]>.json`,
which is why `cmux.render_diff` sets both `--cwd` and the subprocess cwd: left to
an inherited cwd, comments are filed under whatever repo the caller happened to
be launched in. Lookup here matches on each file's own `repoRoot` field rather
than recomputing that digest, so the filename scheme is cmux's business and a
change to it costs us nothing.

**Read-only on cmux's store, and that is deliberate.** Consuming a delivered
comment by rewriting cmux's file is the obvious alternative and it means racing
a writer we do not control for a value we do not own; a lost update there
resends, which is the same failure we were trying to prevent. So delivery is
recorded on our own side instead, in `$COCKPIT_RUNTIME_DIR` — the *runtime* dir
rather than `$COCKPIT_HOME/state` because a comment id names a cmux object on
this machine, and `$COCKPIT_HOME` is commonly a synced folder where a foreign
machine's ids are noise. The ledger is one uuid per comment ever delivered and
is never pruned: a few hundred bytes a year, against a scan-and-intersect pass
that would have to run on every send.

Everything here fails **open** — an unreadable store or ledger yields "no
pending comments" and an unwritable ledger costs one duplicate delivery. The
alternative is a send that raises, and the message is what the user actually
came for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import COCKPIT_RUNTIME_DIR

STORE_DIR = Path.home() / "Library" / "Application Support" / "cmux" / "diff-comments"
DELIVERED = COCKPIT_RUNTIME_DIR / "diff-comments-delivered.json"


@dataclass(frozen=True)
class Comment:
    """One line-anchored remark, reduced to what a prompt needs."""

    id: str
    file: str
    line: int
    message: str


def _roots(paths) -> set[str]:
    out = set()
    for p in paths:
        if not p:
            continue
        try:
            out.add(str(Path(p).resolve()))
        except OSError:
            continue
    return out


def _delivered() -> set[str]:
    try:
        return set(json.loads(DELIVERED.read_text()))
    except (OSError, ValueError, TypeError):
        return set()


def pending_by_root(roots: set[str]) -> dict[str, list[Comment]]:
    """Undelivered comments filed against any of `roots`, bucketed by their own
    `repoRoot` and each bucket sorted oldest-first.

    One glob of `STORE_DIR` regardless of how many roots are asked for — the
    batch form `_fast_tick` uses to cost one directory scan per repo rather
    than one per worktree, since every worktree of a repo shares the same
    handful of candidate roots. `pending()` below is the single-root case,
    kept as the thin wrapper every other caller already uses.
    """
    seen = _delivered()
    if not roots or not STORE_DIR.is_dir():
        return {}
    out: dict[str, list[Comment]] = {}
    for path in sorted(STORE_DIR.glob("*.json")):
        try:
            blob = json.loads(path.read_text())
            root = str(Path(blob.get("repoRoot", "")).resolve())
        except (OSError, ValueError, TypeError):
            continue
        if root not in roots:
            continue
        bucket = out.setdefault(root, [])
        for c in blob.get("comments") or []:
            cid, message = c.get("id"), (c.get("message") or "").strip()
            # An id-less or empty comment is nothing to deliver and nothing we
            # could record having delivered.
            if not cid or not message or cid in seen:
                continue
            bucket.append(
                Comment(
                    id=cid,
                    file=c.get("filePath") or "?",
                    line=c.get("startLine") or 0,
                    message=message,
                )
            )
    for bucket in out.values():
        bucket.sort(key=lambda c: (c.file, c.line))
    return out


def pending(paths) -> list[Comment]:
    """Undelivered comments filed against any of `paths`, oldest first.

    `paths` is a small set of candidate repo roots rather than one, because
    which root cmux records for a *worktree* is not documented — its own
    toplevel (what `--cwd` implies) or the main checkout it was cut from. Both
    are cheap to offer, and the cost of guessing wrong is a key that silently
    delivers nothing.
    """
    roots = _roots(paths)
    out = [c for bucket in pending_by_root(roots).values() for c in bucket]
    out.sort(key=lambda c: (c.file, c.line))
    return out


def mark_delivered(ids) -> None:
    """Record ids as sent, so the next `a` doesn't repeat them. Never raises."""
    ids = [i for i in ids if i]
    if not ids:
        return
    try:
        DELIVERED.parent.mkdir(parents=True, exist_ok=True)
        DELIVERED.write_text(json.dumps(sorted(_delivered() | set(ids))))
    except OSError:
        pass
