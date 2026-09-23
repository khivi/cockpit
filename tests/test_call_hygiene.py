"""Several AGENTS.md rules ban a symbol from appearing at all — a module that
must never call `signal.signal`, never import `cockpit.lib.cache`, never name
`plan.md`. A behaviour test cannot cover any of these: there is no observable
effect to assert on when the banned thing is simply absent, only the presence
or absence of a reference in source. So this module walks the tree with `ast`
instead, the same approach `test_comment_references.py` uses for prose.

One family of small AST helpers, reused across the assertions below rather
than fourteen bespoke greps — greps because the same handful of *shapes*
recur: "is this name referenced/called/imported anywhere in this file",
"does this call shell out to this literal command", "does this function's
call graph reach that one", "does this file write a specific cache key".
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COCKPIT_ROOT = REPO_ROOT / "cockpit"


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


# ── generic AST helpers ──────────────────────────────────────────────────


def _referenced_names(tree: ast.AST) -> set[str]:
    """Every name this tree mentions: identifiers, attribute accesses, and
    the names bound by an import. Broad on purpose — this is the check for
    "does X appear anywhere in this file as a symbol", which is what most of
    the "never references Y" rules mean.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _called_names(tree: ast.AST) -> set[str]:
    """The callee of every `Call` node: `foo()` -> "foo", `x.foo()` -> "foo".

    Narrower than `_referenced_names` on purpose: a rule phrased as "never
    CALLS X" must not trip on a same-named local variable, which
    `worktree_table.py`'s `ticket_url = strip_control(...)` is exactly.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _imported_module_paths(tree: ast.AST) -> set[str]:
    """Every module path an import statement could resolve to, dotted and
    with leading dots preserved for relative imports.

    `import a.b` -> {"a.b"}. `from a.b import c` -> {"a.b", "a.b.c"} (the
    second form covers `from cockpit.lib import cache` binding the module
    itself under a plain name). `from . import c` -> {".", ".c"}.
    """
    paths: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                paths.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            base = f"{prefix}{node.module}" if node.module else prefix
            paths.add(base)
            for alias in node.names:
                paths.add(f"{base}.{alias.name}")
    return paths


def _call_qualname(node: ast.Call) -> str | None:
    """`os.replace(...)` -> "os.replace"; `replace(...)` -> "replace"; a call
    through anything deeper than one attribute hop (`a.b.c()`) -> None,
    which is fine for every exact dotted-call check below (none needs it)."""
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    if isinstance(func, ast.Name):
        return func.id
    return None


def _dotted_calls(tree: ast.AST) -> set[str]:
    return {
        q
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and (q := _call_qualname(node)) is not None
    }


def _call_argument_phrases(tree: ast.AST) -> list[str]:
    """Every Call's positional string-literal arguments, space-joined into
    one phrase per call.

    Models what a real shell-out looks like — `run(["claude", "mcp",
    "list"])` or `run("claude mcp list")` — without matching prose in a
    comment or docstring, neither of which is ever a Call argument.
    """
    phrases: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        parts: list[str] = []
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                parts.append(arg.value)
            elif isinstance(arg, ast.List | ast.Tuple):
                for elt in arg.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        parts.append(elt.value)
        if parts:
            phrases.append(" ".join(parts))
    return phrases


def _call_graph(tree: ast.Module) -> dict[str, set[str]]:
    """Map each function/method defined in `tree` to every name it calls
    (including calls made by functions nested inside it).

    A name resolution only within the tree's own defined functions — good
    enough for "does calling A ever reach B" when both live in files this
    module controls; it can't follow a call through an alias or a dict of
    callables, which none of the paths checked below need.
    """
    graph: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            graph[node.name] = _called_names(node)
    return graph


def _merge_graphs(*graphs: dict[str, set[str]]) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for graph in graphs:
        for name, called in graph.items():
            merged.setdefault(name, set()).update(called)
    return merged


def _reachable(graph: dict[str, set[str]], start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        stack.extend(graph.get(name, ()))
    return seen


# ── tui.signals.no-signal-signal ─────────────────────────────────────────


@pytest.mark.covers("tui.signals.no-signal-signal")
def test_app_never_calls_signal_signal() -> None:
    """`signal.signal` raises off the main thread; the TUI registers every
    handler through `loop.add_signal_handler` instead (see `app.py`'s own
    module docstring). `signal.SIGUSR1` etc. as bare attribute references are
    fine and expected — only the *call* `signal.signal(...)` is banned."""
    tree = _parse(COCKPIT_ROOT / "tui" / "app.py")
    assert "signal.signal" not in _dotted_calls(tree)


# ── cache.renderer.never-reads-source-state ──────────────────────────────


@pytest.mark.covers("cache.renderer.never-reads-source-state")
def test_starship_is_a_strict_cache_reader() -> None:
    """starship's field printers are read-only: the daemon owns every cell,
    so a renderer that shells out itself would race the writer and could
    show a value the daemon never derived. `GitStatusCounts` is the one
    `.git` import — a plain data type, not a git-shelling call — so it is
    named explicitly rather than banning the whole module."""
    tree = _parse(COCKPIT_ROOT / "lib" / "starship.py")
    referenced = _referenced_names(tree)
    assert "subprocess" not in referenced
    assert "atomic_write" not in referenced
    assert not any(p.endswith(".gh") for p in _imported_module_paths(tree))

    git_tree = _parse(COCKPIT_ROOT / "lib" / "git.py")
    git_symbols = {
        node.name
        for node in git_tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    } - {"GitStatusCounts"}
    leaked = referenced & git_symbols
    assert not leaked, f"starship.py reaches into git.py's I/O surface: {leaked}"


# ── tui.diff-key.not-reintroduced ────────────────────────────────────────


@pytest.mark.covers("tui.diff-key.not-reintroduced")
def test_no_tui_module_references_render_diff() -> None:
    """`render_diff` has exactly one caller, `cockpit/diff.py` — the TUI's
    own `d` key was removed because the daemon's process can't be the one
    that runs `cmux diff` (stale source/target surface, see AGENTS.md).
    A second caller under `cockpit/tui/` would reopen that bug."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _iter_python_files(COCKPIT_ROOT / "tui")
        if "render_diff" in _referenced_names(_parse(path))
    ]
    assert not offenders, f"render_diff referenced under cockpit/tui/: {offenders}"


