"""The brief (`d`, "Day"): where to start today, and what changed since yesterday.

The PR cache holds only the present, so "what changed" needs one remembered
state. The slow tick records a digest of every cached PR as `last`; on the first
record of a new day the previous `last` becomes `baseline`, so the brief diffs
against the state at your final tick of the previous day, overnight included.

A diff baseline and nothing else: no decision, cell, pill or send reads it.
Machine-local in `$COCKPIT_RUNTIME_DIR`, like `seed_queue`, so two machines on
a synced home don't roll each other's baseline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import COCKPIT_RUNTIME_DIR, _atomic_write_text

STATE_PATH = COCKPIT_RUNTIME_DIR / "brief.json"

_FIELDS = ("title", "state", "ci", "review", "total", "snoozed")


@dataclass(frozen=True)
class Change:
    key: str
    title: str
    what: str


def repo_and_number(key: str) -> tuple[str, str]:
    """`owner_name__pr-12` → (`owner/name`, `12`). GitHub logins never hold `_`,
    so the first one is the slug's `/`."""
    slug, _, number = key.rpartition("__pr-")
    return slug.replace("_", "/", 1), number


def digest(payloads: dict[str, dict]) -> dict[str, dict]:
    return {k: {f: p.get(f) for f in _FIELDS} for k, p in payloads.items()}


def load(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or STATE_PATH).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def ticket_states(
    inboxes: dict[str, list[dict]], payloads: dict[str, dict]
) -> dict[str, str]:
    """`{ticket id: state}` across the inbox and every PR's delivered tickets."""
    out: dict[str, str] = {}
    blocks = [(p.get("ticket") or {}).get("tickets") for p in payloads.values()]
    for tickets in [*inboxes.values(), *blocks]:
        for t in tickets if isinstance(tickets, list) else []:
            if isinstance(t, dict) and t.get("id"):
                out[str(t["id"])] = str(t.get("state") or "")
    return out


def record(
    payloads: dict[str, dict],
    today: str,
    path: Path | None = None,
    *,
    at: str = "",
    tickets: dict[str, str] | None = None,
) -> None:
    path = path or STATE_PATH
    state = load(path)
    last = state.get("last")
    if isinstance(last, dict) and last.get("date") != today:
        state["baseline"] = last
    state["last"] = {
        "date": today,
        "at": at,
        "prs": digest(payloads),
        "tickets": tickets or {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, json.dumps(state))


def live(payloads: dict[str, dict], state: dict) -> dict[str, dict]:
    """The payloads the last full cycle saw open in a configured repo. The disk
    cache also holds merged PRs and repos since dropped or parked."""
    last = state.get("last")
    prs = last.get("prs") if isinstance(last, dict) else None
    if not isinstance(prs, dict):
        return payloads
    return {k: p for k, p in payloads.items() if k in prs}


def open_keys(open_prs: dict[str, dict[int, str]]) -> set[str]:
    """Cache-file stems for `{owner/name: {number: title}}`. Files are named by
    the bare repo name; keys without an owner are config labels and skipped."""
    return {
        f"{r.partition('/')[2]}__pr-{n}"
        for r, prs in open_prs.items()
        if "/" in r
        for n in prs
    }


def needs_you(payloads: dict[str, dict]) -> list[tuple[str, dict]]:
    """Open PRs carrying a nudge issue that you have not muted or snoozed — the
    rows that show 🔔."""
    return sorted(
        (k, p)
        for k, p in payloads.items()
        if p.get("state") == "OPEN"
        and p.get("nudge")
        and not p.get("muted")
        and not p.get("snoozed")
    )


def diff(before: dict[str, dict], after: dict[str, dict]) -> list[Change]:
    out: list[Change] = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        if new is None:
            title = str((old or {}).get("title") or "")
            out.append(Change(key, title, "gone (merged, closed or torn down)"))
            continue
        title = str(new.get("title") or "")
        if old is None:
            out.append(Change(key, title, "new"))
            continue
        for field in ("state", "ci", "review"):
            if (old.get(field) or "") != (new.get(field) or ""):
                was, now = old.get(field) or "none", new.get(field) or "none"
                out.append(Change(key, title, f"{field} {was} → {now}"))
        added = int(new.get("total") or 0) - int(old.get("total") or 0)
        if added > 0:
            out.append(
                Change(key, title, f"+{added} review comment{'s' * (added != 1)}")
            )
        if old.get("snoozed") and not new.get("snoozed"):
            out.append(Change(key, title, "woke from snooze"))
    return out


def ticket_diff(before: dict[str, str], after: dict[str, str]) -> list[tuple[str, str]]:
    """`(id, what)` for tickets that appeared or changed state. A ticket that
    left both the inbox and every PR says nothing: that is usually it closing."""
    out: list[tuple[str, str]] = []
    for tid in sorted(after):
        if tid not in before:
            out.append((tid, f"new ({after[tid] or 'no state'})"))
        elif before[tid] != after[tid]:
            out.append((tid, f"{before[tid] or 'none'} → {after[tid] or 'none'}"))
    return out


_PR_SUFFIX = re.compile(r"\(#(\d+)\)$")


def commits(log: str, covered: set[str]) -> list[tuple[str, str, str]]:
    """`git log --format=%h%x09%an%x09%s` → `(sha, author, subject)`, minus
    squash-merges whose `(#N)` PR number is in `covered`, which the PR section
    already reports."""
    out: list[tuple[str, str, str]] = []
    for line in log.splitlines():
        sha, _, rest = line.partition("\t")
        author, _, subject = rest.partition("\t")
        m = _PR_SUFFIX.search(subject)
        if sha and not (m and m.group(1) in covered):
            out.append((sha, author, subject))
    return out


START_KINDS = ("session", "pr", "spawn", "ticket")


def start_here(
    items: list[tuple[str, str, str]], cap: int = 7
) -> tuple[list[tuple[str, str, str]], int]:
    """`(kind, subject, reason)` items ranked by `START_KINDS`, first `cap`
    kept; returns them with the count left over. The sort is stable, so each
    kind keeps its caller's order."""
    ranked = sorted(items, key=lambda i: START_KINDS.index(i[0]))
    return ranked[:cap], max(0, len(ranked) - cap)
