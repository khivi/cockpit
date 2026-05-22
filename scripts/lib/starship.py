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

from . import cache as _cache
from .cache import (
    PR_CACHE_TTL_SECS,
    branch_cache,
    is_fresh,
    read_text,
    session_cache,
)
from .git import ahead_of_origin, behind_of_origin, count_status, current_branch

BASE_DISTANCE_FRESH_SECS = 30 * 60
BASE_DISTANCE_MAX_AGE_SECS = 6 * 60 * 60

SESSION_TIME_MIN_SECS = 10
LINEAR_RE = re.compile(r"[A-Z]{2,6}-[0-9]+")

_ANSI_RESET = "\033[0m"

# Claude Code's permission_mode values are camelCase; render them with the
# user-visible label they show in /config and the slash menu, hiding the
# `default` case so the pill is silent in normal use.
_PERMISSION_MODE_LABELS = {
    "plan": "plan",
    "acceptEdits": "accept-edits",
    "bypassPermissions": "bypass",
}

_PR_STATE_ANSI = {
    "DRAFT": "\033[1;38;5;240m",
    "OPEN": "\033[1;38;5;32m",
    "REVIEW_REQUIRED": "\033[1;38;5;172m",
    "APPROVED": "\033[1;38;5;34m",
    "CHANGES_REQUESTED": "\033[1;38;5;160m",
    "MERGED": "\033[1;38;5;91m",
    "CLOSED": "\033[1;38;5;88m",
}

_PR_CHECKS_ANSI = {
    "✓": "\033[32m",
    "✗": "\033[31m",
    "•": "\033[33m",
}


def _pct_tier_ansi(pct: int) -> str:
    if pct >= 100:
        return "\033[1;38;5;160m"
    if pct >= 90:
        return "\033[38;5;160m"
    if pct >= 70:
        return "\033[38;5;172m"
    return "\033[38;5;243m"


def _read_session_or_fallback(stem: str, sid: str | None) -> str:
    """Read `stem-<sid>` cache; if empty/missing and `sid` is set, fall
    back to the most recently modified `stem-*` cache.

    Claude Code's first statusLine pings on a fresh session arrive with
    `session_id` + `transcript_path` only — no `context_window` or
    `rate_limits` yet. Without a fallback the session pills disappear
    for the first few seconds of every session. Showing the previous
    session's value is honest (it's the most recent reading we have)
    and gets overwritten as soon as the new session's data arrives.
    """
    raw = read_text(session_cache(stem, sid))
    if raw or not sid:
        return raw
    try:
        candidates = [
            p
            for p in _cache.FLAT_CACHE_DIR.glob(f"{stem}-*")
            if p.is_file() and p.stat().st_size > 0
        ]
    except OSError:
        return ""
    if not candidates:
        return ""
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return read_text(latest)


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
    raw = _read_session_or_fallback("context", sid)
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
    return f"{_pct_tier_ansi(pct)}🧠 {pct}%/{ceiling}{_ANSI_RESET}"


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
    raw = _read_session_or_fallback("rate-limit-5h", sid)
    if not raw:
        return ""
    parts = raw.split()
    if not parts:
        return ""
    try:
        pct = int(parts[0])
    except ValueError:
        return ""
    return f"{_pct_tier_ansi(pct)}⌛ {pct}%/5h{_ANSI_RESET}"


def print_model(sid: str | None = None) -> str:
    sid = sid or os.environ.get("CSHIP_SESSION_ID") or None
    return _read_session_or_fallback("model", sid)


def print_permission_mode(sid: str | None = None) -> str:
    sid = sid or os.environ.get("CSHIP_SESSION_ID") or None
    raw = _read_session_or_fallback("permission-mode", sid)
    if not raw:
        return ""
    label = _PERMISSION_MODE_LABELS.get(raw)
    if not label:
        return ""
    return f"✎ {label}"


def print_branch_pill() -> str:
    """`⎇ <branch>[ ↑A][ ↓B][ +S][ ~M][ ?U]` — segments hidden when 0.
    Each segment is independently ANSI-colored; the spaces between segments
    are uncolored. Empty when not in a git repo.
    """
    cwd = os.getcwd()
    branch = current_branch(cwd)
    if not branch:
        return ""
    parts = [f"\033[38;5;243m⎇ {branch}{_ANSI_RESET}"]
    ahead = ahead_of_origin(cwd, branch)
    if ahead > 0:
        parts.append(f"\033[38;5;38m↑{ahead}{_ANSI_RESET}")
    behind = behind_of_origin(cwd, branch)
    if behind > 0:
        parts.append(f"\033[38;5;172m↓{behind}{_ANSI_RESET}")
    counts = count_status(Path(cwd))
    if counts.staged > 0:
        parts.append(f"\033[38;5;34m+{counts.staged}{_ANSI_RESET}")
    if counts.unstaged > 0:
        parts.append(f"\033[38;5;220m~{counts.unstaged}{_ANSI_RESET}")
    if counts.untracked > 0:
        parts.append(f"\033[38;5;240m?{counts.untracked}{_ANSI_RESET}")
    stale = _base_distance_segment(branch)
    if stale:
        parts.append(stale)
    return " ".join(parts)


def _base_distance_segment(branch: str) -> str:
    """Render `↻N` (with optional `(Xh ago)` dim suffix) when the cached
    base-distance is non-zero and not too stale. Returns "" otherwise.

    Tiers driven by age since the daemon's last `git fetch`:
      • <30m       → bright orange `↻N` (actionable now)
      • 30m–6h     → dim `↻N (Xh ago)` (still useful, but flagged stale)
      • >6h        → hidden (stale counts breed false confidence)

    Empty/0/unreadable cache renders nothing.
    """
    raw = read_text(branch_cache("base-distance", branch))
    if not raw:
        return ""
    parts = raw.split()
    if len(parts) != 2:
        return ""
    try:
        count = int(parts[0])
        fetch_epoch = int(parts[1])
    except ValueError:
        return ""
    if count <= 0:
        return ""
    age = int(time.time()) - fetch_epoch
    if age < 0:
        age = 0
    if age > BASE_DISTANCE_MAX_AGE_SECS:
        return ""
    if age <= BASE_DISTANCE_FRESH_SECS:
        return f"\033[38;5;172m↻{count}{_ANSI_RESET}"
    hours = max(1, age // 3600)
    return f"\033[38;5;240m↻{count} ({hours}h ago){_ANSI_RESET}"


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
    raw = _cached_or_refresh(branch, "pr-state", "pr-state")
    if not raw:
        return ""
    ansi = _PR_STATE_ANSI.get(raw)
    if not ansi:
        return raw
    return f"{ansi}{raw}{_ANSI_RESET}"


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
    glyph = _cached_or_refresh(branch, "pr-checks", "pr-checks")
    if not glyph:
        return ""
    ansi = _PR_CHECKS_ANSI.get(glyph)
    if not ansi:
        return glyph
    return f"{ansi}{glyph}{_ANSI_RESET}"


def print_pr_title(branch: str | None = None) -> str:
    branch = branch or _branch()
    if not branch:
        return ""
    return read_text(branch_cache("pr-title", branch))
