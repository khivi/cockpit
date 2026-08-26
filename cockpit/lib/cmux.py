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
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from . import run, tool
from .colors import CMUX_COLOR_ANSI, bold, dim
from .constants import MAIN_BRANCHES
from .gh import PR
from .git import Worktree
from .issue_color import issue_color
from .log_format import verb
from .nudges import NudgePref
from .pills import ci_glyph, decide_pills
from .prompts import (
    build_orphan_prompt,
    build_pr_prompt,
    claude_command,
    split_prompt_prefix,
)

GREEN = "#16a34a"
RED = "#eb445a"
ORANGE = "#ff9500"
BLUE = "#3b82f6"
GREY = "#6b7280"
YELLOW = "#facc15"
PURPLE = "#8957e5"

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

# Linear "dev done" marker: set when a tracked PR's linked Linear ticket sits in
# the configured dev-done workflow state (see config.linear_dev_done). It
# is a passive sidebar visual managed directly in the slow tick (not via
# apply_pills) and so is deliberately absent from ACTIONABLE_KEYS — it is never a
# `send`. Gated on the repo being Linear-configured AND the branch carrying a
# ticket id (the same branch→ticket alignment the footer renders).
DEVDONE_KEY = "devdone"
DEVDONE_ICON = "🏁"

MUTED_KEY = "muted"
MUTED_ICON = "🔇"

# The PR-identity pill (`🟢 PR #332 open ✓`), replacing cmux's native sidebar PR
# row — see the `pr` paragraph in pills.py for why that row can't be trusted or
# configured. Passive like `devdone`, so it stays out of ACTIONABLE_KEYS, but it
# IS written by apply_pills and so must be cleared with the rest.
PR_KEY = "pr"

# GitHub's own colour language, so the pill reads the same as the PR page:
# grey draft, green open, purple merged, red closed.
PR_STATUS_STYLE = {
    "draft": ("⚪", GREY),
    "open": ("🟢", GREEN),
    "merged": ("🟣", PURPLE),
    "closed": ("🔴", RED),
}

# CI overriding the status colour above, for the non-passing states only.
# `failed` is prefix-matched (it carries a `:phase` suffix), so it isn't here.
_CI_PILL_COLOR = {"pending": ORANGE, "unknown": RED}

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

# Verbs that need cmux specifically — the limux fork lacks the persistent-pill,
# workspace-action (set-color), and workspace-group APIs. Gated here so they
# no-op on limux instead of erroring; sidebar tint and stack grouping are both
# additive cmux-only niceties.
_CMUX_ONLY_VERBS = frozenset(
    {"set-status", "clear-status", "workspace-action", "workspace-group"}
)


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
    gated in `_CMUX_ONLY_VERBS`) and never raises, so a missed tint can't stall a
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


# SF Symbol on a stacked-PR group's sidebar header. Not a pill — cmux renders
# it on the group row itself, which is the one place "these belong together"
# needs saying.
STACK_GROUP_ICON = "square.stack"

# SF Symbol on the coworker-review fold's header — someone else's PR I'm reading,
# not mine to ship.
REVIEW_GROUP_ICON = "eyeglasses"

# SF Symbol on the snoozed fold's header — read and handed back, waiting on
# someone else's comment or review (TUI `z`, `nudges.NudgePref.snoozed`).
SNOOZE_GROUP_ICON = "moon.zzz"

# Typed into a freshly spawned fold anchor purely to give it a terminal — see
# `create_workspace_group`. It is the only thing a user who clicks the header
# ever sees, so it says what the row is instead of leaving a bare prompt.
ANCHOR_KEEPALIVE_COMMAND = "echo 'cockpit fold anchor — safe to ignore'"


@dataclass(frozen=True)
class WorkspaceGroup:
    """A cmux sidebar group: a collapsible fold over member workspaces.

    `ref` is a `workspace_group:N` handle (window-scoped, like `workspace:N`).
    `anchor` is the member whose sidebar row *is* the group header — so cockpit
    keeps the group on a dedicated workspace of its own rather than on a stack
    member, leaving every member visible as its own row below (see
    `create_workspace_group` and `_durable_anchor`).
    """

    ref: str
    name: str
    anchor: str
    members: tuple[str, ...]
    icon: str = ""


