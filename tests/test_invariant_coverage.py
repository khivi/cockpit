"""AGENTS.md's `**Do not**` lines encode paid-for regressions, and nothing
answers which of them a test actually guards. `tests/test_comment_references.py`
already checks the AGENTS.md -> code direction; this is the missing
AGENTS.md -> tests half.

A rule opts in by ending with a bracketed id — `[folds.restore.create-only]` —
and a test claims it with `@pytest.mark.covers("folds.restore.create-only")`.
The link is the id, never a path or a symbol, so a rename moves neither end.

The id proves a guard EXISTS; it never proves the guard is strong. A test that
asserts nothing satisfies this gate exactly as well as one that asserts
everything, and the marker is written by whoever wrote the rule, so the two
errors correlate. Mutation testing is the only thing that closes that, and this
file is not it.

Markers are collected by walking the source with `ast`, deliberately not from
`config.stash` via a collection hook: under `pytest -n auto` each xdist worker
collects only its own subset, so a session-scoped read would report every other
worker's rules as uncovered. The static walk also lets the gate run alone.
"""

from __future__ import annotations

import ast
import re
import warnings
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_MD = REPO_ROOT / "AGENTS.md"
TESTS_DIR = REPO_ROOT / "tests"

# Trailing id on a rule: at least one dot, so a prose aside in square brackets
# and a markdown link label can never read as a declaration.
_DECL_RE = re.compile(r"\[([a-z0-9]+(?:[.-][a-z0-9]+)+)\]\s*$", re.MULTILINE)

# A rule nothing can assert against — a judgment call, or a fact that lives
# outside the tree. Listing one keeps it out of the gap list instead of letting
# thirty permanent entries teach you to skim the report. Each entry is a claim
# that no test could hold the rule, not that none has been written yet.
_JUDGMENT_ONLY = frozenset(
    {
        # Both are rules about the SHAPE of code a future change would add, and
        # a test can only run code that already exists. AGENTS.md says as much
        # of the second: "the bug class is invisible to mocks and to coverage,
        # so the answer is in the code's shape, not in more tests."
        "tests.helpers.take-input-dont-fetch",
        "tests.gates.verify-outcome-not-proxy",
    }
)


def _declared() -> dict[str, int]:
    """Every id declared in AGENTS.md, mapped to its 1-indexed line."""
    text = AGENTS_MD.read_text(encoding="utf-8")
    out: dict[str, int] = {}
    for match in _DECL_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        out.setdefault(match.group(1), line)
    return out


def _declared_duplicates() -> dict[str, list[int]]:
    text = AGENTS_MD.read_text(encoding="utf-8")
    seen: dict[str, list[int]] = defaultdict(list)
    for match in _DECL_RE.finditer(text):
        seen[match.group(1)].append(text.count("\n", 0, match.start()) + 1)
    return {k: v for k, v in seen.items() if len(v) > 1}


def _covers_calls(tree: ast.AST) -> list[ast.Call]:
    """Every `....mark.covers(...)` call, decorator or `pytestmark` list alike."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "covers"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "mark"
    ]


def _claimed() -> dict[str, list[str]]:
    """Every id claimed by a `covers()` marker, mapped to `path:line` sites."""
    out: dict[str, list[str]] = defaultdict(list)
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _covers_calls(tree):
            where = f"{path.relative_to(REPO_ROOT)}:{call.lineno}"
            for arg in call.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out[arg.value].append(where)
                else:
                    pytest.fail(f"{where}: covers() takes string literals only")
    return dict(out)


def test_every_covers_marker_names_a_declared_rule():
    """The rot direction that always fails loud: a marker outliving its rule.

    Deleting a rule from AGENTS.md leaves its tests claiming an id nothing
    declares, which is the one half of the link a reader would never re-derive.
    """
    unknown = {
        rule_id: sites
        for rule_id, sites in _claimed().items()
        if rule_id not in _declared()
    }
    assert not unknown, (
        "covers() ids with no matching rule in AGENTS.md — the rule was "
        f"renamed or deleted: {unknown}"
    )


def test_no_rule_id_is_declared_twice():
    """Two rules under one id make the gap list lie in both directions."""
    assert not _declared_duplicates()


def test_judgment_only_rules_are_declared_and_unclaimed():
    """The waiver list must stay honest: every entry a real rule, none of them
    also carrying a test that would have made the waiver unnecessary."""
    declared, claimed = _declared(), _claimed()
    assert not (_JUDGMENT_ONLY - set(declared)), "waiver names an undeclared rule"
    assert not (
        _JUDGMENT_ONLY & set(claimed)
    ), "waived rule has a test; drop the waiver"


def test_report_uncovered_rules():
    """Report-only, by design. Rules opt in one at a time, so a rule with no
    test yet must never block a run that has nothing to do with it; flipping
    this to an assertion is a decision for when the gap list is empty.
    """
    declared = _declared()
    uncovered = sorted(set(declared) - set(_claimed()) - _JUDGMENT_ONLY)
    if uncovered:
        listing = "\n".join(f"  AGENTS.md:{declared[r]}  {r}" for r in uncovered)
        warnings.warn(
            f"{len(uncovered)} declared invariant(s) with no covers() marker:\n{listing}",
            stacklevel=1,
        )
