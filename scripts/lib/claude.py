"""Claude Code-side: parse statusLine stdin JSON, write session caches.

Cockpit's statusLine command (`scripts/footer.py`) reads Claude Code's
JSON blob from stdin once per render and calls `stash_from_stdin` here.
Three session-scoped caches get populated:

  - `context[-$sid]`         : "<pct> <limit>"
  - `transcript-path[-$sid]` : path to the current session JSONL
  - `rate-limit-5h[-$sid]`   : "<pct> <resets_at>"

These caches are the *only* place those Claude Code-side values can be
captured — `gh` doesn't know rate limits, the daemon can't see the
transcript path. So this module is the sole writer for the session-scoped
slice of the cship cache; the cship/PR-side writers live in `lib.cship`.

No cship binary invocation lives here. The statusline entry-point
(`scripts/footer.py`) handles the hand-off to cship after this returns.
"""

from __future__ import annotations

import json
import re

from .cache import atomic_write, session_cache


def stash_from_stdin(blob: bytes) -> tuple[bytes, str | None]:
    """Parse Claude Code's statusLine stdin JSON, populate session caches,
    return `(mutated_blob, session_id)`.

    Mutations on the JSON before it's handed downstream:
      - strip trailing " (...)" suffix from `model.display_name`
        (e.g. "Opus 4.7 (1M context)" → "Opus 4.7") so cship's display
        module shows the bare model name.

    Returns the original bytes when nothing was mutated, so callers that
    pipe to cship don't pay a re-encode cost per render. Never raises —
    malformed input yields `(blob, None)` and writes no caches.
    """
    if not blob:
        return blob, None
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return blob, None
    if not isinstance(data, dict):
        return blob, None

    sid = data.get("session_id") if isinstance(data.get("session_id"), str) else None

    mutated = False
    model = data.get("model")
    if isinstance(model, dict):
        name = model.get("display_name")
        if isinstance(name, str):
            stripped = re.sub(r"\s*\([^)]*\)\s*$", "", name)
            if stripped != name:
                model["display_name"] = stripped
                mutated = True

    transcript = data.get("transcript_path")
    if isinstance(transcript, str) and transcript:
        atomic_write(session_cache("transcript-path", sid), transcript)

    ctx = data.get("context_window")
    if isinstance(ctx, dict):
        pct = ctx.get("used_percentage")
        limit = ctx.get("context_window_size")
        if isinstance(pct, (int, float)) and isinstance(limit, int) and limit > 0:
            atomic_write(session_cache("context", sid), f"{int(pct)} {int(limit)}")

    rate_limits = data.get("rate_limits")
    if isinstance(rate_limits, dict):
        five = rate_limits.get("five_hour")
        if isinstance(five, dict):
            pct = five.get("used_percentage")
            resets = five.get("resets_at")
            if isinstance(pct, (int, float)) and resets not in (None, ""):
                atomic_write(
                    session_cache("rate-limit-5h", sid),
                    f"{int(round(pct))} {resets}",
                )

    if not mutated:
        return blob, sid
    return json.dumps(data).encode("utf-8"), sid