def _group_from_json(blob: dict) -> WorkspaceGroup | None:
    ref = blob.get("ref")
    if not ref:
        return None
    return WorkspaceGroup(
        ref=ref,
        name=blob.get("name") or "",
        anchor=blob.get("anchor_workspace_ref") or "",
        members=tuple(blob.get("member_workspace_refs") or ()),
        icon=blob.get("icon_symbol") or "",
    )


def list_workspace_groups() -> list[WorkspaceGroup]:
    """Every sidebar group in the window. Empty on any failure (cmux absent,
    limux, malformed JSON) — grouping is additive, so a failed read just means
    "reconcile nothing this cycle", never an exception into the tick.
    """
    out = cmux("workspace-group", "list", "--json", check=False)
    try:
        blobs = json.loads(out or "{}").get("groups") or []
    except (json.JSONDecodeError, AttributeError):
        return []
    return [g for g in (_group_from_json(b) for b in blobs) if g is not None]


def create_workspace_group(
    name: str,
    refs: list[str],
    *,
    icon: str = STACK_GROUP_ICON,
    collapsed: bool = False,
) -> WorkspaceGroup | None:
    """Fold `refs` into one sidebar group named `name`, headed by its own row.

    `cmux workspace-group create` always spawns a *fresh* workspace to own the
    group, and the anchor's sidebar row **is** the group header. Anchoring the
    group on a stack member would therefore swallow that member's row — a
    four-PR stack rendering three rows under a header that says four. So the
    spawned anchor is kept as a dedicated header instead: the fold reads
    `<tip> (N)` with all N members listed below it.

    The anchor is spawned in `$HOME`, outside every registered repo, so
    `_reap_workspace_orphans` (which only owns workspaces whose cwd sits under
    a repo) never reaps it out from under the group. It is cockpit's to close
    when the stack dissolves — see `_reconcile_sidebar_groups`.

    The anchor cmux spawns is then **swapped for one that owns a live shell**
    (`_durable_anchor`); the workspace `create` makes has no command and so no
    terminal, and does not survive. That swap is what stops the fold churning.

    **One member is a valid group.** cmux drops a group only when the *anchor*
    is its sole workspace, and the dedicated anchor means a one-member fold
    still holds two — so a single coworker review folds under its own
    `<org> reviews (1)` header rather than sitting loose in the sidebar. (This
    guard used to be `< 2`, from before the anchor was split out of the
    members.) Callers that need a real minimum enforce their own: a one-PR
    chain isn't a stack, so `_reconcile_sidebar_groups` still skips it.

    `collapsed` folds the new group shut. cmux always creates a group *expanded*
    (`is_collapsed: false`), so a pile that is by definition not-my-turn pops
    open on the very tick that builds it — see `_reconcile_review_groups`, the
    one caller that asks for this. It is create-time only, never re-asserted: a
    per-cycle collapse would slam shut a fold the user had just expanded to read.

    Returns the created group, or None if anything failed.
    """
    if not refs:
        return None
    # cmux prepends each `--from` entry, so pass them reversed to land the
    # caller's first ref (a stack's tip) at the top of the fold.
    created = cmux(
        "workspace-group",
        "create",
        "--name",
        name,
        "--cwd",
        str(Path.home()),
        "--from",
        ",".join(reversed(refs)),
        "--json",
        check=False,
    )
    try:
        group = _group_from_json(json.loads(created or "{}").get("group") or {})
    except (json.JSONDecodeError, AttributeError):
        return None
    if group is None:
        return None
    _set_group_icon(group.ref, icon)
    if collapsed:
        cmux("workspace-group", "collapse", group.ref, check=False)
    return _durable_anchor(group, name)


