"""cmux CLI wrapper, workspace queries, and cockpit pill management."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import run
from .gh import PR
from .git import Worktree

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


def cmux(*args: str, check: bool = True) -> str:
    return run(["cmux", *args], check=check)


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
) -> bool:
    """Send `message` + enter to workspace `ref` if it's idle and not parked.

    Idempotent: skips if last nudge for this ref was within `interval_secs`,
    or if there's no `idle=` pill, or if `parked=` is set. Updates `nudge_state`
    in place. Returns True if a nudge was sent.
    """
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
    nudge_state[ref] = now
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


def status_pills(pr: PR, wt: Worktree | None = None) -> list[tuple[str, str, str]]:
    """(key, value, color) tuples for cmux set-status. Emoji-in-value only."""
    pills: list[tuple[str, str, str]] = []
    if wt is not None and wt.rebasing:
        pills.append(("rebase", "🔄 rebasing", ORANGE))
    if wt is not None and wt.merging:
        pills.append(("merge", "🔀 merging", ORANGE))
    if wt is not None and wt.dirty_count > 0:
        pills.append(("wip", f"✏️ {wt.dirty_count} dirty", ORANGE))
    if pr.ci.startswith("failed"):
        pills.append(("ci", f"❌ ci:{pr.ci.split(':')[1]}", RED))
    elif pr.ci == "pending":
        pills.append(("ci", "⏳ ci pending", ORANGE))
    if pr.unaddressed > 0:
        pills.append(("comments", f"💬 {pr.unaddressed} unaddressed", RED))
    elif pr.review_decision == "CHANGES_REQUESTED":
        pills.append(("comments", "💬 changes requested", RED))
    if pr.mergeable == "CONFLICTING":
        pills.append(("merge", "⚠️ conflict", ORANGE))
    if pr.is_draft:
        pills.append(("draft", "📝 draft", GREY))
    if pr.review_decision == "APPROVED":
        pills.append(("approved", "✅ approved", GREEN))
    return pills


def apply_pills(
    ref: str, pr: PR, wt: Worktree | None = None
) -> frozenset[tuple[str, str, str]]:
    """Idempotently sync cmux pills; return the desired snapshot for diffing.

    cmux ordering rule: new pills prepend; re-setting an existing key keeps its
    slot. To force a deterministic order — and push cmux's own `claude_code`
    pill (e.g. "Needs input") to the bottom — clear all our keys first, then
    re-set in reverse display order.
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
    return frozenset(desired)


def cmux_close_workspace_best_effort(short_or_ref: str) -> bool:
    """Close every surface in the workspace identified by name or ref.

    cmux has no single-workspace destroy verb. Returns True if the workspace
    no longer appears in `cmux list-workspaces`.
    """
    surfaces = cmux("list-pane-surfaces", "--workspace", short_or_ref, check=False)
    refs = [
        line.strip().split()[0]
        for line in surfaces.splitlines()
        if line.strip().startswith("surface:")
    ]
    for ref in refs:
        cmux("close-surface", "--surface", ref, check=False)
    after = cmux("list-workspaces", check=False)
    return short_or_ref not in after
