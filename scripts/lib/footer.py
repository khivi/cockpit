"""Live statusLine rendering for Claude Code.

`render_footer` is what Claude Code's statusLine command invokes each reply;
it reads PR state from `lib/cache`, never touches the network.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cache import find_pr_payload
from .config import CACHE_DIR, discover_repo

TITLE_MAX = 60
LINEAR_ID_RE = re.compile(r"[A-Z]{2,6}-[0-9]+")


def _ceiling(size: int) -> str:
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
    return f"🧠 {round(float(pct))}%/{_ceiling(int(size))}"


def _find_first_timestamp(node: Any) -> str:
    """Walk a JSON-ish structure and return the first string-typed `.timestamp` value."""
    if isinstance(node, dict):
        ts = node.get("timestamp")
        if isinstance(ts, str):
            return ts
        for v in node.values():
            r = _find_first_timestamp(v)
            if r:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _find_first_timestamp(v)
            if r:
                return r
    return ""


def _elapsed_pill(data: dict) -> str:
    """`⏱ 1h 23m` from now - first transcript timestamp. Empty for <10s or missing transcript."""
    transcript = data.get("transcript_path")
    if not transcript:
        return ""
    path = Path(transcript)
    if not path.is_file():
        return ""
    first_ts = ""
    try:
        with path.open() as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                first_ts = _find_first_timestamp(obj)
                if first_ts:
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
    except (json.JSONDecodeError, ValueError, TypeError):
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


def _truncate_title(title: str) -> str:
    title = title.strip()
    if len(title) > TITLE_MAX:
        return title[: TITLE_MAX - 1].rstrip() + "…"
    return title


def _pr_segment(branch: str) -> str:
    """Cockpit-tracked tier's PR-info segment (no prefix, no dirty/badge)."""
    match = find_pr_payload(branch)
    if not match:
        return f"{branch} · {'no PR' if CACHE_DIR.is_dir() else 'no cache (run /cockpit:sync)'}"
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
    if title := _truncate_title(str(match.get("title") or "")):
        head = f'{head} "{title}"'
    return f"{head} · {ci} · {label}"


def render_footer() -> int:
    """One-line PR status for Claude Code's statusLine.

    Reads optional session JSON on stdin and enriches with model + context +
    rate-limit + elapsed. Uses cockpit's cache only — never blocks on the network.

    Segment order across all three tiers; lower tiers just omit segments:
      head · dirty · <session pills>
      where `head` is `#N <branch> "<title>" · ci · review` when Cockpit-tracked,
      `<branch>` in any other git repo, and empty outside a git repo.
    """
    blob = ""
    if not sys.stdin.isatty():
        try:
            blob = sys.stdin.read()
        except OSError:
            blob = ""

    branch, dirty = _git_branch_and_dirty()
    dirty_pill = f"✏️ {dirty}" if dirty else ""
    linear_pill = ""
    if branch and (m := LINEAR_ID_RE.search(branch)):
        linear_pill = m.group(0)

    if not branch:
        head = ""
    elif discover_repo() is None:
        head = branch
    else:
        head = _pr_segment(branch)

    parts = [head, linear_pill, dirty_pill, *_session_pills(blob)]
    line = " · ".join(p for p in parts if p)
    if line:
        print(line)
    return 0