def _durable_anchor(group: WorkspaceGroup, name: str) -> WorkspaceGroup:
    """Re-anchor `group` onto a workspace that owns a live shell.

    `workspace-group create` spawns its anchor with **no command**, and cmux
    gives a command-less workspace no terminal surface at all (`read-screen`
    answers `Failed to read terminal text`, and no shell process holds the cwd).
    Such a workspace does not survive: both trailing folds' anchors were
    observed dying 287ms apart as `surface.closed` → `workspace.closed`, which
    silently takes the fold with them — silently because cockpit never ran a
    dissolve, so no `ungrouped` line is printed and nothing here notices. The
    next slow cycle then rebuilds the whole fold, which is the sidebar churn:
    a new group ref, a new anchor, and the pile reshuffled, every few minutes.

    Cockpit's *other* workspaces do not die because `spawn_workspace` always
    passes a command, so the fix is to spawn the anchor the same way and
    `set-anchor` onto it. `create` gives no way to seed its own anchor with a
    command, hence the swap rather than a flag.

    Fails **open**: if the spawn or the re-anchor fails, the throwaway anchor
    cmux made is left in place and the caller gets exactly the previous
    behaviour — a fold that may churn is strictly better than no fold.

    The anchor keeps `$HOME` as its cwd (see `create_workspace_group`), so it
    still sits outside every registered repo and stays invisible to
    `_reap_workspace_orphans`, `close_gone_cwd_workspaces` and
    `_dedupe_workspaces`.
    """
    anchor = spawn_workspace(name, Path.home(), ANCHOR_KEEPALIVE_COMMAND)
    if anchor is None:
        return group
    cmux(
        "workspace-group",
        "add",
        "--group",
        group.ref,
        "--workspace",
        anchor,
        check=False,
    )
    cmux(
        "workspace-group",
        "set-anchor",
        "--group",
        group.ref,
        "--workspace",
        anchor,
        check=False,
    )
    # Routed through the shared funnel, never a raw close: it records the UUID
    # via `_note_self_close`, without which the `workspace.closed` event reads
    # as the user clicking cmux's ✕ and gets routed into teardown.
    cmux_close_workspace_best_effort(group.anchor)
    return replace(group, anchor=anchor)


def _set_group_icon(group_ref: str, icon: str) -> None:
    cmux("workspace-group", "set-icon", group_ref, "--symbol", icon, check=False)


def add_to_workspace_group(group_ref: str, ref: str) -> None:
    cmux(
        "workspace-group", "add", "--group", group_ref, "--workspace", ref, check=False
    )


def remove_from_workspace_group(ref: str) -> None:
    cmux("workspace-group", "remove", "--workspace", ref, check=False)


def rename_workspace_group(group_ref: str, name: str) -> None:
    cmux("workspace-group", "rename", group_ref, "--name", name, check=False)


def move_workspace_group_to_end(group_ref: str) -> None:
    """Park the group at the bottom of the sidebar.

    `--to-index` clamps, so a number past the end is "last" without having to
    read the sidebar's current length (`workspace-group list` doesn't report an
    index). Best-effort like every other group verb.
    """
    cmux("workspace-group", "move", group_ref, "--to-index", "9999", check=False)


def move_workspace_group_to_start(group_ref: str) -> None:
    """Park the group at the top of the sidebar — the inverse of the above.

    Only ever called to undo a sink: a stack group sunk while its tip was
    snoozed would otherwise stay at the bottom forever once the snooze woke,
    burying exactly the chain that just asked for attention. Cockpit has nowhere
    to record where the group *used* to sit (inventory is derived, never
    stored), so the lift is a fixed top rather than a restore — which is why the
    caller fires it on that transition only, never as a per-cycle re-assert
    (unlike the sink, which re-asserts so it self-heals). Best-effort like every
    other group verb.
    """
    cmux("workspace-group", "move", group_ref, "--to-index", "0", check=False)


def ungroup_workspaces(group_ref: str) -> None:
    """Dissolve the group, keeping every member workspace open."""
    cmux("workspace-group", "ungroup", group_ref, check=False)


def _resolve_binary(verb: str) -> str | None:
    """Pick a workspace-CLI binary for `verb`. Pills require cmux; everything
    else accepts cmux or its limux fork. Honours cfg['tool'].
    """
    backend = tool.resolve_tool()
    if backend == "none":
        return None
    if verb in _CMUX_ONLY_VERBS and backend != "cmux":
        return None  # limux has no pills / workspace-action / workspace-group
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
        else (
            f"cockpit: '{backend}' not found on PATH — install cmux "
            "(https://github.com/manaflow-ai/cmux) or limux "
            "(https://github.com/am-will/limux)"
        )
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
                " (requires cmux; current tool is limux)"
                if verb in _CMUX_ONLY_VERBS and backend == "limux"
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


