"""Persistent per-PR nudge state (rate limit + user mute/snooze) under COCKPIT_HOME.

One JSON file per PR at `$COCKPIT_HOME/cache/nudges/<repo>__<pr-number>.json`
(`pref_key` — a PR number alone is not unique across repos). Holds both the
daemon-set `last_nudge_at` timestamp (for rate limiting) and the user-set
`muted` / `until` mute (set via `cockpit nudge mute`). A mute is all-or-nothing
— it silences every nudge for the PR.

`snoozed` is the *separate* "I've read this, it's someone else's turn" state
(TUI `z`). It silences nudges like a mute, and additionally sinks the PR to the
bottom of the sidebar (`cycle._reconcile_sidebar_groups`), but unlike a mute it
is **event-expiring**: `wake_on` records the PR's review activity at snooze time
and `wake_nudge` the actionable issue it had (if any), and the daemon clears the
snooze as soon as review activity changes *or* a new issue appears. Kept distinct
from `muted` because the two answer different questions — mute is "shut up
indefinitely" (`cockpit nudge mute`), snooze is "come back when someone comments,
approves, or the PR needs me again".

Snoozing **clears** a mute (the TUI's `z`): a mute wins over a snooze everywhere
(glyph, sidebar fold, `quiet`), so leaving both set would silently discard the
snooze and its wake. Snooze is strictly the narrower ask, so it takes over.

Persisting both in one place means daemon restarts don't replay nudges the user
already saw, and `parked=`-style runtime state survives across cmux restarts
and workspace teardown/recreate on the same PR.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .config import CACHE_DIR

NUDGE_DIR = CACHE_DIR / "nudges"


@dataclass
class NudgePref:
    muted: bool = False
    until: float | None = None
    reason: str = ""
    last_nudge_at: float = 0.0
    snoozed: bool = False
    # The PR's review activity when the snooze was set (`wake_signature`).
    # Meaningless unless `snoozed`; the daemon compares it against the live PR
    # every cycle and wakes on any difference.
    wake_on: str = ""
    # The PR's actionable issue (`PR.nudge_issue`, "" for none) when the snooze
    # was set. Kept as its own field rather than folded into `wake_on` because
    # its wake rule is asymmetric — a *new* issue wakes the snooze, an issue
    # going away does not (nothing to come back to), so equality alone can't
    # decide it. See `cycle._resolve_prefs`.
    wake_nudge: str = ""

    def to_json(self) -> dict:
        return {
            "muted": self.muted,
            "until": self.until,
            "reason": self.reason,
            "last_nudge_at": self.last_nudge_at,
            "snoozed": self.snoozed,
            "wake_on": self.wake_on,
            "wake_nudge": self.wake_nudge,
        }

    @classmethod
    def from_json(cls, data: dict) -> NudgePref:
        # Legacy keys (`disabled_categories`, `last_nudge_category`) are simply
        # ignored — an absent `muted` reads as not muted (any prior mute is
        # dropped). An absent `snoozed` likewise reads as not snoozed, so a
        # pre-snooze pref file loads unchanged. An absent `wake_nudge` on an
        # already-snoozed pref reads as "no issue at snooze time", so a PR that
        # is currently failing wakes once on the next cycle — it does have work.
        return cls(
            muted=bool(data.get("muted")),
            until=data.get("until"),
            reason=data.get("reason", "") or "",
            last_nudge_at=float(data.get("last_nudge_at") or 0.0),
            snoozed=bool(data.get("snoozed")),
            wake_on=str(data.get("wake_on") or ""),
            wake_nudge=str(data.get("wake_nudge") or ""),
        )

    @property
    def quiet(self) -> bool:
        """True when the user has silenced this PR's nudges, either way."""
        return self.muted or self.snoozed


