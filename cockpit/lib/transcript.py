"""Is a Claude Code turn in flight at a worktree? — answered from Claude Code's
own transcript rather than from the backend's opinion of it.

The one question `_screen_signals_idle` cannot answer from the screen. Claude
Code's insert-mode indicator tracks *composer focus*, not turn state: the
composer keeps focus (and accepts type-ahead) while a turn runs, so a mid-turn
screen is indistinguishable from an at-rest one. Measured against a live
session, not inferred — the indicator was present throughout a running turn,
while `claude_code=Running`.

What the screen *can* see is a pending choice: a permission prompt or an
`AskUserQuestion` takes focus off the composer, so the indicator disappears and
the dialog's own footer appears. Those checks stay where they are; this module
only covers the axis they are blind to.

Claude Code appends a JSONL record per message under
`~/.claude/projects/<slug>/<session-id>.jsonl`, so a turn is in flight exactly
when some `tool_use` id has no answering `tool_result`. That is structural — no
prose, no terminal chrome — and it is Claude Code's own durable record, so it
holds identically under limux or with no backend at all.

The format is nonetheless undocumented, which is why `turn_in_flight` reports
`None` rather than guessing and its one caller treats anything but a definite
`False` as a refusal.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .cache import CLAUDE_PROJECTS_DIR, _claude_project_slug

# How recently a transcript must have been written to count as a session worth
# asking about. A session killed mid-tool-call leaves its `tool_use`
# unanswered forever, which would otherwise refuse the heal at that worktree
# for good — including for a later session that reuses the directory. Generous
# on purpose: a tool call legitimately runs for minutes, and reading a slow
# call as "finished" is the failure this module exists to prevent.
LIVE_WINDOW_SECONDS = 86_400.0


def _session_files(cwd: os.PathLike[str] | str) -> list[Path]:
    """Every recently-written transcript for `cwd`, newest first.

    Deliberately not "the newest one": a `use_worktree: false` repo hosts
    several sessions at one cwd, and picking by mtime reads the wrong session's
    state. Observed, not hypothetical — two live sessions in one worktree, and
    the mtime pick reported at-rest while the other was mid-turn.
    """
    proj = CLAUDE_PROJECTS_DIR / _claude_project_slug(cwd)
    cutoff = time.time() - LIVE_WINDOW_SECONDS
    try:
        recent = [p for p in proj.glob("*.jsonl") if p.stat().st_mtime > cutoff]
    except OSError:
        return []
    return sorted(recent, key=lambda p: p.stat().st_mtime, reverse=True)


def _has_open_tool_use(path: Path) -> bool | None:
    """Whether `path` ends with a `tool_use` no `tool_result` has answered.

    None when the file can't be read at all. A single unparsable line is
    skipped rather than failing the file — the transcript is appended live, so
    the last line can be half-written when we read it.
    """
    issued: set[str] = set()
    answered: set[str] = set()
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                content = (record.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    # An id-less block pairs with nothing, so it can neither
                    # open nor close a call; ignoring it keeps a malformed
                    # record from deciding the verdict either way.
                    kind = block.get("type")
                    if kind == "tool_use" and isinstance(block.get("id"), str):
                        issued.add(block["id"])
                    elif kind == "tool_result" and isinstance(
                        block.get("tool_use_id"), str
                    ):
                        answered.add(block["tool_use_id"])
    except OSError:
        return None
    return bool(issued - answered)


def turn_in_flight(cwd: os.PathLike[str] | str) -> bool | None:
    """True when a Claude turn is running at `cwd`, False when none is, None
    when it cannot be told.

    None is the fail-open answer and is deliberately distinct from False, on
    `cmux._screen_shows`' rule: a caller that cannot see must not read silence
    as evidence. Here that means no transcript inside `LIVE_WINDOW_SECONDS`
    (nothing has run here recently enough to have an opinion) or an unreadable
    file.

    Any one session in flight answers for the whole worktree, since a send is
    addressed to the workspace rather than to a session.
    """
    files = _session_files(cwd)
    if not files:
        return None
    verdicts = [_has_open_tool_use(p) for p in files]
    if any(v for v in verdicts):
        return True
    if any(v is None for v in verdicts):
        return None
    return False