def apply_devdone_pill(ref: str, ticket: str | None) -> None:
    """Set the Linear "dev done" pill on `ref` to `ticket`, or clear it when
    `ticket` is falsy. See `DEVDONE_KEY` for the design rationale. Green because
    "development complete" is a positive milestone, not an action item. The 🏁
    icon + green already convey "dev-done", so the label is just the caller's
    `ticket` string (the id, or a Trello card title) — no literal "dev-done"
    word, leaving more room for the label.
    """
    if ticket:
        _set_status(ref, DEVDONE_KEY, f"{DEVDONE_ICON} {ticket}", GREEN)
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


# How long to wait for a freshly-spawned claude to register a `claude_code=`
# status before delivering the follow-up submission. The prefix's first turn is
# an LLM turn, so claude reports state within a few seconds; the cap is a
# backstop so a never-booting session doesn't hang the caller indefinitely.
_FOLLOWUP_READY_TIMEOUT_SECONDS = 20.0
_FOLLOWUP_POLL_INTERVAL_SECONDS = 0.5


def _claude_ready(ref: str) -> bool:
    """True once the workspace's claude has registered any `claude_code=` state
    — i.e. its TUI is up, so typed input queues instead of being dropped into a
    not-yet-rendered terminal.
    """
    lines = cmux("list-status", "--workspace", ref, check=False).splitlines()
    return _native_claude_state(lines) is not None


def deliver_followup(ref: str, text: str) -> bool:
    """Deliver `text` as a SEPARATE submission into an already-spawned
    workspace's claude — the second half of the two-send `prompt_prefix` flow
    (the prefix slash command rides in as the initial `--command`, the task body
    follows here).

    Waits (bounded) for claude to boot so the keystrokes aren't lost into a
    not-yet-rendered TUI, then types the text and submits with Enter — the same
    primitive the attach path and `nudge_if_idle` use. Best-effort: a send
    failure is logged, never raised.
    """
    deadline = time.monotonic() + _FOLLOWUP_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _claude_ready(ref):
            break
        time.sleep(_FOLLOWUP_POLL_INTERVAL_SECONDS)
    try:
        cmux("send", "--workspace", ref, text, check=True)
        cmux("send-key", "--workspace", ref, "enter", check=True)
    except (RuntimeError, FileNotFoundError) as e:
        print(
            f"  warn: {tool.resolve_tool()} followup send failed for {ref}: {e}",
            flush=True,
        )
        return False
    return True


def rename_workspace_if_needed(
    ref: str, expected_name: str, current_name: str, *, dry: bool = False
) -> bool:
    """Re-assert workspace `ref`'s name to `expected_name` (its worktree's
    `workspace_name`, `[<repo>] <branch>`) when the live cmux name has drifted.

    cockpit names a workspace `wt.workspace_name` at spawn, but the name can
    diverge — the user renames it by hand, a closed-then-reopened PR reuses the
    branch, or a limux spawn lands a uuid name. cockpit resolves workspaces by
    cwd→path, never by name, so drift is otherwise silently tolerated; this keeps
    the workspace name tracking repo + branch. `rename-workspace` is not a pill
    verb, so it works on both cmux and limux.

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
    `workspace_name` (the `[<repo>] <label>` branch-derived name). Used by the
    fast tick to recover divergence within ~30s.

    Resolution is cwd→path only, mirroring `find_cockpit_workspaces`'s primary
    match: a workspace is bound to a worktree by its current directory, and its
    expected name is that worktree's `workspace_name`. A workspace that would
    only match by name already equals it, so it never needs a rename and is
    skipped.

    Any worktree on a **main branch** (`wt.is_primary` or `wt.branch in
    MAIN_BRANCHES`) is exempt: its `label` derivation collapses to the branch
    name (`main`/`master`), so a forced rename would either clobber a sibling
    already named that or revert a deliberate user-supplied name with no escape
    hatch ("rename the branch" can't apply to a trunk the user won't rename). In
    a **bare repo** no sibling worktree is ever `is_primary` (there's no
    canonical checkout), so the branch check is what protects a feature worktree
    temporarily parked on `main`. The slow-tick rename paths already skip these
    (no PR → never `tracked`; `branch ∈ MAIN_BRANCHES`); this keeps the fast tick
    from clobbering them back to the branch label.

    Returns `[(ref, old_name, new_name)]` for the renames issued (or, under
    `dry`, that would be issued).
    """
    wt_by_path = {wt.path.resolve(): wt for wt in wts}
    renamed: list[tuple[str, str, str]] = []
    for ref, cwd in cwds.items():
        wt = wt_by_path.get(cwd.resolve())
        if wt is None or wt.is_primary or wt.branch in MAIN_BRANCHES:
            continue
        current = names.get(ref, "")
        if rename_workspace_if_needed(ref, wt.workspace_name, current, dry=dry):
            renamed.append((ref, current, wt.workspace_name))
    return renamed


