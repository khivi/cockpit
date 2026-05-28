"""Persistent per-PR nudge state (rate limit + user mute) under COCKPIT_HOME.

One JSON file per PR at `$COCKPIT_HOME/cache/nudges/<pr-number>.json`. Holds
both the daemon-set `last_nudge_at` timestamp (for rate limiting) and the
user-set `disabled_categories` / `until` mute (set via `cockpit nudge mute`).

Persisting both in one place means daemon restarts don't replay nudges the user
already saw, and `parked=`-style runtime state survives across cmux restarts
and workspace teardown/recreate on the same PR.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import CACHE_DIR

NUDGE_DIR = CACHE_DIR / "nudges"
KNOWN_CATEGORIES = ("comments", "ci", "conflicts")


@dataclass
class NudgePref:
    disabled_categories: set[str] = field(default_factory=set)
    until: float | None = None
    reason: str = ""
    last_nudge_at: float = 0.0
    last_nudge_category: str | None = None

    def to_json(self) -> dict:
        return {
            "disabled_categories": sorted(self.disabled_categories),
            "until": self.until,
            "reason": self.reason,
            "last_nudge_at": self.last_nudge_at,
            "last_nudge_category": self.last_nudge_category,
        }

    @classmethod
    def from_json(cls, data: dict) -> "NudgePref":
        return cls(
            disabled_categories=set(data.get("disabled_categories") or []),
            until=data.get("until"),
            reason=data.get("reason", "") or "",
            last_nudge_at=float(data.get("last_nudge_at") or 0.0),
            last_nudge_category=data.get("last_nudge_category"),
        )


def _pref_path(pr_number: int) -> Path:
    return NUDGE_DIR / f"{pr_number}.json"


def load_pref(pr_number: int, *, now: float | None = None) -> NudgePref:
    """Load a PR's nudge pref. Auto-expires `disabled_categories` when `until`
    has passed and persists the expiry, so the daemon resumes nudging without
    a separate sweep step."""
    path = _pref_path(pr_number)
    if not path.exists():
        return NudgePref()
    try:
        pref = NudgePref.from_json(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError):
        return NudgePref()
    t = time.time() if now is None else now
    if pref.until is not None and pref.until <= t and pref.disabled_categories:
        pref.disabled_categories.clear()
        pref.until = None
        pref.reason = ""
        save_pref(pr_number, pref)
    return pref


def save_pref(pr_number: int, pref: NudgePref) -> None:
    NUDGE_DIR.mkdir(parents=True, exist_ok=True)
    _pref_path(pr_number).write_text(json.dumps(pref.to_json(), indent=2) + "\n")


def delete_pref(pr_number: int) -> bool:
    path = _pref_path(pr_number)
    if not path.exists():
        return False
    path.unlink()
    return True


def list_prefs() -> dict[int, NudgePref]:
    """Return all persisted prefs keyed by PR number. Skips garbage files."""
    if not NUDGE_DIR.exists():
        return {}
    out: dict[int, NudgePref] = {}
    for p in sorted(NUDGE_DIR.glob("*.json")):
        try:
            pr_number = int(p.stem)
        except ValueError:
            continue
        try:
            out[pr_number] = NudgePref.from_json(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def should_nudge(
    pr_number: int,
    category: str,
    *,
    now: float | None = None,
) -> bool:
    """True iff nudging this PR in this category is allowed right now.

    Blocks only when the user has muted the category. The slow tick's cadence
    (`slow_poll_interval_seconds`, default 300s) is the implicit throttle —
    each tick re-evaluates and re-fires if the issue persists. `last_nudge_at`
    is still recorded so `cockpit nudge status` can display "last nudged X
    ago," but it does not gate future nudges.
    """
    t = time.time() if now is None else now
    pref = load_pref(pr_number, now=t)
    if category in pref.disabled_categories:
        return False
    return True


def record_nudge(pr_number: int, category: str, *, now: float | None = None) -> None:
    t = time.time() if now is None else now
    pref = load_pref(pr_number, now=t)
    pref.last_nudge_at = t
    pref.last_nudge_category = category
    save_pref(pr_number, pref)


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$")


def parse_duration(s: str) -> float:
    """Parse `30s`, `15m`, `2h`, `7d`, `1w` into seconds. Raises ValueError otherwise."""
    m = _DURATION_RE.match(s.lower())
    if m is None:
        raise ValueError(
            f"invalid duration {s!r} — use forms like 30s, 15m, 2h, 7d, 1w"
        )
    n = int(m.group(1))
    unit = m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]


def normalize_categories(raw: str | None) -> set[str]:
    """Parse `--categories comments,ci`. Empty / None = all known categories
    (full mute). Unknown tokens raise ValueError so typos don't silently no-op.
    """
    if not raw:
        return set(KNOWN_CATEGORIES)
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    bad = [t for t in tokens if t not in KNOWN_CATEGORIES]
    if bad:
        label = "category" if len(bad) == 1 else "categories"
        raise ValueError(
            f"unknown {label}: {', '.join(bad)} "
            f"(known: {', '.join(KNOWN_CATEGORIES)})"
        )
    return set(tokens)
