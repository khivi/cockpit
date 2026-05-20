"""Live statusLine rendering for Claude Code.

`render_footer` is what Claude Code's statusLine command invokes each reply;
it reads PR state from `lib/cache`, never touches the network.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .cache import find_pr_payload
from .git import count_dirty, current_branch, repo_state


_ANSI_RED = "\033[31m"
_ANSI_AMBER = "\033[33m"
_ANSI_RESET = "\033[0m"


def _size_label(size: int) -> str:
    if size >= 1_000_000:
        return "1M"
    if size >= 1_000:
        return f"{size // 1_000}k"
    return str(size)


def _context_pill(data: dict) -> str:
    """`🧠 4%/1M` from `context_window` block. Defaults pct=0, size=200000."""
    ctx = data.get("context_window") or {}
    pct = ctx.get("used_percentage") or 0
    size = ctx.get("context_window_size") or 200000
    return f"🧠 {round(float(pct))}%/{_size_label(int(size))}"


def _five_hour_pill(rate: float) -> str:
    """`⌛ 5h N%` with amber ≥60 and red ≥80 ANSI coloring."""
    label = f"⌛ 5h {round(rate)}%"
    if rate >= 80:
        return f"{_ANSI_RED}{label}{_ANSI_RESET}"
    if rate >= 60:
        return f"{_ANSI_AMBER}{label}{_ANSI_RESET}"
    return label


def _clock_pill() -> str:
    """Wall-clock `🕐 HH:MM`. Ticks per Claude Code turn, not per minute."""
    return datetime.now().strftime("🕐 %H:%M")


def _elapsed_pill(data: dict) -> str:
    """`⏱ 1h 23m` from now - first transcript timestamp. Falls back to `⏱ 0s`
    when no transcript or unparsable, so the pill is always present."""
    fallback = "⏱ 0s"
    transcript = data.get("transcript_path")
    if not transcript:
        return fallback
    path = Path(transcript)
    if not path.is_file():
        return fallback
    first_ts = ""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    ts = obj.get("timestamp")
                    if isinstance(ts, str):
                        first_ts = ts
                        break
    except OSError:
        return fallback
    if not first_ts:
        return fallback
    try:
        start = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    total = max(0, int((datetime.now(timezone.utc) - start).total_seconds()))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"⏱ {h}h {m}m"
    if m:
        return f"⏱ {m}m"
    return f"⏱ {s}s"


def _session_pills(blob: str) -> list[str]:
    """Right-side pills derived from Claude Code's session JSON.

    Order: clock · model · context · 5h · elapsed. Clock is leftmost so the
    eye can find it without scanning past variable-width pills.
    """
    if not blob:
        return []
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        return []
    pills: list[str] = [_clock_pill()]
    model = (data.get("model") or {}).get("display_name") or ""
    model = model.split(" (")[0].strip()
    if model:
        pills.append(f"🤖 {model}")
    pills.append(_context_pill(data))
    rate = (data.get("rate_limits") or {}).get("five_hour", {}).get(
        "used_percentage"
    ) or 0
    pills.append(_five_hour_pill(float(rate)))
    pills.append(_elapsed_pill(data))
    return pills


def _git_branch_and_dirty() -> tuple[str, int]:
    """`(branch, dirty_count)` for cwd. Branch is "" if not in a git repo."""
    here = Path(".")
    branch = current_branch(here)
    if not branch:
        return "", 0
    return branch, count_dirty(here)


_FOOTER_RENDERERS = {
    "rebase": lambda _p: "🔄 rebasing",
    "merge": lambda _p: "🔀 merging",
    "wip": lambda p: f"✏️ {p['count']}",
    "ci_failed": lambda p: f"✗ {p['phase']}" if p.get("phase") else "✗",
    "ci_pending": lambda _p: "⏳ ci",
    "unaddressed": lambda p: f"💬 {p['count']}",
    "changes_requested": lambda _p: "changes-requested",
    "conflict": lambda _p: "⚠️ conflict",
    "draft": lambda _p: "draft",
    "approved": lambda _p: "approved",
    "state": lambda p: str(p.get("state", "")).lower(),
}


def _legacy_pr_segment(branch: str, match: dict) -> str:
    """Render from raw cache fields when `pills` is missing (pre-0.3.0 cache).

    Self-heals on the next daemon cycle; this branch only fires during the
    first cycle after upgrade.
    """
    ci_raw = str(match.get("ci") or "")
    if ci_raw == "passed":
        ci = "✓"
    elif ci_raw.startswith("failed"):
        ci = "✗"
    elif ci_raw in ("pending", ""):
        ci = "•"
    else:
        ci = ci_raw
    state = str(match.get("state") or "")
    if state != "OPEN":
        label = state.lower()
    elif match.get("isDraft"):
        label = "draft"
    else:
        label = str(match.get("review") or "").lower().replace("_", "-")
    return f"#{match.get('number')} {branch} · {ci} · {label}"


def _pr_segment(branch: str) -> str:
    """Cockpit-tracked tier's PR-info segment (no prefix, no dirty/badge).

    Reads the `pills` array written by the daemon. Each pill kind maps to a
    text token via `_FOOTER_RENDERERS`; unknown kinds are skipped so future
    daemon-side additions don't crash an older footer.
    """
    match = find_pr_payload(branch)
    if not match:
        return f"{branch} · no PR"
    head = f"#{match.get('number')} {branch}"
    if "pills" not in match:
        return _legacy_pr_segment(branch, match)
    parts: list[str] = [head]
    for p in match.get("pills") or []:
        kind = p.get("kind")
        renderer = _FOOTER_RENDERERS.get(kind) if kind else None
        if renderer is None:
            continue
        text = renderer(p)
        if text:
            parts.append(text)
    return " · ".join(parts)


def render_footer() -> int:
    """Two-line PR status for Claude Code's statusLine.

    Line 1: session pills — `🕐 clock · 🤖 model · 🧠 ctx · ⌛ 5h % · ⏱ elapsed`
    — from the JSON Claude Code pipes on stdin. Omitted entirely when no JSON.
    Line 2: head + pills — `#N <branch> · <pill> · <pill>...` when cockpit-
    tracked, `<branch> · no PR` in any other git repo, and empty outside a
    git repo. `· ✏️ N` (live `count_dirty`) is appended when the worktree is
    dirty so footer reflects edits instantly; the cached `wip` pill is
    suppressed in that case.

    Uses cockpit's cache only — never blocks on the network.
    """
    blob = ""
    if not sys.stdin.isatty():
        try:
            blob = sys.stdin.read()
        except OSError:
            blob = ""

    branch, dirty = _git_branch_and_dirty()
    match = find_pr_payload(branch) if branch else None
    head = _pr_segment(branch) if branch else ""
    state = repo_state(Path(".")) if branch else ""
    state_pill = {"rebase": "🔄 rebasing", "merge": "🔀 merging"}.get(state, "")

    # Cached `wip` pill (in `head`) covers tracked repos. Live dirty fallback
    # only for untracked branches so something always renders when editing.
    dirty_pill = f"✏️ {dirty}" if dirty and not match else ""

    pills_line = " · ".join(_session_pills(blob))
    head_line = " · ".join(p for p in [head, state_pill, dirty_pill] if p)
    out = "\n".join(line for line in (pills_line, head_line) if line)
    if out:
        print(out)
    return 0