def wake_signature(total_from_others: int, review_decision: str) -> str:
    """Fingerprint of the review activity a snooze waits on.

    Two signals, both already fetched every slow tick: the number of review
    threads opened by *others* (`PR.total_from_others` — my own comments must
    not wake my own snooze) and GitHub's `reviewDecision` (so an approval or a
    changes-requested wakes it even with no new thread). Any change to either
    ends the snooze; the value itself is opaque, only equality matters.
    """
    return f"{int(total_from_others)}|{review_decision or ''}"


def pref_key(repo_name: str, pr_number: int) -> str:
    """The pref file stem for one PR: `<repo>__<number>`.

    PR numbers are only unique *within* a repo, so keying prefs by the number
    alone made every repo share one file — muting or snoozing `#10` in one repo
    silenced `#10` in every other, and each repo's cycle woke the others'
    snoozes (their `wake_on` describes a different PR). `repo_name` is the git
    nwo name, the same key the PR cache files use (`cache._repo_slug`), so the
    two agree on what identifies a repo.
    """
    return f"{repo_name.replace('/', '_')}__{pr_number}"


def _pref_path(key: str) -> Path:
    return NUDGE_DIR / f"{key}.json"


def _legacy_path(key: str) -> Path | None:
    """The pre-`pref_key` global-by-number file for `key`, if it still exists.

    Read-only fallback: `load_pref` adopts its contents so an existing mute
    survives the re-key, and the next `save_pref` writes the per-repo file. It
    is deliberately never unlinked — several repos can share one legacy file,
    and the first to migrate must not take it from the others.
    """
    _, _, number = key.rpartition("__")
    path = NUDGE_DIR / f"{number}.json"
    return path if number.isdigit() and path.exists() else None


def load_pref(key: str, *, now: float | None = None) -> NudgePref:
    """Load a PR's nudge pref. Auto-expires the mute when `until` has passed and
    persists the expiry, so the daemon resumes nudging without a separate sweep
    step."""
    path = _pref_path(key)
    if not path.exists():
        legacy = _legacy_path(key)
        if legacy is None:
            return NudgePref()
        path = legacy
    try:
        pref = NudgePref.from_json(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError):
        return NudgePref()
    t = time.time() if now is None else now
    if pref.until is not None and pref.until <= t and pref.muted:
        pref.muted = False
        pref.until = None
        pref.reason = ""
        save_pref(key, pref)
    return pref


def save_pref(key: str, pref: NudgePref) -> None:
    NUDGE_DIR.mkdir(parents=True, exist_ok=True)
    _pref_path(key).write_text(json.dumps(pref.to_json(), indent=2) + "\n")


def delete_pref(key: str) -> bool:
    path = _pref_path(key)
    if not path.exists():
        return False
    path.unlink()
    return True


def list_prefs() -> dict[str, NudgePref]:
    """Return all persisted prefs keyed by file stem (`pref_key`, or a bare PR
    number for a not-yet-migrated legacy file). Skips garbage files."""
    if not NUDGE_DIR.exists():
        return {}
    out: dict[str, NudgePref] = {}
    for p in sorted(NUDGE_DIR.glob("*.json")):
        try:
            out[p.stem] = NudgePref.from_json(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def should_nudge(key: str, *, now: float | None = None) -> bool:
    """True iff nudging this PR is allowed right now.

    Blocks when the user has muted the PR (silences all nudges indefinitely) or
    snoozed it (silences until someone comments/approves — the daemon clears the
    snooze, see `wake_signature`). Both are the user saying "not now", so both
    gate here.

    The slow tick's cadence (`slow_poll_interval_seconds`, default 300s) is the
    implicit throttle — each tick re-evaluates and re-fires if the issue
    persists. `last_nudge_at` is still recorded so `cockpit nudge status` can
    display "last nudged X ago," but it does not gate future nudges.
    """
    t = time.time() if now is None else now
    return not load_pref(key, now=t).quiet


def record_nudge(key: str, *, now: float | None = None) -> None:
    t = time.time() if now is None else now
    pref = load_pref(key, now=t)
    pref.last_nudge_at = t
    save_pref(key, pref)


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
