"""Seed bodies handed to a session by command token instead of by keystroke.

`cmux.deliver_followup` used to type the whole first-turn body into the
composer. Past roughly a thousand characters the composer collapses a burst
into `[Pasted text #N]` placeholders, a body has arrived with chunks missing
from its middle, and every newline had to be flattened since a typed newline
submits. So the body is written here and only `/cockpit-seed <id>` is typed;
the bundled `cockpit-seed.md` template expands it by running `cockpit seed
<id>`, and the body reaches the session as file content, byte for byte.

Under `$COCKPIT_RUNTIME_DIR`, not `$COCKPIT_HOME`: a body is addressed to a
workspace on this machine, like the seed queue beside it. A read never deletes
— a token re-sent by the seed retry must still resolve — so `write` prunes
instead, on a window well past `seed_queue.STALE_SECONDS`.
"""

from __future__ import annotations

import os
import re
import sys
import time
import uuid
from pathlib import Path

from .config import COCKPIT_RUNTIME_DIR

STATE_DIR = COCKPIT_RUNTIME_DIR / "seed-bodies"
COMMAND = "cockpit-seed"
# Where `config.install_claude_commands` puts the template. Absent means the
# user upgraded without re-running `cockpit setup`, and a typed token would hit
# "Unknown command" — so delivery falls back to typing the body.
COMMAND_PATH = Path.home() / ".claude" / "commands" / f"{COMMAND}.md"
RETAIN_SECONDS = 3600

# The id arrives from `$ARGUMENTS`, so it is validated before it names a file.
_ID_RE = re.compile(r"[0-9a-f]{12}")


def command_installed() -> bool:
    return COMMAND_PATH.is_file()


def token(seed_id: str) -> str:
    return f"/{COMMAND} {seed_id}"


def _prune(now: float) -> None:
    try:
        entries = list(STATE_DIR.glob("*.txt"))
    except OSError:
        return
    for path in entries:
        try:
            if now - path.stat().st_mtime > RETAIN_SECONDS:
                path.unlink()
        except OSError:
            continue


def write(text: str) -> str | None:
    """Store `text` and return its id, or None if it couldn't be written.

    None tells the caller to type the body as before: a read-only runtime dir
    must cost the token path, never the delivery.
    """
    seed_id = uuid.uuid4().hex[:12]
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _prune(time.time())
        path = STATE_DIR / f"{seed_id}.txt"
        # pid-scoped temp on `config._atomic_write_text`'s rule.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        print(f"  warn: could not store seed body: {e}", flush=True)
        return None
    return seed_id


def read(seed_id: str) -> str | None:
    if not _ID_RE.fullmatch(seed_id):
        return None
    try:
        return (STATE_DIR / f"{seed_id}.txt").read_text(encoding="utf-8")
    except OSError:
        return None


def main(argv: list[str]) -> int:
    """`cockpit seed <id>` — print a stored body for the command template.

    Always exits 0 with something on stdout: the output becomes the session's
    turn, so a miss has to explain itself rather than arrive as a blank prompt.
    """
    seed_id = argv[0].strip() if len(argv) == 1 else ""
    body = read(seed_id)
    if body is None:
        print(
            f"cockpit seed: no stored task for id {seed_id!r} — it expired or "
            "was never written. Ask the user what this session should work on."
        )
        return 0
    sys.stdout.write(body)
    return 0