def _idle_skip_reason(status_lines: list[str]) -> str | None:
    """Why `status_lines` is unsafe to `send` into — `None` when it is safe.

    The gate itself, factored out so a caller can *report* the verdict without
    re-deriving it from a second `list-status` (which would be both a wasted
    round-trip and a second copy of the rule that must never drift from this
    one). Guard order matches `nudge_if_idle`'s: `Running` outranks the at-rest
    check, which outranks `parked=`, so a mid-turn parked workspace reports
    "mid-turn". The strings are user-facing in `cockpit broadcast`'s summary.
    """
    native = _native_claude_state(status_lines)
    if native == "Running":
        return "mid-turn"
    if not (_has_pill(status_lines, "idle") or native == "Idle"):
        # `Needs input` is the ambiguous one — a pending y/n permission looks
        # exactly like a session parked at the composer, so both land here.
        return f"not at rest ({native or 'no Claude session'})"
    if _has_pill(status_lines, PARKED_KEY):
        return "parked"
    return None


def one_line(text: str) -> str:
    r"""Collapse `text` to a single line so `cmux send` delivers it as one prompt.

    `cmux send` synthesizes keypresses; it does NOT do a bracketed paste. Its
    own help says "Escape sequences: \n and \r send Enter, \t sends Tab", and
    probing cmux 0.64.22 confirms both spellings of a newline — the literal
    two-character `\n` AND a real 0x0A byte in the argv — arrive as **Enter**.

    In a Claude Code composer Enter means submit, so an un-normalized
    multi-line message does not deliver one prompt with newlines in it: it
    submits the first fragment as its own truncated prompt and the remainder as
    a second. `cockpit broadcast 'fix the \n handling'` hit exactly that.

    There is no escape that survives — `\\` arrives as two literal backslashes
    and the Enter still fires — so collapsing is the only faithful delivery.
    Applied inside `nudge_if_idle` rather than at each call site, for the same
    single-funnel reason `_note_self_close` lives inside
    `cmux_close_workspace_best_effort`: a new send path is covered for free.
    """
    for esc in ("\\n", "\\r", "\\t"):
        text = text.replace(esc, " ")
    # Bare `.split()` also folds real newlines, tabs and runs of spaces.
    return " ".join(text.split())


def rest_skip_reason(ref: str) -> str | None:
    """Why a `send` into `ref` would be refused right now — None when it'd land.

    One `list-status`, then the gate's own `_idle_skip_reason`. The point is
    that a display caller (the TUI's `a` modal, warning you before you type
    into a session that will refuse the message) gets the verdict from the
    *same* function `nudge_if_idle` gates on, so the warning and the decision
    can never disagree. Advisory only: the gate re-checks at send time and
    stays the authority, because a turn can end while you type.
    """
    return _idle_skip_reason(
        cmux("list-status", "--workspace", ref, check=False).splitlines()
    )


