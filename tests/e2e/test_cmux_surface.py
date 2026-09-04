"""End-to-end tests of the capability gate against the real cmux binary.

`tests/lib/test_capabilities.py` drives `parse_verbs` / `parse_capabilities`
with a synthetic `HELP` fixture, which proves the parsers work on the shape we
*expect*. Nothing there notices when the real cmux stops matching that shape:
if a help-format change makes `parse_verbs` return garbage, every unit test
still passes and the gate silently degrades to warning about everything or
nothing. This module is the other half — the parsers pointed at whatever cmux
is actually installed.

It also pins the two invariants that were only assertions in prose until the
2026-08-25 surface audit found both violated: everything cockpit *declares
required* must really be on offer, and everything cockpit *invokes* must really
exist. See `docs/cmux-surface-audit.md`.

Module-level skip means CI (no cmux) passes cleanly; the laptop hosts the
signal. Every assertion is a subset test against the live binary — deliberately
no counts, no pinned version, nothing that turns a cmux release into a red
suite.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest

from cockpit.lib.capabilities import (
    REQUIRED_CAPABILITIES,
    REQUIRED_VERBS,
    probe,
)

pytestmark = pytest.mark.skipif(
    shutil.which("cmux") is None, reason="cmux binary not installed"
)

# Execs the real binaries — that is this file's whole purpose. Opts out of the
# suite-wide `_no_live_backend` guard in `tests/conftest.py`. Appended rather
# than assigned: a second `pytestmark =` silently replaces the first.
pytestmark = [pytestmark, pytest.mark.real_backend]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PACKAGE = REPO_ROOT / "cockpit"

# `workspace-group` is absent from `cmux --help`'s `Commands:` list though fully
# documented under its own `--help`. cockpit hard-depends on it for every sidebar
# fold, so `test_every_verb_cockpit_invokes_exists` would fail on it forever.
# It is gated on the capability axis instead (`workspace.groups.v1`).
# If cmux ever documents it, `test_undocumented_verbs_are_still_undocumented`
# fails and this set should shrink — that is the point of keeping it explicit.
UNDOCUMENTED_VERBS = frozenset({"workspace-group"})

# Not a cmux verb — `probe` passes it to the same helper to read `--help` itself.
NOT_A_VERB = frozenset({"--help"})

# Every advertised verb cockpit does NOT call, bucketed by why. This is the one
# place bucket membership lives; `docs/cmux-surface-audit.md` carries the prose
# rationale per bucket and deliberately carries no verb lists or counts.
#
# `test_every_advertised_verb_is_accounted_for` fails when cmux ships a verb that
# is in no bucket — so a new cmux release lands here as one failing test naming
# the newcomers, instead of as an audit that quietly describes an older cmux.
UNUSED_VERBS: dict[str, frozenset[str]] = {
    "actionable": frozenset(
        """browser clear-log clear-notifications clear-progress current-workspace
        dismiss-notification identify jump-to-unread list-log
        list-notifications log mark-notification-read markdown memory notify open
        open-notification reorder-workspace reorder-workspaces
        right-sidebar set-progress sidebar sidebar-state surface-health todo top
        tree trigger-flash""".split()
    ),
    "tmux-compat": frozenset(
        """bind-key break-pane capture-pane clear-history display-message
        find-window join-pane last-pane list-buffers next-window paste-buffer
        pipe-pane popup resize-pane respawn-pane set-buffer set-hook swap-pane
        wait-for""".split()
    ),
    "layout": frozenset(
        """close-surface close-window current-window drag-surface-to-split
        focus-pane focus-panel focus-window list-pane-surfaces list-panels
        list-panes list-windows move-surface move-tab-to-new-workspace
        move-workspace-to-window new-pane new-split new-surface new-window
        refresh-surfaces rename-tab rename-window reorder-surface send-key-panel
        send-panel split-off surface tab-action workspace""".split()
    ),
    "remote": frozenset(
        """ai-accounts auth iroh-diag login mosh mosh-tmux ping
        remote-daemon-status remotes ssh ssh-session-attach ssh-session-cleanup
        ssh-session-list ssh-tmux vm""".split()
    ),
    "agent-lifecycle": frozenset(
        """agent-hibernation claude-teams codex-teams feed hooks omc omo omx
        restore restore-session""".split()
    ),
    "chrome": frozenset(
        """config debug-terminals disable-browser docs feedback help ios
        reload-config set-app-focus settings shortcuts simulate-app-active
        simulate-sidebar-drag simulator themes version welcome""".split()
    ),
}


def _invoked_verbs() -> set[str]:
    """Every cmux verb reachable from `cockpit/`, by AST rather than grep.

    Several `cmux(...)` calls span lines, and `lib/events.py` builds a raw
    `["cmux", "events", ...]` argv for `Popen` instead of going through the
    wrapper — a regex anchored on `cmux("` misses both.
    """
    verbs: set[str] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "cmux"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                verbs.add(node.args[0].value)
            elif (
                isinstance(node, ast.List)
                and node.elts
                and isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value == "cmux"
                and len(node.elts) > 1
                and isinstance(node.elts[1], ast.Constant)
                and isinstance(node.elts[1].value, str)
            ):
                verbs.add(node.elts[1].value)
    return verbs - NOT_A_VERB


def _advertised_top_level() -> set[str]:
    """First token of every command line in `cmux --help`'s `Commands:` section.

    Deliberately NOT `parse_verbs`, which additionally splits alternations. That
    is right for `disable-browser | enable-browser | browser-status` (three real
    verbs) and wrong for the `browser <subcommand>` lines, where `goto|navigate`
    and `back|forward|reload` are subcommands of `browser` — so `parse_verbs`
    returns a superset containing tokens that were never top-level. Over-collection
    can only cause a false pass in the gate, so it is not a bug there; but a
    "which verbs exist" question needs the stricter reading.

    The section runs to the next unindented header, **not** to the first blank
    line: it holds four blank-line-separated groups (main, tmux compatibility,
    markdown, browser), and stopping at the first blank line is what made the
    original audit miss 22 verbs.
    """
    from cockpit.lib.cmux import cmux

    verbs, in_section = set(), False
    for line in cmux("--help", check=False).splitlines():
        if not line.startswith((" ", "\t")):
            if line.strip():
                in_section = line.strip() == "Commands:"
            continue
        if in_section:
            head = line.strip().split()
            if head and head[0][0].isalpha():
                verbs.add(head[0])
    return verbs


@pytest.fixture(scope="module")
def live():
    """The real cmux's verbs + capability ids, probed once."""
    probe.cache_clear()
    found = probe()
    if not found.verbs:
        pytest.skip("cmux on PATH but not answering `--help`")
    yield found
    probe.cache_clear()


