"""specs/ -> tests, in both directions.

A spec bullet is `- [<id>~<rev>] <behavior>`; a test claims it with
`@pytest.mark.covers("<id>~<rev>")` (one or more ids per marker). The gate here
fails a marker naming an id no bullet carries, a marker whose revision trails
the bullet's (the bullet's meaning changed — re-verify the test, then bump the
marker), and a bullet no test claims — which is what makes adding a bullet a
demand for a test rather than documentation.

`(untested: <reason>)` after the id waives a bullet nothing runnable can
assert. Waivers are counted against a pinned total so they cannot quietly grow.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
SPECS_DIR = REPO_ROOT / "specs"

_BULLET_RE = re.compile(r"^- \[([a-z0-9.-]+)~(\d+)\](?: \(untested: ([^)]+)\))?")
_MARKER_RE = re.compile(r"^([a-z0-9.-]+)~(\d+)$")

# Bump deliberately, in the same change that adds or drops a waiver. The pin is
# what keeps "no test can assert this" from becoming the path of least
# resistance for a bullet that merely lacks a test.
WAIVED_COUNT = 31


def _spec_bullets() -> tuple[dict[str, int], set[str]]:
    """id -> revision for every bullet, plus the waived subset. Fails on a
    duplicate id — two bullets claiming one id would let the gate pass with
    only one of them meant."""
    revs: dict[str, int] = {}
    waived: set[str] = set()
    for path in sorted(SPECS_DIR.glob("*.md")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            m = _BULLET_RE.match(line)
            if not m:
                continue
            ident, rev, reason = m.group(1), int(m.group(2)), m.group(3)
            if ident in revs:
                pytest.fail(f"{path.name}:{lineno}: duplicate spec id {ident}")
            revs[ident] = rev
            if reason:
                waived.add(ident)
    return revs, waived


def _claimed() -> dict[str, list[str]]:
    """Every `id~rev` claimed by a `covers()` marker, mapped to `path:line`
    sites. Decorator and `pytestmark` list forms alike — both are a Call."""
    out: dict[str, list[str]] = defaultdict(list)
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in ast.walk(tree):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "covers"
                and isinstance(call.func.value, ast.Attribute)
                and call.func.value.attr == "mark"
            ):
                continue
            where = f"{path.relative_to(REPO_ROOT)}:{call.lineno}"
            for arg in call.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out[arg.value].append(where)
                else:
                    pytest.fail(f"{where}: covers() takes string literals only")
    return dict(out)


def test_every_marker_claims_a_live_bullet_at_its_current_revision():
    """A marker must name a spec id that exists, is not waived, and is at the
    revision the marker was written against. A trailing revision means the
    bullet's meaning changed after the test last vouched for it."""
    revs, waived = _spec_bullets()
    unknown: dict[str, list[str]] = {}
    stale: dict[str, list[str]] = {}
    waived_claims: dict[str, list[str]] = {}
    for claim, sites in _claimed().items():
        m = _MARKER_RE.match(claim)
        if not m:
            pytest.fail(f"{sites[0]}: covers({claim!r}) is not '<id>~<rev>'")
        ident, rev = m.group(1), int(m.group(2))
        if ident not in revs:
            unknown[claim] = sites
        elif ident in waived:
            waived_claims[claim] = sites
        elif rev != revs[ident]:
            stale[claim] = sites
    assert not unknown, f"covers() ids with no spec bullet: {unknown}"
    assert not waived_claims, (
        "these bullets are waived but a test claims them — drop the "
        f"(untested: …) waiver and the pin here: {waived_claims}"
    )
    assert not stale, (
        "spec changed, re-verify: the bullet's revision moved past the "
        "marker's, so re-check the test still asserts what the bullet now "
        f"says, then bump the marker: {stale}"
    )


def test_every_unwaived_bullet_is_claimed_by_a_test():
    """The enforcement direction: editing the spec is how you demand a test.
    Add a bullet and this fails until a test claims it."""
    revs, waived = _spec_bullets()
    claimed_ids = {claim.rsplit("~", 1)[0] for claim in _claimed()}
    gaps = sorted(set(revs) - waived - claimed_ids)
    assert not gaps, (
        "spec bullets no test claims — write the test, or waive the bullet "
        f"with (untested: <reason>) if nothing runnable can assert it: {gaps}"
    )


def test_waivers_cannot_quietly_grow():
    _, waived = _spec_bullets()
    assert len(waived) == WAIVED_COUNT, (
        f"waived bullets: {len(waived)}, pinned: {WAIVED_COUNT}. Adding a "
        "waiver is a deliberate act — bump WAIVED_COUNT in the same change, "
        "and only for a bullet nothing runnable can assert."
    )
