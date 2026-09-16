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

**Every check here fires on cmux REMOVING something, never on it adding.** A
census of advertised-but-unused verbs used to live here, bucketed by why
cockpit ignores each one, and it failed by name whenever a release shipped a
new verb. That is a documentation-freshness tripwire, not a correctness gate:
it fired on twelve additions that cost cockpit nothing, in the pre-push suite,
where the only way past it is to classify them. Nothing machine-readable would
have helped — `cmux capabilities` names neither `list-status` nor
`list-workspaces`, so the audit had no choice but to read `--help`. The two
invariants above are the ones worth blocking a push for, and both are immune to
additions. **Do not** re-add a test that enumerates cmux's surface; keep the
prose audit in `docs/cmux-surface-audit.md` and re-derive it when you want it.
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

pytestmark = [
    # Execs the real binaries — that is this file's whole purpose. Opts out of
    # the suite-wide `_no_live_backend` guard in `tests/conftest.py`. One list
    # rather than a second `pytestmark =`, which silently replaces the first.
    pytest.mark.real_backend,
    pytest.mark.skipif(
        shutil.which("cmux") is None, reason="cmux binary not installed"
    ),
]

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
