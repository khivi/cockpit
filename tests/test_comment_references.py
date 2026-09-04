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

# What counts as text worth scanning for a string-literal hit. One definition,
# because the two `literals*` fixtures must vouch against the same corpus —
# differing only in which files are excluded, never in which types count.
_CORPUS_SUFFIXES = (
    ".py",
    ".toml",
    ".json",
    ".txt",
    ".sh",
    ".yml",
    ".yaml",
    ".rb",
    ".md",
)

# Names that correctly resolve outside this repo. Each entry is a promise that
# the name belongs to a dependency or another project — not an excuse to park a
# stale reference here.
_EXTERNAL_SYMBOLS = {
    # Textual framework internals cockpit overrides or defers to.
    "_on_click",
    "_on_mouse_move",
    "action_back",
    "hover_coordinate",
    # Rich's own cell-width measurement, which the header bar's glyph rules
    # are stated against.
    "cell_len",
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
    # A GitHub ruleset rule name, in the tap's protection config.
    "non_fast_forward",
    # cmux's own error code, quoted from an observed failure.
    "not_found",
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
    # The plan gate's artifact, written into the worktree by a spawned session
    # and deliberately never tracked — see the prompt-templates section.
    "plan.md",
    # The brew formula's single source of truth is the tap repo
    # (khivi/homebrew-cockpit); vendoring a copy here would drift.
    "Formula/cockpit.rb",
}

# The agent instruction set, scanned for backticked references. A tuple rather
# than a bare string because the set may grow again if AGENTS.md is ever split.
_DOC_SOURCES = ("AGENTS.md",)

# What the instruction set must not vouch for itself through — the sources plus
# every alias of them. `.github/copilot-instructions.md` is a symlink to
# AGENTS.md and `read_text()` follows it, so leaving it in the `literals` corpus
# would let the prose confirm its own names and the check would pass on
# anything. It is excluded here but NOT scanned above: same bytes under a second
# name would double-report every finding against a path nobody edits.
_DOC_FILES = (*_DOC_SOURCES, ".github/copilot-instructions.md")


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
    return "\n".join(
        (REPO_ROOT / rel).read_text(errors="ignore")
        for rel in tracked
        if rel.endswith(_CORPUS_SUFFIXES)
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


def _unresolved_paths(
    refs: dict[str, list[tuple[str, int]]], tracked: list[str]
) -> list[str]:
    """Backticked file references in `refs` that match no tracked path.

    Shared by the comment and instruction-set checks: the resolution rule is one
    rule, and duplicating it would mean fixing it twice with nothing to flag the
    half that got missed — the drift this module exists to catch.
    """
    stale: list[str] = []
    for name, sites in sorted(refs.items()):
        # A repo-root script is normally named the way it is invoked
        # (`./dev.sh`, `./setup.sh`), and the suffix match below can never
        # resolve that: tracked paths are repo-relative with no leading "./".
        base = name.split("::")[0].removeprefix("./")
        if not _PATH_RE.match(base) or base in _EXTERNAL_PATHS:
            continue
        if any(rel == base or rel.endswith("/" + base) for rel in tracked):
            continue
        where = ", ".join(f"{f}:{n}" for f, n in sites[:3])
        stale.append(f"  `{name}` -> {where}")
    return stale


def test_backticked_paths_in_comments_still_exist(
    references: dict[str, list[tuple[str, int]]],
    tracked: list[str],
) -> None:
    """A file named in a comment must be a file, matched by path suffix.

    Comments usually name a file by basename (`config.py`) or partial path
    (`lib/hidden.py`), so a suffix match is the right resolution — an exact-path
    check would reject nearly every correct reference.
    """
    stale = _unresolved_paths(references, tracked)
    assert not stale, (
        "Comments name files that do not exist. Fix the path, or add it to "
        "_EXTERNAL_PATHS if it lives in another repo:\n" + "\n".join(stale)
    )


def _is_doc(rel: str) -> bool:
    return rel in _DOC_FILES


@pytest.fixture(scope="module")
def doc_references(tracked: list[str]) -> dict[str, list[tuple[str, int]]]:
    """Every `backticked` token in the agent instruction set → where it was said.

    Scans AGENTS.md only, not the `.github/copilot-instructions.md` symlink
    pointing at it — the same bytes under a second name would double-report
    every finding against a path nobody edits.
    """
    found: dict[str, list[tuple[str, int]]] = {}
    for rel in tracked:
        if rel not in _DOC_SOURCES:
            continue
        for lineno, line in enumerate((REPO_ROOT / rel).read_text().split("\n"), 1):
            for name in _BACKTICK_RE.findall(line):
                found.setdefault(name.strip(), []).append((rel, lineno))
    return found


@pytest.fixture(scope="module")
def literals_outside_docs(tracked: list[str]) -> str:
    """`literals`, minus the instruction set — so it cannot vouch for itself."""
    return "\n".join(
        (REPO_ROOT / rel).read_text(errors="ignore")
        for rel in tracked
        if rel.endswith(_CORPUS_SUFFIXES) and not _is_doc(rel)
    )


def test_backticked_symbols_in_the_instruction_set_still_exist(
    doc_references: dict[str, list[tuple[str, int]]],
    defined_symbols: set[str],
    literals_outside_docs: str,
) -> None:
    """AGENTS.md and docs/invariants/* name symbols that must still resolve.

    Same rule as the comment check above, applied to the prose that is dense
    with rationale — and rationale names things, so a rename leaves the old name
    behind as a claim that reads fine and means nothing. Caught two backticked
    names for code that had been deleted: github_done_on_merge and
    move_workspace_group_to_start, both of which the prose describes precisely
    *because* they are gone. Naming a dead symbol is fine and often necessary;
    the convention is that it goes unbackticked, since a backtick is the mark of
    a name that resolves now.
    """
    stale: list[str] = []
    for name, sites in sorted(doc_references.items()):
        if not _SYMBOL_RE.match(name):
            continue
        if name in _EXTERNAL_SYMBOLS or name in defined_symbols:
            continue
        if re.search(rf"""["']{re.escape(name)}["']""", literals_outside_docs):
            continue
        where = ", ".join(f"{f}:{n}" for f, n in sites[:3])
        stale.append(f"  `{name}` -> {where}")
    assert not stale, (
        "The instruction set names symbols that no longer exist. Rename the "
        "reference, un-backtick it if the point is that it is gone, or add it "
        "to _EXTERNAL_SYMBOLS if it belongs elsewhere:\n" + "\n".join(stale)
    )


def test_backticked_paths_in_the_instruction_set_still_exist(
    doc_references: dict[str, list[tuple[str, int]]],
    tracked: list[str],
) -> None:
    """A file named in the instruction set must be a file, by path suffix."""
    stale = _unresolved_paths(doc_references, tracked)
    assert not stale, (
        "The instruction set names files that do not exist. Fix the path, or "
        "add it to _EXTERNAL_PATHS if it lives in another repo:\n" + "\n".join(stale)
    )


def test_a_repo_root_script_resolves_when_named_as_invoked(tracked):
    """`./dev.sh` must resolve. Tracked paths are repo-relative with no leading
    "./", so without normalization the suffix match can never hit one — and a
    root script is normally named the way it is run."""
    assert "dev.sh" in tracked
    base = "./dev.sh".removeprefix("./")
    assert any(rel == base or rel.endswith("/" + base) for rel in tracked)