# ── prompts.plan-gate.never-daemon-read ──────────────────────────────────


@pytest.mark.covers("prompts.plan-gate.never-daemon-read")
def test_no_python_source_names_plan_md() -> None:
    """`plan.md` is a session-written artifact the daemon must never depend
    on — no tick, renderer or teardown may read or even name it, since a
    session might not have written one. Only the prompt templates that tell a
    session to write it may say its name, and those are `.txt`, so no `.py`
    file under `cockpit/` may contain the string at all."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _iter_python_files(COCKPIT_ROOT)
        if "plan.md" in path.read_text()
    ]
    assert not offenders, f"plan.md named in Python source: {offenders}"


# ── stdout.queue-writer.no-per-tick-redirect ─────────────────────────────


@pytest.mark.covers("stdout.queue-writer.no-per-tick-redirect")
def test_redirect_stdout_appears_nowhere_under_cockpit() -> None:
    """One process-wide `_QueueWriter` captures stdout; per-tick
    `redirect_stdout` would let the slow and fast tick threads race on the
    global stream. AST-based rather than grep so `app.py`'s own docstring
    explaining this (which names `redirect_stdout` in prose) doesn't count —
    a string inside a docstring is never a `Name`/`Attribute`/import."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _iter_python_files(COCKPIT_ROOT)
        if "redirect_stdout" in _referenced_names(_parse(path))
    ]
    assert not offenders, f"redirect_stdout referenced under cockpit/: {offenders}"


# ── slack.no-mcp-preflight ────────────────────────────────────────────────


@pytest.mark.covers("slack.no-mcp-preflight")
def test_no_source_shells_out_to_claude_mcp_list() -> None:
    """`claude mcp list` health-checks by connecting, which false-negatives
    on an async-handshaking managed connector — the exact setup this feature
    targets. The ban is repo-wide and provider-neutral: Slack, Linear, Jira
    and Trello all carry the same "retry-then-STOP, no pre-flight" rule.
    `tests/test_spawn.py::test_spawn_never_shells_out_to_claude_mcp_list`
    covers only the Linear spawn path at runtime; this is the tree-wide,
    every-provider half."""
    offenders: list[str] = []
    for path in _iter_python_files(COCKPIT_ROOT):
        for phrase in _call_argument_phrases(_parse(path)):
            if "claude mcp list" in phrase:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {phrase!r}")
    assert not offenders, f"shells out to claude mcp list: {offenders}"


# ── table.ticket-link.no-renderer-resolve ────────────────────────────────


@pytest.mark.covers("table.ticket-link.no-renderer-resolve")
def test_worktree_table_never_calls_ticket_url() -> None:
    """The ticket link is read from the daemon-cached `url` field
    (`cycle._stamp_ticket_urls`); a renderer resolving its own is the thing
    that rule exists to prevent. Checked as a *call* rather than a general
    reference: the file has a same-named local variable
    (`ticket_url = strip_control(_ticket_link(payload))`), which must not
    trip this."""
    tree = _parse(COCKPIT_ROOT / "tui" / "widgets" / "worktree_table.py")
    assert "ticket_url" not in _called_names(tree)


