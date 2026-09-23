"""AGENTS.md -> tests, the direction `tests/test_comment_references.py` leaves
open: it checks that a rule's backticked names still resolve, not that any test
guards the rule.

A rule opts in with a trailing `[some.rule.id]`; a test claims it with a
`covers` marker naming that id. AGENTS.md's "Invariant ids" section holds the
semantics — what fails hard, what only warns, what may be waived.
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

# A rule nothing could ever assert against; not one that merely lacks a test yet.
_JUDGMENT_ONLY = frozenset(
    {
        # Both constrain the shape of code a future change would add; a test
        # can only run code that exists.
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
    """The rot direction that fails loud: a marker outliving its rule — the
    half of the link a reader would never re-derive."""
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
    """Report-only: an unlabelled rule must not break an unrelated run."""
    declared = _declared()
    uncovered = sorted(set(declared) - set(_claimed()) - _JUDGMENT_ONLY)
    if uncovered:
        listing = "\n".join(f"  AGENTS.md:{declared[r]}  {r}" for r in uncovered)
        warnings.warn(
            f"{len(uncovered)} declared invariant(s) with no covers() marker:\n{listing}",
            stacklevel=1,
        )
