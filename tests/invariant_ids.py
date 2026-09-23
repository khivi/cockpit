"""The ids a `covers()` marker may name, and the AGENTS.md rule each one means.

The value is a distinctive phrase from the rule itself, so `rg "<phrase>"
AGENTS.md` lands on it. Keep it that way — a paraphrase makes the rule
unfindable from here, which is the only navigation this file offers.

Registering an id is how a rule enters the gap list — do it when you decide a
rule is worth guarding, not when you write the test.
"""

from __future__ import annotations

INVARIANTS: dict[str, str] = {
    # TUI rendering.
    "table.links.escape-reaches-terminal": "**The hover tooltip names the destination**",
    "palette.commands.order-is-the-menu": "`discover` yields in tuple order",
    "header.bar.countdowns-anchored-right": "`#header-repo` owns the one `1fr` slot",
    "bands.snoozed.no-glyph-still-suppresses-bell": (
        "**Snooze has no row glyph but still suppresses"
    ),
    "tickets-screen.columns.explicit-widths": (
        "**Its columns take explicit widths, never `DataTable`'s auto-sizing.**"
    ),
    # Cache keying and the PR join.
    "cache.key.flat-cells-by-worktree-path": (
        "**Every flat cell is keyed by worktree path"
    ),
    "pr-list.one-per-head-branch": (
        "### The live PR list carries at most one PR per head branch"
    ),
    # cmux sidebar folds.
    "folds.anchor.owns-a-live-shell": "**The anchor must own a live shell",
    "folds.restore.create-only": "**It can only create**",
    "sidebar-tag.separator.last-char": (
        "**A tag ending in a non-alphanumeric takes a space"
    ),
    # Sends and seeding.
    "send.one-line.inside-the-funnel": "**Every message is collapsed to one line",
    "spawn.seed.no-enter-on-unconfirmed": "**Giving up presses no Enter.**",
    "idle-pill.liveness.match-on-workspace-id": (
        "**The liveness guard in the hook must compare"
    ),
    # Spawn, tickets, capabilities.
    "spawn.ticket-prompt.reads-repo-block": "**`spawn.py`'s half is deferred",
    "tickets.credentials.envs-in-step-with-stripper": (
        "**An unset credential warns at startup"
    ),
    "capabilities.tiers.must-exist": (
        "**Every entry must name a tier cockpit actually HAS.**"
    ),
    "prompts.templates.all-resolve": "`tests/test_templates.py` asserts every template",
    "update-branch.dismisses-stale.two-sources": "**That verdict is TWO sources",
    # Release plumbing.
    "release.tag-yml.keeps-credentials": "`tag.yml` is exempt from `artipacked`.",
    # The suite's own rules.
    "suite.isolation.no-live-backend": ("## The suite cannot reach the live machine"),
    "docs.references.backticks-resolve": "**Repo-wide invariant tests**",
    "tests.helpers.take-input-dont-fetch": (
        "**An extracted helper takes its input; it does not fetch it.**"
    ),
    "tests.gates.verify-outcome-not-proxy": "must VERIFY THE OUTCOME",
}

# A rule no test could ever assert against; not one that merely lacks a test
# yet. Both constrain the shape of code a future change would add.
JUDGMENT_ONLY: frozenset[str] = frozenset(
    {
        "tests.helpers.take-input-dont-fetch",
        "tests.gates.verify-outcome-not-proxy",
    }
)
