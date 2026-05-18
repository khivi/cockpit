"""Live statusLine rendering for Claude Code.

`render_footer` is what Claude Code's statusLine command invokes each reply;
it reads PR state from `lib/cache`, never touches the network.
"""

from __future__ import annotations

import json
import subprocess
import sys

from .cache import find_pr_payload
from .config import CACHE_DIR, discover_repo


def _model_badge(blob: str) -> str:
    """Extract `🤖 Opus 4.7 · ⌛ 5h 23%` from Claude Code's session JSON. Empty if N/A."""
    if not blob:
        return ""
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError, TypeError):
        return ""
    parts: list[str] = []
    model = (data.get("model") or {}).get("display_name") or ""
    model = model.split(" (")[0].strip()
    if model:
        parts.append(f"🤖 {model}")
    rate = (data.get("rate_limits") or {}).get("five_hour", {}).get("used_percentage")
    if rate is not None:
        parts.append(f"⌛ 5h {round(float(rate))}%")
    return " · ".join(parts)


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


def _join(*parts: str) -> str:
    return " · ".join(p for p in parts if p)


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
    return f"#{match.get('number')} {branch} · {ci} · {label}"


def render_footer() -> int:
    """One-line PR status for Claude Code's statusLine.

    Reads optional session JSON on stdin and enriches with model + rate-limit.
    Uses cockpit's cache only — never blocks on the network.

    Same segment order across all three tiers; lower tiers just omit segments:
      head · dirty · badge
      where `head` is `#N <branch> · ci · review` when Cockpit-tracked,
      `<branch>` in any other git repo, and empty outside a git repo.
    """
    blob = ""
    if not sys.stdin.isatty():
        try:
            blob = sys.stdin.read()
        except OSError:
            blob = ""

    badge = _model_badge(blob)
    branch, dirty = _git_branch_and_dirty()
    dirty_pill = f"✏️ {dirty}" if dirty else ""

    if not branch:
        head = ""
    elif discover_repo() is None:
        head = branch
    else:
        head = _pr_segment(branch)

    line = _join(head, dirty_pill, badge)
    if line:
        print(line)
    return 0