def nudge_if_idle(
    ref: str,
    message: str,
    *,
    dry: bool = False,
    tag: str = "",
    pref_key: str | None = None,
    skips: dict[str, str] | None = None,
) -> bool:
    """Send `message` + enter to workspace `ref` if it's idle and not parked.

    For PR-attached nudges (`pref_key` set — `nudges.pref_key(repo, number)`,
    per-repo because PR numbers collide across repos), check the file-backed
    mute state in `lib.nudges` so the user's `cockpit nudge mute` survives
    daemon restarts. For orphan (no-PR) nudges, fire unconditionally when idle.

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

    `skips` is an optional out-dict: on a gate skip it gets `{ref: reason}`,
    so a fan-out caller can report *why* each workspace was passed over. The
    return value stays "the nudge actually fired" — under `dry` an eligible
    workspace returns False (it sent nothing), and its absence from `skips` is
    what marks it as would-have-received.
    """
    if pref_key is not None:
        from . import nudges

        if not nudges.should_nudge(pref_key):
            if skips is not None:
                skips[ref] = "muted or snoozed"
            return False
    status_lines = cmux("list-status", "--workspace", ref, check=False).splitlines()
    native = _native_claude_state(status_lines)
    reason = _idle_skip_reason(status_lines)
    if reason is not None:
        if skips is not None:
            skips[ref] = reason
        return False
    has_idle_pill = _has_pill(status_lines, "idle")
    if native == "Idle" and not has_idle_pill and not dry:
        _set_status(ref, "idle", "idle", GREY)
    # Every newline in `message` would arrive as Enter and split it into
    # several truncated prompts — see `one_line`. Normalized before the dry
    # print so `--dry` reports the text that would actually be delivered.
    message = one_line(message)
    if dry:
        print(f"  [dry] nudge {tag} → {ref}: {message[:70]}", flush=True)
        return False
    try:
        cmux("send", "--workspace", ref, message, check=True)
        cmux("send-key", "--workspace", ref, "enter", check=True)
    except (RuntimeError, FileNotFoundError) as e:
        print(f"  warn: {tool.resolve_tool()} send failed for {ref}: {e}", flush=True)
        if skips is not None:
            skips[ref] = "send failed"
        return False
    if pref_key is not None:
        from . import nudges

        nudges.record_nudge(pref_key)
    return True


def workspace_names() -> dict[str, str]:
    """{ref: name} from `cmux list-workspaces` or `limux --json list-workspaces`.

    Raises `CmuxUnavailable` if the query exits nonzero — callers must not treat
    an empty dict as "no workspaces" when the backend itself failed.
    """
    try:
        out = cmux("list-workspaces", check=True)
    except (RuntimeError, FileNotFoundError) as e:
        # cmux() raises FileNotFoundError when the backend binary is absent.
        raise CmuxUnavailable(f"list-workspaces failed: {e}") from e
    names: dict[str, str] = {}
    for line in out.splitlines():
        # `list-workspaces` is `[*] workspace:<ref>  <name>  [flag]…`. The name
        # may contain spaces (`[repo] label`) and is followed by zero or more
        # bracketed status flags (`[selected]`). Capture the whole tail, then
        # strip the trailing flags — a bare `\S+` truncated `[repo] label` to
        # `[repo]`, collapsing every repo's workspaces into one dedupe group and
        # thrashing spawn/rename/dedupe every tick.
        m = re.search(r"(workspace:[\w-]+)\s+(.+)", line)
        if m:
            name = re.sub(r"(?:\s+\[[^\]]*\])+\s*$", "", m.group(2)).strip()
            names[m.group(1)] = name
    return names


