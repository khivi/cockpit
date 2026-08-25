"""Comments here carry rationale, and rationale names things — functions, config
fields, prompt templates, cache cells. A renamed symbol leaves those names behind
as claims that no longer resolve, and nothing re-reads a comment to notice.

This is the prose analogue of `tests/e2e/test_cmux_surface.py`: assert the fact
against the tree instead of writing it down and hoping. It never asserts *what* a
comment says — only that every `backticked` name it mentions still exists.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import tokenize
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Snake_case only. A bare word like `master` or `approved` is prose, a config
# value, or an external field name; requiring an underscore keeps the rule on
# tokens that read as repo symbols and off English.
_SYMBOL_RE = re.compile(r"^_?[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_PATH_RE = re.compile(r"^[\w./\-]+\.(?:py|md|txt|json|toml|sh|yml|yaml|rb)$")

# Names that correctly resolve outside this repo. Each entry is a promise that
# the name belongs to a dependency or another project — not an excuse to park a
# stale reference here.
_EXTERNAL_SYMBOLS = {
    # Textual framework internals cockpit overrides or defers to.
    "_on_click",
    "_on_mouse_move",
    "action_back",
    "hover_coordinate",
    # stdlib.
    "lru_cache",
    "redirect_stdout",
    "run_until_complete",
    "shutdown_default_executor",
    # cmux's own native status field, not a Python name.
    "claude_code",
    # The PyPI distribution name (`cmux-cockpit`) in its normalized wheel form.
    "cmux_cockpit",
    # cmux capability ids deliberately documented as the narrowing NOT taken.
    "group_actions",
    "group_create",
    # Legacy nudge-pref keys, named so the drop is documented.
    "disabled_categories",
    "last_nudge_category",
}

_EXTERNAL_PATHS = {
    # Claude Code's `.claude/commands/` filename convention, by example.
    "deploy.md",
    # The morning-align repo's strict-delivery helper, referenced as precedent.
    "linear_delivery.py",
    # Runtime artifacts under $COCKPIT_HOME — written by the daemon, never
    # tracked. `config.example.json` is the tracked sample of the first.
    "config.json",
    "hidden-repos.json",
}


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.split()


@pytest.fixture(scope="module")
def tracked() -> list[str]:
    return _tracked_files()


@pytest.fixture(scope="module")
def references(tracked: list[str]) -> dict[str, list[tuple[str, int]]]:
    """Every `backticked` token in a comment or docstring → where it was said."""
    found: dict[str, list[tuple[str, int]]] = {}
    for rel in tracked:
        if not rel.endswith(".py"):
            continue
        src = (REPO_ROOT / rel).read_text(errors="ignore")
        try:
            toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            continue
        for tok in toks:
            is_doc = tok.type == tokenize.STRING and tok.string[:3] in ('"""', "'''")
            if tok.type != tokenize.COMMENT and not is_doc:
                continue
            for name in _BACKTICK_RE.findall(tok.string):
                found.setdefault(name.strip(), []).append((rel, tok.start[0]))
    return found


@pytest.fixture(scope="module")
def defined_symbols(tracked: list[str]) -> set[str]:
    """Every name this repo binds: defs, classes, assignments, args, attributes."""
    names: set[str] = set()
    for rel in tracked:
        if not rel.endswith(".py"):
            continue
        try:
            tree = ast.parse((REPO_ROOT / rel).read_text(errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
        names.add(Path(rel).stem)
    return names


@pytest.fixture(scope="module")
def literals(tracked: list[str]) -> str:
    """Concatenated non-Python sources plus Python text, for string-literal hits.

    A config field (`use_worktree`), a starship module (`branch_identity`) and a
    cache-cell stem are never Python *definitions* — they live as string literals
    or TOML keys, so a definition-only check would reject all three.
    """
    keep = (".py", ".toml", ".json", ".txt", ".sh", ".yml", ".yaml", ".rb", ".md")
    return "\n".join(
        (REPO_ROOT / rel).read_text(errors="ignore")
        for rel in tracked
        if rel.endswith(keep)
    )


def test_backticked_symbols_in_comments_still_exist(
    references: dict[str, list[tuple[str, int]]],
    defined_symbols: set[str],
    literals: str,
) -> None:
    """A snake_case name in a comment must resolve somewhere in the tree.

    Caught three comments explaining code by a name it had stopped having:
    branch_pill (the starship module is now `branch_identity`),
    _resolve_linear_block (now `_prefetch_linear_blocks`) and
    github_done_on_merge (folded into `tickets.close_on_merge`).

    The dead names above are deliberately not backticked — this module scans
    itself, and a backtick is the mark of a name that resolves *now*.
    """
    stale: list[str] = []
    for name, sites in sorted(references.items()):
        if not _SYMBOL_RE.match(name):
            continue
        if name in _EXTERNAL_SYMBOLS or name in defined_symbols:
            continue
        if re.search(rf"""["']{re.escape(name)}["']""", literals):
            continue
        if re.search(rf"^\s*\[?custom\.{re.escape(name)}", literals, re.M):
            continue
        where = ", ".join(f"{f}:{n}" for f, n in sites[:3])
        stale.append(f"  `{name}` -> {where}")
    assert not stale, (
        "Comments name symbols that no longer exist. Rename the reference to "
        "the current name, or add it to _EXTERNAL_SYMBOLS if it belongs to a "
        "dependency:\n" + "\n".join(stale)
    )


def test_backticked_paths_in_comments_still_exist(
    references: dict[str, list[tuple[str, int]]],
    tracked: list[str],
) -> None:
    """A file named in a comment must be a file, matched by path suffix.

    Comments usually name a file by basename (`config.py`) or partial path
    (`lib/hidden.py`), so a suffix match is the right resolution — an exact-path
    check would reject nearly every correct reference.
    """
    stale: list[str] = []
    for name, sites in sorted(references.items()):
        base = name.split("::")[0]
        if not _PATH_RE.match(base) or base in _EXTERNAL_PATHS:
            continue
        if any(rel == base or rel.endswith("/" + base) for rel in tracked):
            continue
        where = ", ".join(f"{f}:{n}" for f, n in sites[:3])
        stale.append(f"  `{name}` -> {where}")
    assert not stale, (
        "Comments name files that do not exist. Fix the path, or add it to "
        "_EXTERNAL_PATHS if it lives in another repo:\n" + "\n".join(stale)
    )
