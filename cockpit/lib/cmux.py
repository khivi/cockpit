"""cmux CLI wrapper, workspace queries, and cockpit pill management.

Backend *policy* (which of cmux/limux is in effect) lives in
`cockpit.lib.tool`; this module owns the *implementation* — the `cmux()` CLI
wrapper, ref parsing, pill management, and the per-backend actions
(`workspace_cwds`, `spawn_workspace`) that branch on `tool.is_limux()`.
Callers needing the policy predicates import `resolve_tool` / `is_cmux` /
`is_limux` from `cockpit.lib.tool`; everything else comes from here.
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

from . import run, tool
from .cache import find_pr_payload_by_number
from .colors import CMUX_COLOR_ANSI, bold, dim
from .config import discover_repo
from .gh import PR
from .git import Worktree, worktrees
from .issue_color import issue_color
from .log_format import verb
from .nudges import NudgePref
from .pills import decide_pills
from .prompts import build_orphan_prompt, build_pr_prompt, claude_command

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

# Stale-running escape hatch: set when an actionable PR issue has persisted past
# the stale threshold without the workspace ever becoming nudgeable (e.g. the
# agent is wedged mid-turn, or its `idle=` pill write was lost and never
# re-asserted). A passive, out-of-session visual marker — never a `send`, so it
# can't type into a pending y/n permission prompt. Managed in the cycle's slow
# tick, not via apply_pills, so it is deliberately absent from ACTIONABLE_KEYS.
STUCK_KEY = "stuck"
STUCK_ICON = "🚨"

# Linear "dev done" marker: set when a tracked PR's linked Linear ticket sits in
# the configured dev-done workflow state (see config.linear_dev_done_state). Like
# `stuck=`, it is a passive sidebar visual managed directly in the slow tick (not
# via apply_pills) and so is deliberately absent from ACTIONABLE_KEYS — it is
# never a `send`. Gated on the repo being Linear-configured AND the branch
# carrying a ticket id (the same branch→ticket alignment the footer renders).
DEVDONE_KEY = "devdone"
DEVDONE_ICON = "🏁"

MUTED_KEY = "muted"
MUTED_ICON = "🔇"

KEEP_KEY = "keep"
KEEP_ICON = "📌"

ACTIONABLE_KEYS = (
    "ci",
    "comments",
    "merge",
    "draft",
    "approved",
    "rebase",
    "wip",
    MUTED_KEY,
    KEEP_KEY,
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


def _native_claude_state(lines: list[str]) -> str | None:
    """cmux's own `claude_code=` agent state from a `list-status` dump, or None.

    cmux's Claude wrapper drives three values (verified against the live event
    stream): `Running` (mid-turn), `Idle` (Stop fired, parked at the prompt),
    and `Needs input`. `Needs input` is AMBIGUOUS — it fires both for an
    idle-at-prompt session aged past Claude's ~60s Notification *and* for a
    pending y/n permission request mid-turn (which never fires Stop). So it is
    not a safe at-rest signal on its own; only `Idle` is unambiguous. A line
    looks like `claude_code=Needs input icon=bell.fill color=#4C8DFF`.
    """
    for line in lines:
        s = line.strip()
        if not s.startswith("claude_code="):
            continue
        rest = s[len("claude_code=") :]
        for sep in (" icon=", " color="):
            idx = rest.find(sep)
            if idx != -1:
                rest = rest[:idx]
        return rest.strip() or None
    return None


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


def apply_stuck_pill(ref: str, label: str | None) -> None:
    """Set the stale-running escape-hatch pill on `ref` to `label`, or clear it
    when `label` is falsy. See `STUCK_KEY` for the design rationale.
    """
    if label:
        _set_status(ref, STUCK_KEY, f"{STUCK_ICON} {label}", RED)
    else:
        _clear_status(ref, STUCK_KEY)


def apply_devdone_pill(ref: str, ticket: str | None) -> None:
    """Set the Linear "dev done" pill on `ref` to `ticket`, or clear it when
    `ticket` is falsy. See `DEVDONE_KEY` for the design rationale. Green because
    "development complete" is a positive milestone, not an action item.
    """
    if ticket:
        _set_status(ref, DEVDONE_KEY, f"{DEVDONE_ICON} dev-done {ticket}", GREEN)
    else:
        _clear_status(ref, DEVDONE_KEY)


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


def rename_workspace_if_needed(
    ref: str, expected_name: str, current_name: str, *, dry: bool = False
) -> bool:
    """Re-assert workspace `ref`'s name to `expected_name` (its worktree's
    branch-derived `label`) when the live cmux name has drifted.

    cockpit names a workspace `wt.label` at spawn, but the name can diverge —
    the user renames it by hand, a closed-then-reopened PR reuses the branch, or
    a limux spawn lands a uuid name. cockpit resolves workspaces by cwd→path,
    never by name, so drift is otherwise silently tolerated; this keeps the
    sidebar label tracking the branch. `rename-workspace` is not a pill verb, so
    it works on both cmux and limux.

    No-op (returns False) when `expected_name` is empty or already current.
    Returns True iff a rename was issued (or, under `dry`, would have been).
    """
    if not expected_name or current_name == expected_name:
        return False
    if not dry:
        cmux("rename-workspace", "--workspace", ref, expected_name, check=False)
    return True


def reconcile_workspace_names(
    names: dict[str, str],
    cwds: dict[str, Path],
    wts: list[Worktree],
    *,
    dry: bool = False,
) -> list[tuple[str, str, str]]:
    """Rename every workspace whose cmux name has drifted from its worktree's
    branch-derived `label`. Used by the fast tick to recover divergence within
    ~30s.

    Resolution is cwd→path only, mirroring `find_cockpit_workspaces`'s primary
    match: a workspace is bound to a worktree by its current directory, and its
    expected name is that worktree's `label`. A workspace that would only match
    by name already equals `label`, so it never needs a rename and is skipped.

    The **primary checkout** (`wt.is_primary` — the canonical clone, on a main
    branch) is exempt: a workspace parked there to run skills (morning-align,
    etc.) is a deliberate utility the user names by hand, and the documented
    escape hatch ("rename the branch") can't apply — the primary is on a main
    branch the user won't rename. The slow-tick rename paths already skip it (no
    PR → never `tracked`; `master` ∈ `MAIN_BRANCHES`); this keeps the fast tick
    from clobbering it back to the branch label.

    Returns `[(ref, old_name, new_name)]` for the renames issued (or, under
    `dry`, that would be issued).
    """
    wt_by_path = {wt.path.resolve(): wt for wt in wts}
    renamed: list[tuple[str, str, str]] = []
    for ref, cwd in cwds.items():
        wt = wt_by_path.get(cwd.resolve())
        if wt is None or wt.is_primary:
            continue
        current = names.get(ref, "")
        if rename_workspace_if_needed(ref, wt.label, current, dry=dry):
            renamed.append((ref, current, wt.label))
    return renamed


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

    Gates on two independent at-rest signals so a dropped Stop-hook write can't
    silently suppress nudges forever:

    - cmux's native `claude_code=Running` always blocks — an active turn is
      never safe, and this also catches a dropped `idle=` clear (a stale pill
      left on a now-running session).
    - Otherwise the workspace is "at rest and safe" iff the persistent `idle=`
      pill is present OR cmux reports the unambiguous native `Idle` state. The
      `idle=` pill is set only at Stop (permission prompts are mid-turn and
      never fire Stop), so it never coincides with a pending y/n. Native
      `Needs input` is deliberately NOT trusted: it is the same value cmux
      shows for a pending permission request, and nudging there would type into
      the confirmation.
    - When native `Idle` holds but the `idle=` pill is missing, re-assert it —
      self-healing a Stop-hook write that the daemon never landed.

    Still skips when `parked=` is present (user's done-waiting marker).

    There is no time-based throttle. The slow tick's cadence
    (`slow_poll_interval_seconds`, default 300s) is the implicit rate limit
    — each tick re-evaluates and re-fires if the underlying issue persists.
    """
    if pr_number is not None and category is not None:
        from . import nudges

        if not nudges.should_nudge(pr_number):
            return False
    status_lines = cmux("list-status", "--workspace", ref, check=False).splitlines()
    native = _native_claude_state(status_lines)
    if native == "Running":
        return False
    has_idle_pill = _has_pill(status_lines, "idle")
    if not (has_idle_pill or native == "Idle"):
        return False
    if _has_pill(status_lines, PARKED_KEY):
        return False
    if native == "Idle" and not has_idle_pill and not dry:
        _set_status(ref, "idle", "idle", GREY)
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
    wt_by_name = {wt.label: wt for wt in wts}
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
    "muted": lambda _p: (MUTED_KEY, f"{MUTED_ICON} muted", YELLOW),
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
    "keep": lambda _p: (KEEP_KEY, f"{KEEP_ICON} keep", BLUE),
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
    *,
    keep: bool = False,
) -> list[tuple[str, str, str]]:
    """(key, value, color) tuples for cmux set-status. Maps decide_pills output.

    When `self_user` is given and `pr.author` differs, prepends an `owner` pill
    so coworker-owned PRs are visible in the sidebar. Prepended so reversed
    set-order in `apply_pills` places it at the bottom of the visual stack.

    `pref` carries the daemon-resolved mute state; pure consumer — does not
    load it. See cycle.py for the single-authority pref load.

    `keep` marks user-spawned worktrees protected from auto-reap on merge.
    """
    out: list[tuple[str, str, str]] = []
    if self_user and pr.author and pr.author != self_user:
        out.append((OWNER_KEY, f"{OWNER_ICON} @{pr.author}", BLUE))
    for p in decide_pills(pr, wt, pref, keep=keep):
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
    *,
    keep: bool = False,
) -> frozenset[tuple[str, str, str]]:
    """Idempotently sync cmux pills; return the desired snapshot for diffing.

    cmux ordering rule: new pills prepend; re-setting an existing key keeps its
    slot. To force a deterministic order — and push cmux's own `claude_code`
    pill (e.g. "Needs input") to the bottom — clear all our keys first, then
    re-set in reverse display order. The `idle=` pill is owned by
    `hooks/cmux-idle-pill.sh` (Stop / UserPromptSubmit) — not touched here.
    """
    desired = tuple(status_pills(pr, wt, self_user, pref, keep=keep))
    _clear_pr_pill_keys(ref)
    for key, value, color in reversed(desired):
        _set_status(ref, key, value, color)

    return frozenset(desired)