# ── ticket-inbox.screen.no-narrow-repos-for-marker ───────────────────────


@pytest.mark.covers("ticket-inbox.screen.no-narrow-repos-for-marker")
def test_tickets_screen_never_references_narrow_repos() -> None:
    """`narrow_repos` is the paid tiebreak fetch; the inbox screen's `?`/`!`
    markers are stage-one-only predictions computed by `app._ticket_routes`
    and handed in, so opening the modal must reach no network however many
    tickets it holds. Calling `narrow_repos` from the screen would pay that
    fetch on every repaint."""
    tree = _parse(COCKPIT_ROOT / "tui" / "widgets" / "tickets_screen.py")
    assert "narrow_repos" not in _referenced_names(tree)


# ── diff.resolution.no-configured-repo-required ──────────────────────────


@pytest.mark.covers("diff.resolution.no-configured-repo-required")
def test_diff_py_imports_neither_load_config_nor_resolve_target() -> None:
    """`cockpit diff` resolves purely from cwd via `git.worktree_root` and
    must work in any git repo, registered or not — routing it through
    `close.py::_resolve_target` (which requires a configured repo) or
    reading config directly would break that for an unregistered repo."""
    referenced = _referenced_names(_parse(COCKPIT_ROOT / "diff.py"))
    assert "load_config" not in referenced
    assert "_resolve_target" not in referenced


# ── events.cursor-file.not-cache-cell ─────────────────────────────────────


@pytest.mark.covers("events.cursor-file.not-cache-cell")
def test_events_py_never_imports_cache_module() -> None:
    """The `cmux events` resume cursor is cmux's own bookmark, not cockpit
    inventory — it must never be routed through `lib.cache`'s flat-cell
    machinery. `cockpit.lib.config` (a different module, `CACHE_DIR` the
    constant) is imported here and is not what this bans."""
    tree = _parse(COCKPIT_ROOT / "lib" / "events.py")
    paths = _imported_module_paths(tree)
    assert not any(p == "cockpit.lib.cache" or p.endswith(".cache") for p in paths)


# ── folds.collapse.no-read-back ──────────────────────────────────────────


@pytest.mark.covers("folds.collapse.no-read-back")
def test_cycle_py_never_reads_is_collapsed() -> None:
    """Both trailing folds are born collapsed at create time only; reading
    `is_collapsed` back to "correct" a fold the user deliberately expanded
    would slam it shut on the next cycle. cmux's own field name
    (`is_collapsed: false`) appears only in a comment describing cmux's API,
    never as code here."""
    tree = _parse(COCKPIT_ROOT / "orchestrators" / "cycle.py")
    assert "is_collapsed" not in _referenced_names(tree)


# ── config.atomic-write.no-reinline ──────────────────────────────────────


@pytest.mark.covers("config.atomic-write.no-reinline")
def test_atomic_write_text_is_the_only_temp_then_replace_writer() -> None:
    """`config.py::_atomic_write_text` is the one place that performs a
    literal `os.replace(tmp, path)` — every other atomic write (including
    `lib.cache.atomic_write` and `lib.seed_queue.enqueue`, both PID-suffixed
    temp files too) uses `Path.replace`, a different call node, so this
    checks the exact dotted call rather than the general write shape."""
    config_path = COCKPIT_ROOT / "lib" / "config.py"
    offenders: list[str] = []
    for path in _iter_python_files(COCKPIT_ROOT):
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if "os.replace" not in _dotted_calls(node):
                continue
            if path == config_path and node.name == "_atomic_write_text":
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)}::{node.name}")
    assert not offenders, f"os.replace called outside _atomic_write_text: {offenders}"


# ── cache.session-cells.daemon-never-writes ──────────────────────────────

# Every stem `claude.py::stash_from_stdin` writes. The daemon may still READ
# "cost" (it derives wt-cost from it) — this set is checked only against
# atomic_write(session_cache(...)) call pairs, never against read_text(...)
# ones, so that exception falls out of the call shape rather than needing an
# explicit carve-out.
_SESSION_CELL_STEMS = frozenset(
    {"context", "rate-limit-5h", "model", "permission-mode", "transcript-path", "cost"}
)


def _session_cell_writes(tree: ast.AST) -> set[str]:
    """Every stem written via `atomic_write(session_cache(stem, ...), ...)`
    in this tree."""
    stems: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _call_qualname(node) == "atomic_write"):
            continue
        if not node.args:
            continue
        inner = node.args[0]
        if not (
            isinstance(inner, ast.Call) and _call_qualname(inner) == "session_cache"
        ):
            continue
        if not inner.args:
            continue
        stem_arg = inner.args[0]
        if isinstance(stem_arg, ast.Constant) and isinstance(stem_arg.value, str):
            stems.add(stem_arg.value)
    return stems


