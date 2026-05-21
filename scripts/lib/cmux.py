"""cmux CLI wrapper, workspace queries, and cockpit pill management."""

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
from .colors import bold, issue_color, magenta
from .gh import PR
from .git import Worktree, worktrees
from .pills import decide_pills
from .prompts import build_orphan_prompt, build_pr_prompt, claude_command

GREEN = "#2dd36f"
RED = "#eb445a"
ORANGE = "#ff9500"
BLUE = "#3b82f6"
GREY = "#6b7280"

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

ACTIONABLE_KEYS = ("ci", "comments", "merge", "draft", "approved", "rebase", "wip")

# Verbs that need cmux specifically — limux fork lacks the persistent-pill API.
_PILL_VERBS = frozenset({"set-status", "clear-status"})


def _resolve_binary(verb: str) -> str | None:
    """Pick a workspace-CLI binary for `verb`. Pills require cmux; everything
    else accepts cmux or its limux fork.
    """
    if shutil.which("cmux"):
        return "cmux"
    if verb in _PILL_VERBS:
        return None
    if shutil.which("limux"):
        return "limux"
    return None


def require_workspace_binary() -> None:
    """Exit cleanly with a one-liner if neither cmux nor limux is on PATH.
    Use at the top of slash-command entry scripts so the user gets a useful
    message instead of a Python traceback.
    """
    if shutil.which("cmux") or shutil.which("limux"):
        return
    print(
        "cockpit: this command requires cmux or limux on PATH",
        file=sys.stderr,
    )
    sys.exit(2)


def cmux(*args: str, check: bool = True) -> str:
    verb = args[0] if args else ""
    binary = _resolve_binary(verb)
    if binary is None:
        if check:
            hint = (
                " (limux lacks pill support)"
                if verb in _PILL_VERBS and shutil.which("limux")
                else " or limux" if verb not in _PILL_VERBS else ""
            )
            raise FileNotFoundError(f"cockpit: '{verb}' requires cmux{hint} on PATH")
        return ""
    return run([binary, *args], check=check)


def apply_wip_pill(ref: str, dirty_count: int) -> None:
    """Set or clear the WIP pill on `ref` based on dirty-file count."""
    if dirty_count > 0:
        cmux(
            "set-status",
            WIP_KEY,
            f"{WIP_ICON} {dirty_count}",
            "--workspace",
            ref,
            "--color",
            ORANGE,
            check=False,
        )
    else:
        cmux("clear-status", WIP_KEY, "--workspace", ref, check=False)


def list_workspaces() -> list[str]:
    out = cmux("list-workspaces", check=False)
    refs: list[str] = []
    for line in out.splitlines():
        m = re.search(r"(workspace:\d+)", line)
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


def nudge_if_idle(
    ref: str,
    message: str,
    *,
    nudge_state: dict,
    interval_secs: int = 300,
    dry: bool = False,
    tag: str = "",
    pr_number: int | None = None,
    category: str | None = None,
) -> bool:
    """Send `message` + enter to workspace `ref` if it's idle and not parked.

    Two persistence regimes:
      - When `pr_number` is set, gate on `lib.nudges` — file-backed prefs
        survive daemon/cmux restarts and let the user mute via `cockpit nudge`.
      - Otherwise (orphan worktree, no PR), gate on the in-memory `nudge_state`
        dict only.

    Always also gates on cmux pills: skips if `idle=` is absent or `parked=`
    is present, so a transient runtime override still works.
    """
    if pr_number is not None and category is not None:
        from . import nudges

        if not nudges.should_nudge(pr_number, category, interval_secs=interval_secs):
            return False
    else:
        now = time.monotonic()
        if now - nudge_state.get(ref, 0.0) < interval_secs:
            return False
    status_lines = cmux("list-status", "--workspace", ref, check=False).splitlines()
    if not any(line.lstrip().startswith("idle=") for line in status_lines):
        return False
    if any(line.lstrip().startswith(f"{PARKED_KEY}=") for line in status_lines):
        return False
    if dry:
        print(f"  [dry] nudge {tag} → {ref}: {message[:70]}", flush=True)
        return False
    if pr_number is not None and category is not None:
        from . import nudges

        nudges.record_nudge(pr_number, category)
    else:
        nudge_state[ref] = time.monotonic()
    cmux("send", "--workspace", ref, message, check=False)
    cmux("send-key", "--workspace", ref, "enter", check=False)
    return True


def workspace_names() -> dict[str, str]:
    """{ref: name} from `cmux list-workspaces`."""
    out = cmux("list-workspaces", check=False)
    names: dict[str, str] = {}
    for line in out.splitlines():
        m = re.search(r"(workspace:\d+)\s+(\S+)", line)
        if m:
            names[m.group(1)] = m.group(2)
    return names


def workspace_cwds() -> dict[str, Path]:
    """{ref: current_directory} via `cmux rpc workspace.list`."""
    out = cmux("rpc", "workspace.list", "{}", check=False)
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {}
    cwds: dict[str, Path] = {}
    for ws in data.get("workspaces", []):
        ref = ws.get("ref")
        cwd = ws.get("current_directory")
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
    return any(line.lstrip().startswith("idle=") for line in out.splitlines())


