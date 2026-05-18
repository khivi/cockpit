"""Live statusLine rendering for Claude Code.

`render_footer` is what Claude Code's statusLine command invokes each reply;
it reads PR state from `lib/cache`, never touches the network.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .cache import find_pr_payload


def _size_label(size: int) -> str:
    if size >= 1_000_000:
        return "1M"
    if size >= 1_000:
        return f"{size // 1_000}k"
    return str(size)


def _context_pill(data: dict) -> str:
    """`🧠 4%/1M` from `context_window` block. Empty if absent."""
    ctx = data.get("context_window") or {}
    pct = ctx.get("used_percentage")
    size = ctx.get("context_window_size")
    if pct is None or not size:
        return ""
    return f"🧠 {round(float(pct))}%/{_size_label(int(size))}"


def _elapsed_pill(data: dict) -> str:
    """`⏱ 1h 23m` from now - first transcript timestamp.

    Empty when: no `transcript_path`, file missing, unreadable, no parsable
    top-level `timestamp` on any entry, unparsable ISO string, or elapsed < 10s.
    """
    transcript = data.get("transcript_path")
    if not transcript:
        return ""
    path = Path(transcript)
    if not path.is_file():
        return ""
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
        return ""
    if not first_ts:
        return ""
    try:
        start = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    total = int((datetime.now(timezone.utc) - start).total_seconds())
    if total < 10:
        return ""
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
    if ctx := _context_pill(data):
        pills.append(ctx)
    rate = (data.get("rate_limits") or {}).get("five_hour", {}).get("used_percentage")
    if rate is not None:
        pills.append(f"⌛ 5h {round(float(rate))}%")
    if elapsed := _elapsed_pill(data):
        pills.append(elapsed)
    return pills


def _git_branch_and_dirty() -> tuple[str, int]:
    """`(branch, dirty_count)` for cwd. Branch is "" if not in a git repo."""
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    if not branch:
        return "", 0
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True
    ).stdout
    return branch, sum(1 for row in porcelain.splitlines() if row)


def _pr_segment(branch: str) -> str:
    """Cockpit-tracked tier's PR-info segment (no prefix, no dirty/badge)."""
    match = find_pr_payload(branch)
    if not match:
        return f"{branch} · no PR"
    ci_raw = str(match.get("ci") or "")
    ci = (
        "✓"
        if ci_raw == "passed"
        else (
            "✗"
            if ci_raw.startswith("failed")
            else "•" if ci_raw in ("pending", "") else ci_raw
        )
    )
    state = str(match.get("state") or "")
    if state == "OPEN" and match.get("isDraft"):
        label = "draft"
    elif state == "OPEN":
        label = str(match.get("review") or "").lower().replace("_", "-")
    else:
        label = state.lower()
    head = f"#{match.get('number')} {branch}"
    return f"{head} · {ci} · {label}"


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

    if os.getenv("COCKPIT_FOOTER_DEBUG"):
        try:
            Path("/tmp/cockpit_footer_stdin.json").write_text(blob)
        except Exception:
            pass

    branch, dirty = _git_branch_and_dirty()
    dirty_pill = f"✏️ {dirty}" if dirty else ""

    head = _pr_segment(branch) if branch else ""

    pills_line = " · ".join(_session_pills(blob))
    head_line = " · ".join(p for p in [head, dirty_pill] if p)
    out = "\n".join(line for line in (pills_line, head_line) if line)
    if out:
        print(out)
    return 0
