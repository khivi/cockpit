"""AGENTS.md -> tests, the direction `tests/test_comment_references.py` leaves
open: it checks that a rule's backticked names still resolve, not that any test
guards the rule.

A test claims a rule by quoting it: `@pytest.mark.covers("<phrase from the
rule>")`. `rg 'covers\\(' tests/` is then the whole map, test -> rule, with no
second file to open. This module is the one thing that keeps the quote honest.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"


def _claimed() -> dict[str, list[str]]:
    """Every phrase claimed by a `covers()` marker, mapped to `path:line`
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


def test_every_covers_marker_quotes_a_live_rule():
    """The only way this rots: a rule is reworded or deleted while the test
    claiming it stays green, guarding something that no longer says what the
    marker says it says. The phrase must still appear in AGENTS.md verbatim.
    """
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    stale = {p: sites for p, sites in _claimed().items() if p not in agents}
    assert not stale, (
        "covers() phrases that have left AGENTS.md — each rule was reworded or "
        f"deleted, so re-quote it or drop the marker: {stale}"
    )