def workspace_is_parked(ref: str) -> bool:
    """True if the user manually set the `parked=` pill (done-waiting marker)."""
    out = cmux("list-status", "--workspace", ref, check=False)
    return any(line.lstrip().startswith(f"{PARKED_KEY}=") for line in out.splitlines())


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


_CMUX_RENDERERS = {
    "rebase": lambda _p: ("rebase", "🔄 rebasing", ORANGE),
    "merge": lambda _p: ("merge", "🔀 merging", ORANGE),
    "wip": lambda p: ("wip", f"✏️ {p['count']} dirty", ORANGE),
    "ci_failed": lambda p: ("ci", f"❌ ci:{p['phase']}", RED),
    "ci_pending": lambda _p: ("ci", "⏳ ci pending", ORANGE),
    "unaddressed": lambda p: ("comments", f"💬 {p['count']} unaddressed", RED),
    "changes_requested": lambda _p: ("comments", "💬 changes requested", RED),
    "conflict": lambda _p: ("merge", "⚠️ conflict", ORANGE),
    "draft": lambda _p: ("draft", "📝 draft", GREY),
    "approved": lambda _p: ("approved", "✅ approved", GREEN),
    # `state` is footer-only; cmux drops it since autoclose removes workspaces
    # for non-OPEN PRs within a cycle.
    "state": lambda _p: None,
}


def status_pills(pr: PR, wt: Worktree | None = None) -> list[tuple[str, str, str]]:
    """(key, value, color) tuples for cmux set-status. Maps decide_pills output."""
    out: list[tuple[str, str, str]] = []
    for p in decide_pills(pr, wt):
        renderer = _CMUX_RENDERERS.get(p["kind"])
        if renderer is None:
            continue
        tup = renderer(p)
        if tup is not None:
            out.append(tup)
    return out


def apply_pills(
    ref: str, pr: PR, wt: Worktree | None = None
) -> frozenset[tuple[str, str, str]]:
    """Idempotently sync cmux pills; return the desired snapshot for diffing.

    cmux ordering rule: new pills prepend; re-setting an existing key keeps its
    slot. To force a deterministic order — and push cmux's own `claude_code`
    pill (e.g. "Needs input") to the bottom — clear all our keys first, then
    re-set in reverse display order. Also sets a default `idle=` pill if the
    workspace has no claude_code or loop pills (indicates no active agent).
    """
    desired = tuple(status_pills(pr, wt))
    keys_to_clear = [*ACTIONABLE_KEYS, COCKPIT_KEY]
    with ThreadPoolExecutor(max_workers=len(keys_to_clear)) as ex:
        for f in [
            ex.submit(cmux, "clear-status", k, "--workspace", ref, check=False)
            for k in keys_to_clear
        ]:
            f.result()
    for key, value, color in reversed(desired):
        cmux(
            "set-status", key, value, "--workspace", ref, "--color", color, check=False
        )

    current_status = cmux("list-status", "--workspace", ref, check=False)
    has_claude = any(
        line.lstrip().startswith(k + "=")
        for k in ("claude_code", "loop")
        for line in current_status.splitlines()
    )
    if not has_claude:
        cmux(
            "set-status",
            "idle",
            "☕ rest",
            "--workspace",
            ref,
            "--color",
            GREY,
            check=False,
        )

    return frozenset(desired)


@dataclass
class WorkspaceMatch:
    ref: str
    name: str
    worktree: Worktree | None


def _pr_num_to_branch(pr_num: str) -> str:
    from .cache import find_pr_payload_by_number
    from .config import discover_repo

    repo_cfg = discover_repo()
    repo_name = repo_cfg.get("name") if repo_cfg else None
    payload = find_pr_payload_by_number(pr_num, repo_name=repo_name)
    if payload is None:
        raise LookupError(f"PR #{pr_num} not in cockpit cache")
    branch = payload.get("branch")
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


def spawn_workspace(name: str, cwd: Path, command: str) -> str | None:
    """Spawn a new cmux workspace and return its ref, or None on failure.

    Works around `cmux new-workspace` not returning the ref on stdout: snapshots
    existing refs, spawns, then polls `list-workspaces` for a new one.
    """
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


def spawn_pr_workspace(pr: PR, wt: Worktree, *, dry: bool = False) -> str | None:
    """Spawn the tracked cmux workspace for a PR; apply pills, log to stdout."""
    if dry:
        print(f"  [dry] spawn {wt.short}  #{pr.number}  cwd={wt.path}", flush=True)
        for key, value, _ in status_pills(pr, wt):
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
    apply_pills(ref, pr, wt)
    print(
        f"  {magenta('spawned')} {bold(wt.short)} ({ref})  #{pr.number}"
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
    cmux(
        "set-status",
        ORPHAN_KEY,
        ORPHAN_ICON,
        "--workspace",
        ref,
        "--color",
        ORANGE,
        check=False,
    )
    apply_wip_pill(ref, wt.dirty_count)
    print(
        f"  {magenta('ORPHAN:')} spawned {bold(wt.short)} ({ref})  branch={wt.branch}",
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
        action = "[dry] autoclose" if dry else "autoclose:"
        print(
            f"  {magenta(action)} closing workspace {ws_name} ({ref}) "
            f"— cwd missing: {cwd}",
            flush=True,
        )
        if not dry:
            cmux_close_workspace_best_effort(ref)
            closed.append(ref)
    return closed