def workspace_cwds(*, include_self: bool = False) -> dict[str, Path]:
    """{ref: current_directory} via `cmux rpc workspace.list` (cmux) or `limux --json list-workspaces` (limux).

    Raises `CmuxUnavailable` on nonzero rc or unparsable output, so a backend
    hiccup is not misread as an empty workspace set.

    Excludes the caller's OWN workspace (id == $CMUX_WORKSPACE_ID) by default:
    the daemon/TUI resolves *other* workspaces to switch/match against, and its
    own dashboard is never a valid focus/PR target — resolving an in-place repo
    that shares the dashboard's cwd to self makes `select-workspace` a no-op.
    Pass `include_self=True` when operating ON the current workspace (e.g.
    `cockpit close` run from inside the worktree it's tearing down).

    limux uses `--json` as a global flag (before the command), so the limux
    path bypasses the `cmux()` wrapper — `cmux("--json", ...)` would still
    work, but the global flag is clearer as a direct `run([...])` invocation.
    """
    if tool.is_limux():
        cwd_key = "cwd"
        label = "limux --json list-workspaces"
        # This path uses raw run() (not the cmux() wrapper, which which-checks the
        # binary), so guard explicitly: run() sys.exit(2)s when the binary is
        # absent — a SystemExit that neither this except nor the daemon's degrade
        # would catch, crashing the tick instead of degrading gracefully.
        if shutil.which("limux") is None:
            raise CmuxUnavailable(f"{label}: limux not found on PATH")
        try:
            out = run(["limux", "--json", "list-workspaces"], check=True)
        except RuntimeError as e:
            raise CmuxUnavailable(f"{label} failed: {e}") from e
    else:
        cwd_key = "current_directory"
        label = "rpc workspace.list"
        try:
            out = cmux("rpc", "workspace.list", "{}", check=True)
        except (RuntimeError, FileNotFoundError) as e:
            # cmux() raises FileNotFoundError when the binary is absent.
            raise CmuxUnavailable(f"{label} failed: {e}") from e

    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise CmuxUnavailable(f"{label} returned non-JSON: {e}") from e
    # `CMUX_WORKSPACE_ID` is the caller's own workspace UUID `id` (not its
    # `ref`); absent outside cmux/limux, so the guard degrades to skipping
    # nobody. See the docstring for why self is excluded by default.
    self_id = None if include_self else os.environ.get("CMUX_WORKSPACE_ID")
    cwds: dict[str, Path] = {}
    for ws in data.get("workspaces", []):
        if self_id and ws.get("id") == self_id:
            continue
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
    wt_by_name = {wt.workspace_name: wt for wt in wts}
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


def _pr_pill(p: dict) -> tuple[str, str, str]:
    """`pr` kind → the PR-identity pill, with CI as a trailing glyph.

    Unknown status falls back to grey. A pill carries exactly one colour, so a
    CI that isn't passing takes it: red `✗` is the actionable half of the line,
    and a green "open" saying nothing about a broken build is the reading this
    replaces. A passing or absent CI leaves GitHub's colour language alone.
    """
    icon, color = PR_STATUS_STYLE.get(p["status"], ("🔀", GREY))
    ci = str(p.get("ci") or "")
    color = RED if ci.startswith("failed") else _CI_PILL_COLOR.get(ci, color)
    label = f"{icon} PR #{p['number']} {p['status']}"
    glyph = ci_glyph(ci)
    return (PR_KEY, f"{label} {glyph}" if glyph else label, color)