@pytest.mark.covers("cache.session-cells.daemon-never-writes")
def test_daemon_never_writes_a_session_scoped_cell() -> None:
    """Session cells are written exactly once, from `claude.py::
    stash_from_stdin` off the statusLine hook — the daemon has no visibility
    into a session's own context/rate-limit/cost, so a daemon-side write
    would fabricate a value with no source of truth behind it."""
    daemon_files = [
        COCKPIT_ROOT / "cockpit.py",
        *sorted((COCKPIT_ROOT / "orchestrators").glob("*.py")),
    ]
    offenders: dict[str, set[str]] = {}
    for path in daemon_files:
        written = _session_cell_writes(_parse(path)) & _SESSION_CELL_STEMS
        if written:
            offenders[str(path.relative_to(REPO_ROOT))] = written
    assert not offenders, f"daemon writes a session-scoped cell: {offenders}"


# ── rowaction.z-full-cycle.no-move-to-fast-tick ──────────────────────────


@pytest.mark.covers("rowaction.z-full-cycle.no-move-to-fast-tick")
def test_fast_tick_never_reaches_reconcile_review_groups() -> None:
    """`_reconcile_review_groups` needs `folds`, which is only built when
    `cycle_all` runs unscoped (`only_repo is None`) — the fast tick never
    passes that, so it must never call anything whose call graph reaches this
    pass. `restore_trailing_folds` IS reachable from `_fast_tick` and is the
    allowed, narrower self-heal (replay only, never a dissolve) — this
    doesn't ban that, only the pass it stands in for."""
    cockpit_graph = _call_graph(_parse(COCKPIT_ROOT / "cockpit.py"))
    cycle_graph = _call_graph(_parse(COCKPIT_ROOT / "orchestrators" / "cycle.py"))
    combined = _merge_graphs(cockpit_graph, cycle_graph)
    reachable = _reachable(combined, "_fast_tick")
    assert "_reconcile_review_groups" not in reachable
    assert "restore_trailing_folds" in reachable  # sanity: the graph isn't empty/broken


# ── update-stale.mechanism.no-local-rebase ────────────────────────────────


@pytest.mark.covers("update-stale.mechanism.no-local-rebase")
def test_update_stale_branches_never_shells_a_local_rebase_or_force_push() -> None:
    """`cycle.py::_update_stale_branches` brings a PR's head up to date via
    GitHub's server-side `updatePullRequestBranch` mutation, never a local
    `git rebase` + force-push (see AGENTS.md's update-stale-branches
    section): a conflicted local rebase could strand a `rebase-merge` state
    that reads as dirty and wedges teardown, and no force-push may originate
    from an unattended process.

    Scoped to what actually RUNS the update — the call graph reachable from
    `_update_stale_branches` across cycle.py, git.py, gh.py and config.py —
    rather than a tree-wide ban on the word "rebase": `git.py::
    resync_to_origin` legitimately runs a plain `reset --hard` after a
    REBASE-method update (local reconciliation of an already server-rewritten
    ref, not the banned mechanism), and `git.py` separately carries unrelated
    rebase-*state* helpers (`_rebase_head_name` reads `rebase-merge/head-name`
    off disk; it shells no git subcommand and isn't reachable from this path
    at all) that a bare `"rebase" not in referenced_names` sweep would trip
    on for no reason.
    """
    files = {
        "cycle": COCKPIT_ROOT / "orchestrators" / "cycle.py",
        "git": COCKPIT_ROOT / "lib" / "git.py",
        "gh": COCKPIT_ROOT / "lib" / "gh.py",
        "config": COCKPIT_ROOT / "lib" / "config.py",
    }
    trees = {name: _parse(path) for name, path in files.items()}
    combined = _merge_graphs(*(_call_graph(tree) for tree in trees.values()))
    reachable = _reachable(combined, "_update_stale_branches")
    assert "resync_to_origin" in reachable  # sanity: the graph isn't empty/broken

    offenders: list[str] = []
    for name, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if node.name not in reachable:
                continue
            for phrase in _call_argument_phrases(node):
                tokens = phrase.split()
                shells_rebase = "rebase" in tokens
                shells_force_push = "push" in tokens and (
                    "--force" in tokens or "-f" in tokens
                )
                if shells_rebase or shells_force_push:
                    offenders.append(f"{name}::{node.name}: {phrase!r}")
    assert (
        not offenders
    ), f"update-stale path shells a local rebase or force-push: {offenders}"
