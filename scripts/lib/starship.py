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
import time
from pathlib import Path

from . import cache as _cache
from .cache import (
    branch_cache,
    cwd_cache,
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
from .git import GitStatusCounts
from .linear import extract_ticket

SESSION_TIME_MIN_SECS = 10
POWERLINE_BRANCH = ""  # nf-pl-branch separator (Nerd Font powerline)

# Statusline glyphs. One module-level constant per icon so a rename or palette
# refresh has a single touch point. cship pills (cmux.py) follow the same pattern.
ICON_CONTEXT = "🧠"  # 🧠 context-usage gauge
ICON_SESSION = "⌛"  # ⌛ session 5h-bucket usage
ICON_BRANCH = "⎇"  # ⎇ current branch
ICON_AHEAD_ORIGIN = "↑"  # ↑ commits ahead of origin
ICON_AHEAD_BASE = "↗"  # ↗ commits ahead of base
ICON_BEHIND_ORIGIN = "↓"  # ↓ commits behind origin
ICON_BEHIND_BASE = "↻"  # ↻ rebase-staleness vs base
ICON_STAGED = "●"  # ● staged file count
ICON_UNSTAGED = "✎"  # ✎ unstaged modifications
ICON_UNTRACKED = "✚"  # ✚ untracked files
ICON_PERMISSION_MODE = "✎"  # ✎ permission-mode label (same glyph as unstaged)

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

_PR_STATE_ICON: dict[str, str] = {
    "DRAFT": "📝",
    "OPEN": "🔵",
    "REVIEW_REQUIRED": "👀",
    "APPROVED": "✅",
    "CHANGES_REQUESTED": "💬",
    "MERGED": "🟣",
    "CLOSED": "⛔",
}

ICON_PR_NUM = "🔗"
ICON_PR_TITLE = "📄"
ICON_PR_MUTED = "🔇"
ICON_PR_COMMENTS = "💬"
ICON_COST = "💰"  # 💰 running session spend in USD

_PR_CHECKS_COLOR: dict[str, Colorizer] = {
    "✓": green,
    "✗": red,
    "•": orange,
    "?": red,
}


def _pct_tier(pct: int) -> Colorizer:
    if pct >= 100:
        return bold_crimson
    if pct >= 90:
        return crimson
    if pct >= 70:
        return orange
    return slate


def _cost_tier(usd: float) -> Colorizer:
    if usd >= 10.0:
        return bold_crimson
    if usd >= 5.0:
        return crimson
    if usd >= 2.0:
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
    return _git_state(os.getcwd())[0]


def _git_state(cwd: str) -> tuple[str, GitStatusCounts, int, int]:
    """Cached `(branch, status_counts, ahead_origin, behind_origin)` for `cwd`.

    Cell layout — written exclusively by the daemon (slow tick in
    `_write_pr_caches`, fast tick in `cockpit._fast_tick`, both via
    `cache.write_git_state_cache`):
      • `git-branch-<cwd-slug>` — branch name (or empty when not a repo)
      • `git-status-<cwd-slug>` — `"<staged> <unstaged> <untracked>"`
      • `git-sync-<cwd-slug>`   — `"<ahead_origin> <behind_origin>"`

    The renderer is strictly read-only — it never spawns a git subprocess.
    When the daemon hasn't yet populated a worktree's cells (first render
    of a brand-new worktree before the next tick), the footer renders
    blank for that worktree's git segments; the daemon's fast loop
    (`fast_poll_interval_seconds`, default 30s) catches up shortly.

    Empty `branch` cell means "not a repo" (daemon wrote empty after cwd
    left a repo); counts and sync zero out so callers
    (`print_branch_identity`, `print_worktree_status`, `print_linear`) can
    short-circuit on `not branch`.
    """
    branch = read_text(cwd_cache("git-branch", cwd))
    if not branch:
        return "", GitStatusCounts(0, 0, 0), 0, 0
    counts = _parse_status_counts(read_text(cwd_cache("git-status", cwd)))
    ahead, behind = _parse_sync(read_text(cwd_cache("git-sync", cwd)))
    return branch, counts, ahead, behind


def _parse_status_counts(raw: str) -> GitStatusCounts:
    parts = raw.split()
    if len(parts) != 3:
        return GitStatusCounts(0, 0, 0)
    try:
        return GitStatusCounts(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return GitStatusCounts(0, 0, 0)


def _parse_sync(raw: str) -> tuple[int, int]:
    parts = raw.split()
    if len(parts) != 2:
        return 0, 0
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0


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
    return _pct_tier(pct)(f"{ICON_CONTEXT} {pct}%/{ceiling}")


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
    return _pct_tier(pct)(f"{ICON_SESSION} {pct}%/5h")


def print_model(sid: str | None = None) -> str:
    sid = sid or os.environ.get("CSHIP_SESSION_ID") or None
    return _read_session_or_fallback("model", sid)


def print_cost(sid: str | None = None) -> str:
    sid = sid or os.environ.get("CSHIP_SESSION_ID") or None
    raw = _read_session_or_fallback("cost", sid)
    if not raw:
        return ""
    try:
        usd = float(raw)
    except ValueError:
        return ""
    if usd <= 0:
        return ""
    return _cost_tier(usd)(f"{ICON_COST} ${usd:.2f}")


def print_permission_mode(sid: str | None = None) -> str:
    sid = sid or os.environ.get("CSHIP_SESSION_ID") or None
    raw = _read_session_or_fallback("permission-mode", sid)
    if not raw:
        return ""
    label = _PERMISSION_MODE_LABELS.get(raw)
    if not label:
        return ""
    return f"{ICON_PERMISSION_MODE} {label}"


def print_branch_identity() -> str:
    """`⎇ <branch>[ ↑A][ ↗N]` — branch + ahead-of-origin + ahead-of-base.

    Empty when not in a git repo. Inter-segment spacing belongs to TOML;
    this emits only its own content.
    """
    branch, _, ahead, _ = _git_state(os.getcwd())
    if not branch:
        return ""
    parts = [slate(f"{ICON_BRANCH} {branch}")]
    if ahead > 0:
        parts.append(azure(f"{ICON_AHEAD_ORIGIN}{ahead}"))
    ahead_base = _base_ahead_segment(branch)
    if ahead_base:
        parts.append(ahead_base)
    return " ".join(parts)


def print_worktree_status() -> str:
    """`<sep> [●S] [✎M] [✚U] [↓B] [↻N]` — working-tree + sync state.

    Empty (the empty string, not the separator alone) when nothing to show,
    so the calling TOML segment's `format = "(   $output)"` collapses to
    nothing. The leading powerline-branch separator pins this segment
    visually to the preceding `[custom.branch_identity]` segment.
    """
    branch, counts, _, behind = _git_state(os.getcwd())
    if not branch:
        return ""
    parts: list[str] = []
    if counts.staged > 0:
        parts.append(leaf(f"{ICON_STAGED}{counts.staged}"))
    if counts.unstaged > 0:
        parts.append(amber(f"{ICON_UNSTAGED}{counts.unstaged}"))
    if counts.untracked > 0:
        parts.append(shadow(f"{ICON_UNTRACKED}{counts.untracked}"))
    if behind > 0:
        parts.append(orange(f"{ICON_BEHIND_ORIGIN}{behind}"))
    stale = _base_distance_segment(branch)
    if stale:
        parts.append(stale)
    if not parts:
        return ""
    return slate(POWERLINE_BRANCH) + " " + " ".join(parts)


def _base_distance_segment(branch: str) -> str:
    raw = read_text(branch_cache("base-distance", branch))
    if not raw:
        return ""
    try:
        count = int(raw)
    except ValueError:
        return ""
    if count <= 0:
        return ""
    return orange(f"{ICON_BEHIND_BASE}{count}")


def _base_ahead_segment(branch: str) -> str:
    raw = read_text(branch_cache("base-ahead", branch))
    if not raw:
        return ""
    try:
        count = int(raw)
    except ValueError:
        return ""
    if count <= 0:
        return ""
    return azure(f"{ICON_AHEAD_BASE}{count}")


def print_linear() -> str:
    return extract_ticket(_branch())


def print_pr_state(branch: str | None = None) -> str:
    branch = branch or _branch()
    if not branch:
        return ""
    raw = read_text(branch_cache("pr-state", branch))
    if not raw:
        return ""
    icon = _PR_STATE_ICON.get(raw, "")
    label = f"{icon} {raw}" if icon else raw
    color = _PR_STATE_COLOR.get(raw)
    if not color:
        return label
    return color(label)


def print_pr_num(branch: str | None = None) -> str:
    branch = branch or _branch()
    if not branch:
        return ""
    raw = read_text(branch_cache("pr-num", branch))
    if not raw or raw in ("0", "null"):
        return ""
    return f"{ICON_PR_NUM} #{raw}"


def print_pr_comments(branch: str | None = None) -> str:
    branch = branch or _branch()
    if not branch:
        return ""
    raw = read_text(branch_cache("pr-comments", branch))
    if not raw:
        return ""
    try:
        count = int(raw)
    except ValueError:
        return ""
    if count <= 0:
        return ""
    return red(f"{ICON_PR_COMMENTS} {count}")


def print_pr_checks(branch: str | None = None) -> str:
    branch = branch or _branch()
    if not branch:
        return ""
    glyph = read_text(branch_cache("pr-checks", branch))
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
    raw = read_text(branch_cache("pr-title", branch))
    if not raw:
        return ""
    return f"{ICON_PR_TITLE} {raw}"


def print_pr_muted(branch: str | None = None) -> str:
    """Render the daemon's mute snapshot. Reader-only — see write_pr_cache."""
    branch = branch or _branch()
    if not branch:
        return ""
    raw = read_text(branch_cache("pr-muted", branch))
    if not raw:
        return ""
    if raw == "all":
        return yellow(f"{ICON_PR_MUTED} muted")
    return yellow(f"{ICON_PR_MUTED} muted: {raw.replace(',', '+')}")
