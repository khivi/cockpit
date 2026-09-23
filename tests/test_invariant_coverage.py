"""AGENTS.md -> tests, the direction `tests/test_comment_references.py` leaves
open: it checks that a rule's backticked names still resolve, not that any test
guards the rule.

`tests/invariant_ids.py` registers the ids; a test claims one with a `covers`
marker. AGENTS.md's "Invariant ids" section holds the semantics — what fails
hard, what only warns, what may be waived.
"""

from __future__ import annotations

import ast
import warnings
from collections import defaultdict
from pathlib import Path

import pytest

from tests.invariant_ids import INVARIANTS, JUDGMENT_ONLY

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"


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


def test_every_covers_marker_names_a_registered_id():
    """The rot direction that fails loud: a marker outliving its rule — the
    half of the link a reader would never re-derive."""
    unknown = {r: sites for r, sites in _claimed().items() if r not in INVARIANTS}
    assert not unknown, f"covers() ids missing from tests/invariant_ids.py: {unknown}"


def test_every_registered_id_names_a_findable_rule():
    """The registry is the only route from an id back to its rule, so the
    phrase must still appear in AGENTS.md verbatim. Without this the two drift
    silently and the id stops meaning anything."""
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    lost = sorted(r for r, phrase in INVARIANTS.items() if phrase not in agents)
    assert not lost, (
        "registered ids whose AGENTS.md phrase no longer appears — the rule was "
        f"reworded or deleted: {lost}"
    )


def test_judgment_only_ids_are_registered_and_unclaimed():
    """The waiver list must stay honest: every entry a real id, none of them
    also carrying a test that would have made the waiver unnecessary."""
    assert not (JUDGMENT_ONLY - set(INVARIANTS)), "waiver names an unregistered id"
    assert not (JUDGMENT_ONLY & set(_claimed())), "waived rule has a test"


def test_report_uncovered_rules():
    """Report-only: an unregistered rule must not break an unrelated run."""
    uncovered = sorted(set(INVARIANTS) - set(_claimed()) - JUDGMENT_ONLY)
    if uncovered:
        listing = "\n".join(f"  {r}  ({INVARIANTS[r]})" for r in uncovered)
        warnings.warn(
            f"{len(uncovered)} registered invariant(s) with no covers() marker:\n{listing}",
            stacklevel=1,
        )
