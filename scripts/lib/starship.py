"""starship field printers — readers of the flat cockpit-cache.

`scripts/defaults/starship.toml` has 8 `[custom.*]` modules; starship
spawns `scripts/starship.py <field>` once per module per render. Each
subprocess calls one `print_<field>()` here, which reads a single cache
file from `lib.cache.FLAT_CACHE_DIR` and prints a short string.

Cache layout + writers live in `lib.cache`. This module is reader-only,
plus the background-refresh fork that re-invokes `scripts/starship.py
<field>-refresh` when a PR-side cache is stale.
"""

from __future__ import annotations

import calendar
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .cache import (
    PR_CACHE_TTL_SECS,
    branch_cache,
    is_fresh,
    read_text,
    session_cache,
)
from .git import current_branch

SESSION_TIME_MIN_SECS = 10
LINEAR_RE = re.compile(r"[A-Z]{2,6}-[0-9]+")


def _branch() -> str:
    return current_branch(os.getcwd())


def _spawn_background_refresh(field: str) -> None:
    """Fire-and-forget background refresh by re-invoking
    `scripts/starship.py <field>-refresh`.

    Mirrors the historical `(refresh) >/dev/null 2>&1 &` pattern. The
    child is detached via start_new_session so it survives the parent's
    exit and starship's render budget is preserved.
    """
    starship_py = Path(__file__).resolve().parent.parent / "starship.py"
    try:
        subprocess.Popen(
            [sys.executable, str(starship_py), f"{field}-refresh"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


# ── field printers ─────────────────────────────────────────────────────────


def print_context(sid: str | None = None) -> str:
    sid = sid or os.environ.get("CSHIP_SESSION_ID") or None
    raw = read_text(session_cache("context", sid))
    if not raw:
        return ""
    parts = raw.split()
    if len(parts) != 2:
        return ""
    pct_s, limit_s = parts
    try:
        pct = int(pct_s)
        limit = int(limit_s)
    except ValueError:
        return ""
    if limit <= 0:
        return ""
    if limit >= 1_000_000:
        ceiling = "1M"
    elif limit >= 1_000:
        ceiling = f"{limit // 1000}k"
    else:
        ceiling = str(limit)
    return f"{pct}%/{ceiling}"


def print_session_time(sid: str | None = None) -> str:
    sid = sid or os.environ.get("CSHIP_SESSION_ID") or None
    transcript = read_text(session_cache("transcript-path", sid))
    if not transcript:
        return ""
    transcript_path = Path(transcript)
    if not transcript_path.is_file():
        return ""
    first_ts = _first_timestamp(transcript_path)
    if not first_ts:
        return ""
    start_epoch = _parse_iso_epoch(first_ts)
    if start_epoch is None:
        return ""
    total = int(time.time()) - start_epoch
    if total < SESSION_TIME_MIN_SECS:
        return ""
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m"
    return f"{s}s"


def _first_timestamp(transcript: Path) -> str | None:
    """Return the first `timestamp` field encountered in the transcript JSONL.

    Streams line by line — transcripts can be megabytes. Matches the
    historical `jq -rs 'map(.. | objects | .timestamp? // empty) | first'`
    behavior to a useful approximation (top-level `.timestamp` on each
    record, which is where Claude Code puts it).
    """
    try:
        with transcript.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _find_first_timestamp(rec)
                if ts:
                    return ts
    except OSError:
        return None
    return None


def _find_first_timestamp(obj) -> str | None:
    if isinstance(obj, dict):
        ts = obj.get("timestamp")
        if isinstance(ts, str) and ts:
            return ts
        for v in obj.values():
            found = _find_first_timestamp(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_first_timestamp(v)
            if found:
                return found
    return None


def _parse_iso_epoch(ts: str) -> int | None:
    """Parse an ISO 8601 timestamp into a UTC epoch seconds int.

    Strips fractional seconds and trailing 'Z' so `time.strptime` accepts
    both `2024-01-02T03:04:05Z` and `2024-01-02T03:04:05.123Z`. Uses
    `calendar.timegm` (inverse of `time.gmtime`) so the timestamp is
    interpreted as UTC regardless of the host's local timezone.
    """
    clean = ts.split(".", 1)[0].rstrip("Z")
    try:
        return calendar.timegm(time.strptime(clean, "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return None


def print_rate_limit(sid: str | None = None) -> str:
    sid = sid or os.environ.get("CSHIP_SESSION_ID") or None
    raw = read_text(session_cache("rate-limit-5h", sid))
    if not raw:
        return ""
    parts = raw.split()
    if not parts:
        return ""
    try:
        pct = int(parts[0])
    except ValueError:
        return ""
    return f"⌛ {pct}%/5h"


def print_linear() -> str:
    branch = _branch()
    if not branch:
        return ""
    m = LINEAR_RE.search(branch)
    return m.group(0) if m else ""


def _cached_or_refresh(branch: str, stem: str, field: str) -> str:
    """Return cached payload if fresh, else trigger background refresh and
    still return whatever is on disk (possibly empty / stale).
    """
    cache = branch_cache(stem, branch)
    if is_fresh(cache, PR_CACHE_TTL_SECS):
        return read_text(cache)
    _spawn_background_refresh(field)
    return read_text(cache)


def print_pr_state(branch: str | None = None) -> str:
    branch = branch or _branch()
    if not branch:
        return ""
    return _cached_or_refresh(branch, "pr-state", "pr-state")


def print_pr_num(branch: str | None = None) -> str:
    branch = branch or _branch()
    if not branch:
        return ""
    raw = read_text(branch_cache("pr-num", branch))
    if not raw or raw in ("0", "null"):
        return ""
    return f"#{raw}"


def print_pr_checks(branch: str | None = None) -> str:
    branch = branch or _branch()
    if not branch:
        return ""
    return _cached_or_refresh(branch, "pr-checks", "pr-checks")


def print_pr_title(branch: str | None = None) -> str:
    branch = branch or _branch()
    if not branch:
        return ""
    return read_text(branch_cache("pr-title", branch))
