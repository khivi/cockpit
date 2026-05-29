"""cmux CLI wrapper, workspace queries, and cockpit pill management.

Backend *policy* (which of cmux/limux is in effect) lives in
`scripts.lib.tool`; this module owns the *implementation* — the `cmux()` CLI
wrapper, ref parsing, pill management, and the per-backend actions
(`workspace_cwds`, `spawn_workspace`) that branch on `tool.is_limux()`.
Callers needing the policy predicates import `resolve_tool` / `is_cmux` /
`is_limux` from `scripts.lib.tool`; everything else comes from here.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import run
from .cache import find_pr_payload_by_number
from .colors import CMUX_COLOR_ANSI, bold, dim
from .config import discover_repo
from .issue_color import issue_color
from .log_format import verb
from .gh import PR
from .git import Worktree, worktrees
from .nudges import NudgePref
from .pills import decide_pills
from .prompts import build_orphan_prompt, build_pr_prompt, claude_command
from . import tool

GREEN = "#16a34a"
RED = "#eb445a"
ORANGE = "#ff9500"
BLUE = "#3b82f6"
GREY = "#6b7280"
YELLOW = "#facc15"

# cmux's named workspace-entry colors (`workspace-action --action set-color`).
# These tint the whole sidebar row, distinct from the per-state pill colors
# above. cmux also accepts #RRGGBB, but cockpit only exposes the names so a
# repo's `sidebar_color` stays theme-agnostic (cmux maps the name per theme).
# Sourced from `colors.CMUX_COLOR_ANSI` so the valid set and the log-echo
# colorizers can't drift apart.
WORKSPACE_COLORS = frozenset(CMUX_COLOR_ANSI)

# Pill key kept for backward compatibility — older workspaces may have it set;
# apply_pills clears it every cycle to clean up.
COCKPIT_KEY = "cockpit_pr"

PARKED_KEY = "parked"
PARKED_ICON = "💤"

LOOP_KEY = "loop"
LOOP_ICON = "🔁"

ORPHAN_KEY = "worktree"
ORPHAN_ICON = "🛠️"

WIP_KEY = "wip"
WIP_ICON = "✏️"

STALE_KEY = "stale"
STALE_ICON = "↻"

MUTED_KEY = "muted"
MUTED_ICON = "🔇"

ACTIONABLE_KEYS = (
    "ci",
    "comments",
    "merge",
    "draft",
    "approved",
    "rebase",
    "wip",
    MUTED_KEY,
)

OWNER_KEY = "owner"
OWNER_ICON = "👥"

# Verbs that need cmux specifically — limux fork lacks the persistent-pill
# and workspace-action (set-color) APIs. Gated here so they no-op on limux
# instead of erroring; repo sidebar colors are an additive cmux-only nicety.
_PILL_VERBS = frozenset({"set-status", "clear-status", "workspace-action"})


class CmuxUnavailable(RuntimeError):
    """Raised when the workspace backend (cmux/limux) refuses or fails a query.

    Callers needing authoritative workspace state must let this propagate;
    best-effort callers (status pings, close-by-ref) should keep `check=False`
    and ignore empty output.
    """


def _has_pill(lines: list[str], *keys: str) -> bool:
    """True if any `KEY=` line is present (KEY ∈ keys)."""
    return any(line.lstrip().startswith(k + "=") for line in lines for k in keys)


def _set_status(ref: str, key: str, value: str, color: str) -> None:
    cmux("set-status", key, value, "--workspace", ref, "--color", color, check=False)


def _clear_status(ref: str, key: str) -> None:
    cmux("clear-status", key, "--workspace", ref, check=False)


def _apply_count_pill(
    ref: str, key: str, icon: str, count: int, *, color: str = ORANGE
) -> None:
    """Set `KEY=ICON N` when count>0, else clear it."""
    if count > 0:
        _set_status(ref, key, f"{icon} {count}", color)
    else:
        _clear_status(ref, key)


def set_workspace_color(ref: str, color: str) -> None:
    """Tint workspace `ref`'s sidebar entry to `color` (a `WORKSPACE_COLORS`
    name). Best-effort and cmux-only — no-ops on limux (workspace-action is
    gated in `_PILL_VERBS`) and never raises, so a missed tint can't stall a
    reconcile. Callers validate `color` against `WORKSPACE_COLORS` first.
    """
    cmux(
        "workspace-action",
        "--action",
        "set-color",
        "--color",
        color,
        "--workspace",
        ref,
        check=False,
    )


def _resolve_binary(verb: str) -> str | None:
    """Pick a workspace-CLI binary for `verb`. Pills require cmux; everything
    else accepts cmux or its limux fork. Honours cfg['tool'].
    """
    backend = tool.resolve_tool()
    if backend == "none":
        return None
    if verb in _PILL_VERBS and backend != "cmux":
        return None  # limux can't do pills
    return backend if shutil.which(backend) else None


def require_workspace_binary() -> None:
    """Exit cleanly with a one-liner if no workspace backend is available.
    Use at the top of slash-command entry scripts so the user gets a useful
    message instead of a Python traceback.
    """
    backend = tool.resolve_tool()
    if backend != "none" and shutil.which(backend):
        return
    msg = (
        "cockpit: tool=none in config — workspace commands disabled"
        if backend == "none"
        else f"cockpit: '{backend}' not found on PATH"
    )
    print(msg, file=sys.stderr)
    sys.exit(2)


def cmux(*args: str, check: bool = True) -> str:
    verb = args[0] if args else ""
    binary = _resolve_binary(verb)
    if binary is None:
        if check:
            backend = tool.resolve_tool()
            hint = (
                " (pills require cmux; current tool is limux)"
                if verb in _PILL_VERBS and backend == "limux"
                else f" (current tool: {backend})"
            )
            raise FileNotFoundError(f"cockpit: '{verb}' unavailable{hint}")
        return ""
    return run([binary, *args], check=check)


def apply_wip_pill(ref: str, dirty_count: int) -> None:
    """Set or clear the WIP pill on `ref` based on dirty-file count."""
    _apply_count_pill(ref, WIP_KEY, WIP_ICON, dirty_count)


def apply_stale_pill(ref: str, behind_base: int) -> None:
    """Set or clear the rebase-staleness pill on `ref`.

    Surfaces "branch is N commits behind base" on orphan workspaces, where
    no PR-side conflict pill will catch it. PR-tracked workspaces already
    get conflict signal from PR review state, so this pill is intentionally
    omitted there.
    """
    _apply_count_pill(ref, STALE_KEY, STALE_ICON, behind_base)


def list_workspaces() -> list[str]:
    out = cmux("list-workspaces", check=False)
    refs: list[str] = []
    for line in out.splitlines():
        m = re.search(r"(workspace:[\w-]+)", line)
        if m:
            refs.append(m.group(1))
    return refs


def wait_for_new_workspace_ref(
    existing: set[str], *, attempts: int = 20, delay: float = 0.15
) -> str | None:
    """Poll list-workspaces for a ref that wasn't in `existing`. Workaround for
    `cmux new-workspace` not returning the new ref on stdout.
    """
    for _ in range(attempts):
        time.sleep(delay)
        diff = set(list_workspaces()) - existing
        if diff:
            return sorted(diff)[0]
    return None


def spawn_workspace(name: str, cwd: Path, command: str) -> str | None:
    """Spawn a new workspace and return its ref, or None on failure.

    cmux: passes --name/--focus, polls list-workspaces for the new ref since
    `cmux new-workspace` does not echo it on stdout.

    limux: passes --cwd/--command only (limux's new-workspace lacks --name
    and --focus). Parses the ref from stdout ("OK workspace:<uuid>") and
    follows up with `rename-workspace` so cockpit's name conventions match.
    """
    if tool.is_limux():
        out = cmux(
            "new-workspace",
            "--cwd",
            str(cwd),
            "--command",
            command,
            check=False,
        )
        m = re.search(r"(workspace:[\w-]+)", out)
        if m is None:
            return None
        ref = m.group(1)
        cmux("rename-workspace", "--workspace", ref, name, check=False)
        return ref

    before = set(list_workspaces())
    cmux(
        "new-workspace",
        "--name",
        name,
        "--cwd",
        str(cwd),
        "--command",
        command,
        "--focus",
        "false",
    )
    return wait_for_new_workspace_ref(before)


def nudge_if_idle(
    ref: str,
    message: str,
    *,
    dry: bool = False,
    tag: str = "",
    pr_number: int | None = None,
    category: str | None = None,
) -> bool:
    """Send `message` + enter to workspace `ref` if it's idle and not parked.

    For PR-attached nudges (`pr_number` set), check the file-backed mute
    state in `lib.nudges` so the user's `cockpit nudge mute` survives daemon
    restarts. For orphan (no-PR) nudges, fire unconditionally when idle.

    Always gates on cmux pills: skips if `idle=` is absent or `parked=` is
    present, so a transient runtime override still works.

    There is no time-based throttle. The slow tick's cadence
    (`slow_poll_interval_seconds`, default 300s) is the implicit rate limit
    — each tick re-evaluates and re-fires if the underlying issue persists.
    """
    if pr_number is not None and category is not None:
        from . import nudges

        if not nudges.should_nudge(pr_number, category):
            return False
    status_lines = cmux("list-status", "--workspace", ref, check=False).splitlines()
    if not _has_pill(status_lines, "idle"):
        return False
    if _has_pill(status_lines, PARKED_KEY):
        return False
    if dry:
        print(f"  [dry] nudge {tag} → {ref}: {message[:70]}", flush=True)
        return False
    try:
        cmux("send", "--workspace", ref, message, check=True)
        cmux("send-key", "--workspace", ref, "enter", check=True)
    except (RuntimeError, FileNotFoundError) as e:
        print(f"  warn: {tool.resolve_tool()} send failed for {ref}: {e}", flush=True)
        return False
    if pr_number is not None and category is not None:
        from . import nudges

        nudges.record_nudge(pr_number, category)
    return True


def workspace_names() -> dict[str, str]:
    """{ref: name} from `cmux list-workspaces` or `limux --json list-workspaces`.

    Raises `CmuxUnavailable` if the query exits nonzero — callers must not treat
    an empty dict as "no workspaces" when the backend itself failed.
    """
    try:
        out = cmux("list-workspaces", check=True)
    except RuntimeError as e:
        raise CmuxUnavailable(f"list-workspaces failed: {e}") from e
    names: dict[str, str] = {}
    for line in out.splitlines():
        m = re.search(r"(workspace:[\w-]+)\s+(\S+)", line)
        if m:
            names[m.group(1)] = m.group(2)
    return names


def workspace_cwds() -> dict[str, Path]:
    """{ref: current_directory} via `cmux rpc workspace.list` (cmux) or `limux --json list-workspaces` (limux).

    Raises `CmuxUnavailable` on nonzero rc or unparsable output, so a backend
    hiccup is not misread as an empty workspace set.

    limux uses `--json` as a global flag (before the command), so the limux
    path bypasses the `cmux()` wrapper — `cmux("--json", ...)` would still
    work, but the global flag is clearer as a direct `run([...])` invocation.
    """
    if tool.is_limux():
        cwd_key = "cwd"
        label = "limux --json list-workspaces"
        try:
            out = run(["limux", "--json", "list-workspaces"], check=True)
        except RuntimeError as e:
            raise CmuxUnavailable(f"{label} failed: {e}") from e
    else:
        cwd_key = "current_directory"
        label = "rpc workspace.list"
        try:
            out = cmux("rpc", "workspace.list", "{}", check=True)
        except RuntimeError as e:
            raise CmuxUnavailable(f"{label} failed: {e}") from e

    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise CmuxUnavailable(f"{label} returned non-JSON: {e}") from e
    cwds: dict[str, Path] = {}
    for ws in data.get("workspaces", []):
        ref = ws.get("ref")
        cwd = ws.get(cwd_key)
        if ref and cwd:
            cwds[ref] = Path(cwd)
    return cwds


def workspace_state() -> tuple[dict[str, str], dict[str, Path]]:
    """Fetch names and cwds in parallel."""
    with ThreadPoolExecutor(max_workers=2) as ex:
        names_fut = ex.submit(workspace_names)
        cwds_fut = ex.submit(workspace_cwds)
        return names_fut.result(), cwds_fut.result()


def workspace_is_idle(ref: str) -> bool:
    """True if the workspace has an `idle=` pill (set by the Stop hook)."""
    out = cmux("list-status", "--workspace", ref, check=False)
    return _has_pill(out.splitlines(), "idle")


def workspace_is_parked(ref: str) -> bool:
    """True if the user manually set the `parked=` pill (done-waiting marker)."""
    out = cmux("list-status", "--workspace", ref, check=False)
    return _has_pill(out.splitlines(), PARKED_KEY)


def find_cockpit_workspaces(
    prs: list[PR],
    wts: list[Worktree],
    *,
    names: dict[str, str] | None = None,
    cwds: dict[str, Path] | None = None,
) -> dict[str, tuple[PR, Worktree]]:
    """Match cmux workspaces to (PR, Worktree) by cwd → wt → branch → PR.

    Path-first matching catches workspaces whose name doesn't match the worktree
    dir (e.g. ticket-named workspaces rooted in a feature worktree). Falls back
    to name match. Returns {ref: (PR, Worktree)}.
    """
    wt_by_path = {wt.path.resolve(): wt for wt in wts}
    wt_by_name = {wt.short: wt for wt in wts}
    pr_by_branch = {pr.branch: pr for pr in prs}
    if cwds is None:
        cwds = workspace_cwds()
    if names is None:
        names = workspace_names()
    out: dict[str, tuple[PR, Worktree]] = {}
    for ref in set(cwds) | set(names):
        wt = wt_by_path.get(cwds[ref].resolve()) if ref in cwds else None
        if wt is None:
            wt = wt_by_name.get(names.get(ref, ""))
        if wt is None:
            continue
        pr = pr_by_branch.get(wt.branch)
        if pr is None:
            continue
        out[ref] = (pr, wt)
    return out


def _muted_label(p: dict) -> str:
    """Sidebar label for the muted pill. Categories are sorted upstream."""
    if p.get("scope") == "all":
        return f"{MUTED_ICON} muted"
    cats = "+".join(p.get("categories") or [])
    return f"{MUTED_ICON} muted: {cats}" if cats else f"{MUTED_ICON} muted"


_CMUX_RENDERERS = {
    "muted": lambda p: (MUTED_KEY, _muted_label(p), YELLOW),
    "rebase": lambda _p: ("rebase", "🔄 rebasing", ORANGE),
    "merge": lambda _p: ("merge", "🔀 merging", ORANGE),
    "wip": lambda p: ("wip", f"✏️ {p['count']} dirty", ORANGE),
    "ci_failed": lambda p: ("ci", f"❌ ci:{p['phase']}", RED),
    "ci_pending": lambda _p: ("ci", "⏳ ci pending", ORANGE),
    "ci_passed": lambda _p: ("ci", "✓ ci", GREEN),
    "ci_unknown": lambda _p: ("ci", "⚠️ ci error", RED),
    "unaddressed": lambda p: ("comments", f"💬 {p['count']} unaddressed", RED),
    "changes_requested": lambda _p: ("comments", "💬 changes requested", RED),
    "conflict": lambda _p: ("merge", "⚠️ conflict", ORANGE),
    "draft": lambda _p: ("draft", "📝 draft", GREY),
    "approved": lambda _p: ("approved", "✅ approved", GREEN),
    # `state` is footer-only; cmux already surfaces MERGED/CLOSED natively in
    # its sidebar, so the cockpit pill map drops it (None) to avoid double-
    # rendering. Load-bearing for merged-but-dirty workspaces where autoclose
    # is blocked and a non-OPEN PR persists in `ctx.prs` across cycles.
    "state": lambda _p: None,
}


def status_pills(
    pr: PR,
    wt: Worktree | None = None,
    self_user: str | None = None,
    pref: NudgePref | None = None,
) -> list[tuple[str, str, str]]:
    """(key, value, color) tuples for cmux set-status. Maps decide_pills output.

    When `self_user` is given and `pr.author` differs, prepends an `owner` pill
    so coworker-owned PRs are visible in the sidebar. Prepended so reversed
    set-order in `apply_pills` places it at the bottom of the visual stack.

    `pref` carries the daemon-resolved mute state; pure consumer — does not
    load it. See cycle.py for the single-authority pref load.
    """
    out: list[tuple[str, str, str]] = []
    if self_user and pr.author and pr.author != self_user:
        out.append((OWNER_KEY, f"{OWNER_ICON} @{pr.author}", BLUE))
    for p in decide_pills(pr, wt, pref):
        renderer = _CMUX_RENDERERS.get(p["kind"])
        if renderer is None:
            continue
        tup = renderer(p)
        if tup is not None:
            out.append(tup)
    return out


def apply_pills(
    ref: str,
    pr: PR,
    wt: Worktree | None = None,
    self_user: str | None = None,
    pref: NudgePref | None = None,
) -> frozenset[tuple[str, str, str]]:
    """Idempotently sync cmux pills; return the desired snapshot for diffing.

    cmux ordering rule: new pills prepend; re-setting an existing key keeps its
    slot. To force a deterministic order — and push cmux's own `claude_code`
    pill (e.g. "Needs input") to the bottom — clear all our keys first, then
    re-set in reverse display order. The `idle=` pill is owned by
    `hooks/cmux-idle-pill.sh` (Stop / UserPromptSubmit) — not touched here.
    """
    desired = tuple(status_pills(pr, wt, self_user, pref))
    # "cockpit_managed" is a one-release back-compat strip — remove next release.
    keys_to_clear = [*ACTIONABLE_KEYS, COCKPIT_KEY, OWNER_KEY, "cockpit_managed"]
    with ThreadPoolExecutor(max_workers=len(keys_to_clear)) as ex:
        for f in [ex.submit(_clear_status, ref, k) for k in keys_to_clear]:
            f.result()
    for key, value, color in reversed(desired):
        _set_status(ref, key, value, color)

    return frozenset(desired)


@dataclass
class WorkspaceMatch:
    ref: str
    name: str
    worktree: Worktree | None


def _pr_num_to_branch(pr_num: str) -> str:
    repo_cfg = discover_repo()
    repo_name = repo_cfg.get("name") if repo_cfg else None
    payload = find_pr_payload_by_number(pr_num, repo_name=repo_name)
    if payload is None:
        raise LookupError(f"PR #{pr_num} not in cockpit cache")
    branch: str = payload.get("branch") or ""
    if not branch:
        raise LookupError(f"PR #{pr_num} has no branch in cockpit cache")
    return branch


def resolve_workspace(query: str, repo_dir: Path) -> WorkspaceMatch:
    """Resolve `<pr|branch|slug>` against live cmux + git state.

    Match order: PR number (#N or N) via cache → worktree branch → workspace name.
    Raises LookupError on no match or ambiguity.
    """
    names = workspace_names()
    cwds = workspace_cwds()
    wts = worktrees(repo_dir)
    wt_by_path = {wt.path.resolve(): wt for wt in wts}
    wt_by_branch = {wt.branch: wt for wt in wts}

    wt_for_ref: dict[str, Worktree | None] = {
        ref: (wt_by_path.get(cwds[ref].resolve()) if ref in cwds else None)
        for ref in set(names) | set(cwds)
    }

    def _ref_for_worktree(wt: Worktree) -> str:
        candidates = [r for r, w in wt_for_ref.items() if w is wt]
        if not candidates:
            raise LookupError(f"worktree {wt.path} has no cmux workspace")
        if len(candidates) > 1:
            raise LookupError(
                f"worktree {wt.path} matches multiple workspaces: {sorted(candidates)}"
            )
        return candidates[0]

    pr_match = re.fullmatch(r"#?(\d+)", query)
    if pr_match:
        pr_num = pr_match.group(1)
        branch = _pr_num_to_branch(pr_num)
        wt = wt_by_branch.get(branch)
        if wt is None:
            raise LookupError(f"PR #{pr_num} (branch {branch!r}) has no worktree")
        ref = _ref_for_worktree(wt)
        return WorkspaceMatch(ref, names.get(ref, ""), wt)

    if query in wt_by_branch:
        wt = wt_by_branch[query]
        ref = _ref_for_worktree(wt)
        return WorkspaceMatch(ref, names.get(ref, ""), wt)

    slug_refs = [r for r, n in names.items() if n == query]
    if len(slug_refs) > 1:
        raise LookupError(
            f"slug {query!r} matches multiple workspaces: {sorted(slug_refs)}"
        )
    if slug_refs:
        ref = slug_refs[0]
        return WorkspaceMatch(ref, query, wt_for_ref.get(ref))

    raise LookupError(f"no workspace matched {query!r}")


def cmux_close_workspace_best_effort(short_or_ref: str) -> bool:
    """Close the workspace identified by name or ref.

    Returns True if the workspace no longer appears in `cmux list-workspaces`.
    """
    cmux("close-workspace", "--workspace", short_or_ref, check=False)
    after = cmux("list-workspaces", check=False)
    return short_or_ref not in after


def spawn_pr_workspace(
    pr: PR,
    wt: Worktree,
    *,
    self_user: str | None = None,
    pref: NudgePref | None = None,
    dry: bool = False,
) -> str | None:
    """Spawn the tracked cmux workspace for a PR; apply pills, log to stdout."""
    if dry:
        print(f"  [dry] spawn {wt.short}  #{pr.number}  cwd={wt.path}", flush=True)
        for key, value, _ in status_pills(pr, wt, self_user, pref):
            print(f"  [dry]   pill {key}={value}", flush=True)
        return None
    ref = spawn_workspace(wt.short, wt.path, claude_command(build_pr_prompt(pr)))
    if ref is None:
        print(
            f"  warn: could not resolve new workspace ref for {wt.short}",
            file=sys.stderr,
            flush=True,
        )
        return None
    apply_pills(ref, pr, wt, self_user, pref)
    print(
        f"  {verb('spawned')} {bold(wt.short)} ({ref})  #{pr.number}"
        f"  [{issue_color(pr.display_issue)(pr.display_issue)}]",
        flush=True,
    )
    return ref


def spawn_orphan_workspace(wt: Worktree, *, dry: bool = False) -> str | None:
    """Spawn an orphan-worktree workspace (no PR); apply orphan + WIP pills."""
    if dry:
        print(f"  [dry] orphan spawn {wt.short}  cwd={wt.path}", flush=True)
        return None
    ref = spawn_workspace(wt.short, wt.path, claude_command(build_orphan_prompt(wt)))
    if ref is None:
        print(
            f"  warn: could not resolve orphan workspace ref for {wt.short}",
            file=sys.stderr,
            flush=True,
        )
        return None
    _set_status(ref, ORPHAN_KEY, ORPHAN_ICON, ORANGE)
    apply_wip_pill(ref, wt.dirty_count)
    print(
        f"  {verb('spawned')} {bold(wt.short)} ({ref})  {dim(f'orphan branch={wt.branch}')}",
        flush=True,
    )
    return ref


def close_gone_cwd_workspaces(*, dry: bool = False) -> list[str]:
    """Close cmux workspaces whose cwd no longer exists on disk; returns refs closed.

    A worktree can be removed externally (manual `git worktree remove`, an
    autoclose pass that crashed before closing the workspace, sync tools)
    without taking its cmux workspace with it. The workspace becomes unusable
    because its processes have no cwd. Close it.
    """
    closed: list[str] = []
    names, cwds = workspace_state()
    for ref, cwd in cwds.items():
        if cwd.exists():
            continue
        ws_name = names.get(ref, ref)
        action = "[dry] autoclose" if dry else "autoclose"
        print(
            f"  {verb(action)} {dim(f'closing workspace {ws_name} ({ref}) — cwd missing: {cwd}')}",
            flush=True,
        )
        if not dry:
            cmux_close_workspace_best_effort(ref)
            closed.append(ref)
    return closed