# "cockpit_managed" is a one-release back-compat strip — remove next release.
_PR_PILL_CLEAR_KEYS = [*ACTIONABLE_KEYS, COCKPIT_KEY, OWNER_KEY, "cockpit_managed"]


def _clear_pr_pill_keys(ref: str) -> None:
    """Clear every PR-derived pill key from workspace `ref` in parallel."""
    with ThreadPoolExecutor(max_workers=len(_PR_PILL_CLEAR_KEYS)) as ex:
        for f in [ex.submit(_clear_status, ref, k) for k in _PR_PILL_CLEAR_KEYS]:
            f.result()


def clear_pr_pills(ref: str) -> None:
    """Remove all PR pills from workspace `ref`, leaving no PR marker on the card.

    Used when a merged/closed PR's branch has been reused for new local work
    (`cycle._is_reused_branch_merge`): the stale merged pill is cleared so the
    card shows no PR until a new one is opened. Same key set `apply_pills`
    clears, with nothing re-set.
    """
    _clear_pr_pill_keys(ref)


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


def select_workspace(ref: str, *, check: bool = False) -> str:
    """Switch the active cmux workspace to `ref`.

    The verb is `select-workspace` (a stable legacy alias for `workspace
    select`), NOT `focus` — `cmux focus` is not a command and exits nonzero,
    which `check=False` would silently swallow. Centralised here so the TUI's
    `f`/Enter/double-click focus actions all use the one correct verb.
    """
    return cmux("select-workspace", "--workspace", ref, check=check)


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
    keep: bool = False,
    dry: bool = False,
) -> str | None:
    """Spawn the tracked cmux workspace for a PR; apply pills, log to stdout."""
    if dry:
        print(f"  [dry] spawn {wt.short}  #{pr.number}  cwd={wt.path}", flush=True)
        for key, value, _ in status_pills(pr, wt, self_user, pref, keep=keep):
            print(f"  [dry]   pill {key}={value}", flush=True)
        return None
    ref = spawn_workspace(wt.label, wt.path, claude_command(build_pr_prompt(pr)))
    if ref is None:
        print(
            f"  warn: could not resolve new workspace ref for {wt.short}",
            file=sys.stderr,
            flush=True,
        )
        return None
    apply_pills(ref, pr, wt, self_user, pref, keep=keep)
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
    ref = spawn_workspace(wt.label, wt.path, claude_command(build_orphan_prompt(wt)))
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