def test_parse_verbs_still_understands_the_real_help_format(live):
    """A help-format change must fail here, not silently empty the gate.

    The floor is a sanity bound, not a pinned count — it only catches the parser
    returning junk, and must never be tightened into a version assertion.
    """
    assert len(live.verbs) > 50
    # One line shape per assertion: bare, trailing into `[flags]`, and a
    # verb-position alternation. `enable-browser` is only ever the middle term of
    # `disable-browser | enable-browser | browser-status`, so it appears here iff
    # the split still happens — the path a first-token reading never exercises.
    assert "capabilities" in live.verbs
    assert {"send", "list-workspaces"} <= live.verbs
    assert "enable-browser" in live.verbs
    assert not any(v.startswith(("-", "<", "[")) for v in live.verbs)


def test_every_required_verb_is_really_advertised(live):
    """`REQUIRED_VERBS` must name verbs this cmux actually has.

    A required verb missing from the real binary means the daemon warns on every
    start and disables a tier for a user whose cmux is fine.
    """
    assert set(REQUIRED_VERBS) <= live.verbs


def test_every_required_capability_is_really_offered(live):
    """`REQUIRED_CAPABILITIES` must name ids this cmux actually negotiates.

    Catches only over-declaring. Requiring an id cmux does not offer is a
    different fault from requiring one for a feature cockpit lacks, which is
    `test_required_capabilities_only_name_tiers_cockpit_actually_has` in
    `tests/lib/test_capabilities.py`. **Do not** "fix" a failure here by
    requiring whatever the installed cmux happens to offer.
    """
    if not live.supports_capabilities:
        pytest.skip("cmux predates `cmux capabilities`")
    if not live.capabilities:
        pytest.skip("`cmux capabilities` returned nothing — app likely not running")
    assert set(REQUIRED_CAPABILITIES) <= live.capabilities


def test_every_verb_cockpit_invokes_exists(live):
    """Nothing in `cockpit/` may shell out to a verb this cmux doesn't have.

    Broader than `REQUIRED_VERBS`, which gates 5 of the 15 cockpit invokes — the
    rest are best-effort `check=False` calls, so a typo or a verb cmux retires
    surfaces as a silent mid-cycle no-op rather than an error.
    """
    unknown = _invoked_verbs() - live.verbs - UNDOCUMENTED_VERBS
    assert not unknown, f"cockpit invokes verbs this cmux doesn't advertise: {unknown}"


def test_the_buckets_do_not_overlap_and_do_not_claim_used_verbs():
    """Bookkeeping on `UNUSED_VERBS`: no bucket overlaps another or a used verb."""
    seen: set[str] = set()
    for name, bucket in UNUSED_VERBS.items():
        clash = seen & bucket
        assert not clash, f"{name} re-buckets {sorted(clash)}"
        seen |= bucket
    assert not (seen & _invoked_verbs()), "a bucketed verb is actually invoked"


def test_every_advertised_verb_is_accounted_for(live):
    """Each advertised verb is either invoked by cockpit or in exactly one bucket.

    An audit that silently comes to describe an older cmux is worse than no audit.
    A cmux upgrade adding verbs fails here, naming them — the intended signal, not
    a breakage. Classify them in `UNUSED_VERBS`, and write up anything interesting
    in `docs/cmux-surface-audit.md`.
    """
    bucketed = frozenset().union(*UNUSED_VERBS.values())
    unclassified = _advertised_top_level() - _invoked_verbs() - bucketed
    assert not unclassified, (
        f"cmux advertises verbs in no bucket: {sorted(unclassified)} — "
        "classify them in UNUSED_VERBS"
    )


def test_undocumented_verbs_are_still_undocumented(live):
    """`UNDOCUMENTED_VERBS` is a waiver, so it has to expire on its own.

    A verb listed here is exempt from the check above. If cmux documents one,
    the waiver silently keeps covering it — so fail, and make someone shrink
    the set.
    """
    documented = UNDOCUMENTED_VERBS & live.verbs
    assert not documented, (
        f"cmux now documents {documented} in `--help` — drop it from "
        "UNDOCUMENTED_VERBS so the real check covers it"
    )