_CMUX_RENDERERS = {
    "muted": lambda _p: (MUTED_KEY, f"{MUTED_ICON} muted", YELLOW),
    "rebase": lambda _p: ("rebase", "🔄 rebasing", ORANGE),
    "merge": lambda _p: ("merge", "🔀 merging", ORANGE),
    "wip": lambda p: ("wip", f"✏️ {p['count']} dirty", ORANGE),
    # The four `ci_*` kinds join `draft` and `state` as footer-only: the `pr`
    # pill below already names draftness, MERGED/CLOSED and CI, so rendering any
    # of them here would print the same fact twice on one card. All six stay
    # emitted by decide_pills — the footer renders them, and `state` is besides
    # load-bearing for merged-but-dirty workspaces, where autoclose is blocked
    # and a non-OPEN PR persists in `ctx.prs` across cycles.
    "ci_failed": lambda _p: None,
    "ci_pending": lambda _p: None,
    "ci_passed": lambda _p: None,
    "ci_unknown": lambda _p: None,
    "unaddressed": lambda p: ("comments", f"💬 {p['count']} unaddressed", RED),
    "changes_requested": lambda _p: ("comments", "💬 changes requested", RED),
    "conflict": lambda _p: ("merge", "⚠️ conflict", ORANGE),
    "draft": lambda _p: None,
    "approved": lambda _p: ("approved", "✅ approved", GREEN),
    "state": lambda _p: None,
    "pr": _pr_pill,
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
    `cockpit/hooks/cmux-idle-pill.sh` (Stop / UserPromptSubmit) — not touched here.
    """
    desired = tuple(status_pills(pr, wt, self_user, pref))
    _clear_pr_pill_keys(ref)
    for key, value, color in reversed(desired):
        _set_status(ref, key, value, color)

    return frozenset(desired)


_PR_PILL_CLEAR_KEYS = [*ACTIONABLE_KEYS, COCKPIT_KEY, OWNER_KEY, PR_KEY]


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


def select_workspace(ref: str, *, check: bool = False) -> str:
    """Switch the active cmux workspace to `ref`.

    The verb is `select-workspace` (a stable legacy alias for `workspace
    select`), NOT `focus` — `cmux focus` is not a command and exits nonzero,
    which `check=False` would silently swallow. Centralised here so the TUI's
    `f`/Enter/double-click focus actions all use the one correct verb.
    """
    return cmux("select-workspace", "--workspace", ref, check=check)


# Workspace UUIDs cockpit itself just closed, each with a monotonic deadline.
# `workspace.closed` says nothing about *who* closed a workspace, and cockpit
# closes them for four reasons that are explicitly NOT teardown: `h`/park
# ("parking is not teardown"), a trailing-fold anchor dissolve, the dead-cwd
# sweep, and teardown's own trailing close. Without this ledger the TUI's
# close-event handler would read cockpit's own park as a user gesture and tear
# down every worktree in the parked repo.
_SELF_CLOSE_TTL_SECONDS = 120.0
_self_closed: dict[str, float] = {}
_self_closed_lock = threading.Lock()


def _note_self_close(short_or_ref: str) -> None:
    """Record the UUID behind `short_or_ref` so the close event can be filtered.

    Resolved *before* the close, while the workspace is still listable. Keyed by
    UUID rather than cwd because a `use_worktree: false` repo can host several
    workspaces rooted at one checkout, and cockpit closing one of them must not
    swallow the user's close of its neighbour. cmux-only — limux has no event
    stream, so there is nothing to filter and nothing to pay for.
    """
    if not tool.is_cmux():
        return
    try:
        data = json.loads(cmux("rpc", "workspace.list", "{}", check=True))
    except (RuntimeError, FileNotFoundError, json.JSONDecodeError):
        # Unresolvable: the close still happens, it just isn't filtered. The
        # handler's own blockers gate is the backstop.
        return
    deadline = time.monotonic() + _SELF_CLOSE_TTL_SECONDS
    with _self_closed_lock:
        for ws in data.get("workspaces", []):
            keys = (
                ws.get("ref"),
                ws.get("id"),
                ws.get("custom_title"),
                ws.get("title"),
            )
            if short_or_ref in keys and ws.get("id"):
                _self_closed[str(ws["id"])] = deadline


def was_self_closed(workspace_id: str) -> bool:
    """True when cockpit itself closed `workspace_id`. Consumes the record.

    Consuming matters: a workspace id is unique per workspace, so one recorded
    close answers for exactly one event — leaving it would let a *later* user
    close of a re-created workspace inherit the same id's suppression.
    """
    now = time.monotonic()
    with _self_closed_lock:
        for wsid, deadline in list(_self_closed.items()):
            if deadline <= now:
                del _self_closed[wsid]
        return _self_closed.pop(workspace_id, None) is not None


def cmux_close_workspace_best_effort(short_or_ref: str) -> bool:
    """Close the workspace identified by name or ref.

    Returns True if the workspace no longer appears in `cmux list-workspaces`.

    Every cockpit-initiated close funnels through here, which is why the
    self-close ledger is recorded here too rather than at each of the five call
    sites — a new close path gets the filtering for free.
    """
    _note_self_close(short_or_ref)
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
    initial, followup = split_prompt_prefix(build_pr_prompt(pr))
    ref = spawn_workspace(wt.workspace_name, wt.path, claude_command(initial))
    if ref is None:
        print(
            f"  warn: could not resolve new workspace ref for {wt.short}",
            file=sys.stderr,
            flush=True,
        )
        return None
    if followup:
        deliver_followup(ref, followup)
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
    initial, followup = split_prompt_prefix(build_orphan_prompt(wt))
    ref = spawn_workspace(wt.workspace_name, wt.path, claude_command(initial))
    if ref is None:
        print(
            f"  warn: could not resolve orphan workspace ref for {wt.short}",
            file=sys.stderr,
            flush=True,
        )
        return None
    if followup:
        deliver_followup(ref, followup)
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
