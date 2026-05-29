"""Claude Code-side: parse statusLine stdin JSON, write session caches.

Cockpit's statusLine command (`scripts/footer.py`) reads Claude Code's
JSON blob from stdin once per render and calls `stash_from_stdin` here.
Six session-scoped caches get populated:

  - `context[-$sid]`         : "<pct> <limit>"
  - `transcript-path[-$sid]` : path to the current session JSONL
  - `rate-limit-5h[-$sid]`   : "<pct> <resets_at>"
  - `model[-$sid]`           : display name with trailing "( ... )" stripped
  - `permission-mode[-$sid]` : raw mode string (default / plan / acceptEdits / bypassPermissions)
  - `cost[-$sid]`            : "<usd>" — running session spend in USD

These caches are the *only* place those Claude Code-side values can be
captured — `gh` doesn't know rate limits, the daemon can't see the
transcript path. So this module is the sole writer for the session-scoped
slice of the cship cache; the cship/PR-side writers live in `lib.cship`.

No cship binary invocation lives here. The statusline entry-point
(`scripts/footer.py`) handles the hand-off to cship after this returns.
"""

from __future__ import annotations

import calendar
import json
import re
import time

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
            if stripped:
                atomic_write(session_cache("model", sid), stripped)

    mode = data.get("permission_mode")
    if isinstance(mode, str) and mode:
        atomic_write(session_cache("permission-mode", sid), mode)

    transcript = data.get("transcript_path")
    if isinstance(transcript, str) and transcript:
        atomic_write(session_cache("transcript-path", sid), transcript)

    ctx = data.get("context_window")
    if isinstance(ctx, dict):
        pct = ctx.get("used_percentage")
        limit = ctx.get("context_window_size")
        if isinstance(pct, (int, float)) and isinstance(limit, int) and limit > 0:
            atomic_write(session_cache("context", sid), f"{int(pct)} {int(limit)}")

    cost = data.get("cost")
    if isinstance(cost, dict):
        usd = cost.get("total_cost_usd")
        if isinstance(usd, (int, float)) and usd >= 0:
            atomic_write(session_cache("cost", sid), f"{float(usd):.4f}")

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
            # cship 1.7.x parses Claude Code's JSON itself and rejects the
            # WHOLE render (blank stdout, error to stderr) if any field
            # type-mismatches its expectations — e.g. `resets_at` as an
            # ISO string when it expects u64. Coerce ISO → epoch in the
            # outgoing blob so cship can't blackout the footer.
            if isinstance(resets, str):
                epoch = _iso_to_epoch(resets)
                if epoch is not None:
                    five["resets_at"] = epoch
                    mutated = True

    if not mutated:
        return blob, sid
    return json.dumps(data).encode("utf-8"), sid


def _iso_to_epoch(ts: str) -> int | None:
    """Parse `2026-05-22T15:00:00Z` (with optional fractional seconds) to
    a UTC epoch int. Returns None on any parse failure."""
    clean = ts.split(".", 1)[0].rstrip("Z")
    try:
        return calendar.timegm(time.strptime(clean, "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return None
