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
from .colors import (
    Colorizer,
    amber,
    azure,
    bold_azure,
    bold_crimson,
    bold_leaf,
    bold_orange,
    bold_ruby,
    bold_shadow,
    bold_violet,
    crimson,
    green,
    leaf,
    orange,
    red,
    shadow,
    slate,
    yellow,
)
from .git import ahead_of_origin, behind_of_origin, count_status, current_branch

BASE_DISTANCE_FRESH_SECS = 30 * 60
BASE_DISTANCE_MAX_AGE_SECS = 6 * 60 * 60

SESSION_TIME_MIN_SECS = 10
LINEAR_RE = re.compile(r"[A-Z]{2,6}-[0-9]+")

# Claude Code's permission_mode values are camelCase; render them with the
# user-visible label they show in /config and the slash menu, hiding the
# `default` case so the pill is silent in normal use.
_PERMISSION_MODE_LABELS = {
    "plan": "plan",
    "acceptEdits": "accept-edits",
    "bypassPermissions": "bypass",
}

_PR_STATE_COLOR: dict[str, Colorizer] = {
    "DRAFT": bold_shadow,
    "OPEN": bold_azure,
    "REVIEW_REQUIRED": bold_orange,
    "APPROVED": bold_leaf,
    "CHANGES_REQUESTED": bold_crimson,
    "MERGED": bold_violet,
    "CLOSED": bold_ruby,
}

_PR_CHECKS_COLOR: dict[str, Colorizer] = {
    "✓": green,
    "✗": red,
    "•": yellow,
}


def _pct_tier(pct: int) -> Colorizer:
    if pct >= 100:
        return bold_crimson
    if pct >= 90:
        return crimson
    if pct >= 70:
        return orange
    return slate


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
    return _pct_tier(pct)(f"🧠 {pct}%/{ceiling}")


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
    return _pct_tier(pct)(f"⌛ {pct}%/5h")


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
    """`⎇ <branch>[ ↑A][ ↗N]  [ ●S][ ✎M][ ✚U][ ↓B][ ↻N]` — segments hidden when 0.

    Layout groups branch-identity + ahead-of-* counters first, then a
    powerline-branch separator, then working-tree + sync state. Each
    segment is independently ANSI-colored; the spaces between segments
    are uncolored. Empty when not in a git repo.
    """
    cwd = os.getcwd()
    branch = current_branch(cwd)
    if not branch:
        return ""
    parts = [slate(f"⎇ {branch}")]
    ahead = ahead_of_origin(cwd, branch)
    if ahead > 0:
        parts.append(azure(f"↑{ahead}"))
    ahead_base = _base_ahead_segment(branch)
    if ahead_base:
        parts.append(ahead_base)
    parts.append(slate(""))
    counts = count_status(Path(cwd))
    if counts.staged > 0:
        parts.append(leaf(f"●{counts.staged}"))
    if counts.unstaged > 0:
        parts.append(amber(f"✎{counts.unstaged}"))
    if counts.untracked > 0:
        parts.append(shadow(f"✚{counts.untracked}"))
    behind = behind_of_origin(cwd, branch)
    if behind > 0:
        parts.append(orange(f"↓{behind}"))
    stale = _base_distance_segment(branch)
    if stale:
        parts.append(stale)
    return " ".join(parts)


def _read_base_cache(stem: str, branch: str) -> tuple[int, int] | None:
    """Read a `<count> <fetch_epoch>` base-* cache. Returns None for any
    unreadable/malformed payload or non-positive count.
    """
    raw = read_text(branch_cache(stem, branch))
    if not raw:
        return None
    parts = raw.split()
    if len(parts) != 2:
        return None
    try:
        count = int(parts[0])
        fetch_epoch = int(parts[1])
    except ValueError:
        return None
    if count <= 0:
        return None
    return count, fetch_epoch


def _render_base_segment(
    count: int, fetch_epoch: int, glyph: str, fresh_color: Colorizer
) -> str:
    """Apply the shared fresh/dim/hidden staleness ladder to a base-* count.

    Tiers driven by age since the daemon's last `git fetch`:
      • <30m       → `fresh_color <glyph>N` (actionable now)
      • 30m–6h     → dim `<glyph>N (Xh ago)` (still useful, but flagged stale)
      • >6h        → hidden (stale counts breed false confidence)
    """
    age = int(time.time()) - fetch_epoch
    if age < 0:
        age = 0
    if age > BASE_DISTANCE_MAX_AGE_SECS:
        return ""
    if age <= BASE_DISTANCE_FRESH_SECS:
        return fresh_color(f"{glyph}{count}")
    hours = max(1, age // 3600)
    return shadow(f"{glyph}{count} ({hours}h ago)")


def _base_distance_segment(branch: str) -> str:
    cached = _read_base_cache("base-distance", branch)
    if cached is None:
        return ""
    count, fetch_epoch = cached
    return _render_base_segment(count, fetch_epoch, "↻", orange)


def _base_ahead_segment(branch: str) -> str:
    cached = _read_base_cache("base-ahead", branch)
    if cached is None:
        return ""
    count, fetch_epoch = cached
    return _render_base_segment(count, fetch_epoch, "↗", azure)


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
    color = _PR_STATE_COLOR.get(raw)
    if not color:
        return raw
    return color(raw)


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
    color = _PR_CHECKS_COLOR.get(glyph)
    if not color:
        return glyph
    return color(glyph)


def print_pr_title(branch: str | None = None) -> str:
    branch = branch or _branch()
    if not branch:
        return ""
    return read_text(branch_cache("pr-title", branch))
