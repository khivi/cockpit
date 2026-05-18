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
    """Right-side pills derived from Claude Code's session JSON: model, context, 5h rate, elapsed."""
    if not blob:
        return []
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        return []
    pills: list[str] = []
    model = (data.get("model") or {}).get("display_name") or ""
    model = model.split(" (")[0].strip()
    if model:
        pills.append(f"🤖 {model}")
    pills.append(_context_pill(data))
    rate = (data.get("rate_limits") or {}).get("five_hour", {}).get(
        "used_percentage"
    ) or 0
    pills.append(f"⌛ 5h {round(float(rate))}%")
    pills.append(_elapsed_pill(data))
    return pills


def _git_branch_and_dirty() -> tuple[str, int]:
    """`(branch, dirty_count)` for cwd. Branch is "" if not in a git repo."""
    here = Path(".")
    branch = current_branch(here)
    if not branch:
        return "", 0
    return branch, count_dirty(here)


def _ci_glyph(ci_raw: str) -> str:
    if ci_raw == "passed":
        return "✓"
    if ci_raw.startswith("failed"):
        return "✗"
    if ci_raw in ("pending", ""):
        return "•"
    return ci_raw


def _pr_label(match: dict) -> str:
    state = str(match.get("state") or "")
    if state != "OPEN":
        return state.lower()
    if match.get("isDraft"):
        return "draft"
    return str(match.get("review") or "").lower().replace("_", "-")


def _pr_segment(branch: str) -> str:
    """Cockpit-tracked tier's PR-info segment (no prefix, no dirty/badge)."""
    match = find_pr_payload(branch)
    if not match:
        return f"{branch} · no PR"
    ci = _ci_glyph(str(match.get("ci") or ""))
    head = f"#{match.get('number')} {branch}"
    return f"{head} · {ci} · {_pr_label(match)}"


def render_footer() -> int:
    """Two-line PR status for Claude Code's statusLine.

    Line 1: session pills — `🤖 model · 🧠 ctx · ⌛ 5h % · ⏱ elapsed` — from the
    JSON Claude Code pipes on stdin. Omitted entirely when no JSON.
    Line 2: head + dirty — `#N <branch> · ci · review` when cockpit-tracked,
    `<branch> · no PR` in any other git repo, and empty outside a git repo.
    `· ✏️ N` is appended when the worktree is dirty.

    Uses cockpit's cache only — never blocks on the network.
    """
    blob = ""
    if not sys.stdin.isatty():
        try:
            blob = sys.stdin.read()
        except OSError:
            blob = ""

    branch, dirty = _git_branch_and_dirty()
    dirty_pill = f"✏️ {dirty}" if dirty else ""

    head = _pr_segment(branch) if branch else ""
    state = repo_state(Path(".")) if branch else ""
    state_pill = {"rebase": "🔄 rebasing", "merge": "🔀 merging"}.get(state, "")

    pills_line = " · ".join(_session_pills(blob))
    head_line = " · ".join(p for p in [head, state_pill, dirty_pill] if p)
    out = "\n".join(line for line in (pills_line, head_line) if line)
    if out:
        print(out)
    return 0
