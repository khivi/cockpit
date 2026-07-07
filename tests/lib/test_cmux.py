"""Tests for cmux pill consumption targeting cockpit/lib/cmux.py.

Covers `apply_pills` (clear/set behavior) and `status_pills` (kind→styling
mapping from `decide_pills` output).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cockpit.lib.cmux import (
    ACTIONABLE_KEYS,
    COCKPIT_KEY,
    DEVDONE_KEY,
    GREEN,
    MUTED_KEY,
    WORKSPACE_COLORS,
    YELLOW,
    CmuxUnavailable,
    apply_devdone_pill,
    apply_pills,
    cmux_close_workspace_best_effort,
    deliver_followup,
    nudge_if_idle,
    reconcile_workspace_names,
    rename_workspace_if_needed,
    select_workspace,
    set_workspace_color,
    spawn_workspace,
    status_pills,
    workspace_cwds,
    workspace_names,
    workspace_state,
)
from cockpit.lib.gh import PR
from cockpit.lib.git import Worktree
from cockpit.lib.nudges import NudgePref


def test_select_workspace_uses_select_workspace_verb():
    # Regression: `cmux focus` is not a command (exits nonzero); the workspace
    # switch verb is `select-workspace`.
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        return "OK workspace:12"

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        select_workspace("workspace:12")

    assert calls == [("select-workspace", "--workspace", "workspace:12")]


def _pr(**overrides) -> PR:
    base: dict = dict(
        number=1,
        title="t",
        branch="khivi/feature",
        url="https://example/pr/1",
        author="khivi",
        is_draft=False,
        review_decision="REVIEW_REQUIRED",
        mergeable="MERGEABLE",
        ci="passed",
        unaddressed=0,
        total_from_others=0,
        state="OPEN",
        updated_at="",
    )
    base.update(overrides)
    return PR(**base)


def _wt(
    branch: str = "khivi/feature",
    *,
    rebasing: bool = False,
    merging: bool = False,
    dirty: int = 0,
) -> Worktree:
    return Worktree(
        path=Path("/tmp/wt"),
        branch=branch,
        rebasing=rebasing,
        merging=merging,
        dirty_count=dirty,
    )


# ── apply_pills ─────────────────────────────────────────────────────────────


def test_apply_pills_clears_legacy_managed_key():
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        apply_pills("workspace:1", _pr(), _wt())

    cleared_keys = {args[1] for args in calls if args and args[0] == "clear-status"}
    for k in ACTIONABLE_KEYS:
        assert k in cleared_keys
    assert COCKPIT_KEY in cleared_keys
    assert "cockpit_managed" in cleared_keys


# ── status_pills (cmux mapper) ──────────────────────────────────────────────


def test_cmux_status_pills_matches_decisions():
    out = status_pills(_pr(ci="failed:lint", unaddressed=2), _wt(dirty=1))
    assert out == [
        ("wip", "✏️ 1 dirty", "#ff9500"),
        ("ci", "❌ ci:lint", "#eb445a"),
        ("comments", "💬 2 unaddressed", "#eb445a"),
    ]


def test_cmux_drops_state_pill():
    # cmux suppresses the `state` pill (sidebar surfaces merge state natively);
    # ci_passed still renders so the user sees CI status alongside merge.
    out = status_pills(_pr(state="MERGED"), _wt())
    assert out == [("ci", "✓ ci", "#16a34a")]


def test_cmux_conflict_emits_merge_key():
    out = status_pills(_pr(mergeable="CONFLICTING"), _wt())
    assert out == [
        ("ci", "✓ ci", "#16a34a"),
        ("merge", "⚠️ conflict", "#ff9500"),
    ]


def test_cmux_ci_unknown_renders_error_pill():
    out = status_pills(_pr(ci="unknown"), _wt())
    assert out == [("ci", "⚠️ ci error", "#eb445a")]


def test_cmux_owner_pill_added_for_coworker():
    out = status_pills(_pr(author="bob"), _wt(), self_user="khivi")
    assert ("owner", "👥 @bob", "#3b82f6") in out
    assert out[0] == ("owner", "👥 @bob", "#3b82f6")


def test_cmux_owner_pill_absent_for_self():
    out = status_pills(_pr(author="khivi"), _wt(), self_user="khivi")
    assert all(k != "owner" for k, _, _ in out)


def test_cmux_owner_pill_absent_when_self_user_none():
    out = status_pills(_pr(author="bob"), _wt())
    assert all(k != "owner" for k, _, _ in out)


def test_apply_pills_clears_owner_key():
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        apply_pills("workspace:1", _pr(), _wt())

    cleared_keys = {args[1] for args in calls if args and args[0] == "clear-status"}
    assert "owner" in cleared_keys


# ── CmuxUnavailable: nonzero rc must raise, not return {} ────────────────────


def test_workspace_names_raises_on_nonzero_rc():
    def fake_cmux(*_args, **_kwargs):
        raise RuntimeError("cmux list-workspaces failed: socket missing")

    with (
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
        pytest.raises(CmuxUnavailable, match="list-workspaces failed"),
    ):
        workspace_names()


def test_workspace_cwds_raises_on_nonzero_rc():
    def fake_cmux(*_args, **_kwargs):
        raise RuntimeError("cmux rpc workspace.list failed: daemon down")

    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="cmux"),
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
        pytest.raises(CmuxUnavailable, match="rpc workspace.list failed"),
    ):
        workspace_cwds()


def test_workspace_cwds_raises_on_non_json():
    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="cmux"),
        patch("cockpit.lib.cmux.cmux", return_value="not json"),
        pytest.raises(CmuxUnavailable, match="non-JSON"),
    ):
        workspace_cwds()


def test_workspace_state_propagates_cmux_unavailable():
    def fake_cmux(*_args, **_kwargs):
        raise RuntimeError("backend offline")

    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="cmux"),
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
        pytest.raises(CmuxUnavailable),
    ):
        workspace_state()


def test_workspace_names_parses_ok_when_cmux_ok():
    with patch(
        "cockpit.lib.cmux.cmux",
        return_value="workspace:1 feat-x\nworkspace:2 other\n",
    ):
        assert workspace_names() == {"workspace:1": "feat-x", "workspace:2": "other"}


def test_workspace_names_parses_limux_uuid_refs():
    output = (
        "  workspace:850fee36-6efb-48b1-91cc-27225bb45c44 needl-ai\n"
        "* workspace:65160839-6664-4325-9d3c-bf272aa7d13a feature-branch\n"
    )
    with patch("cockpit.lib.cmux.cmux", return_value=output):
        result = workspace_names()
        assert result["workspace:850fee36-6efb-48b1-91cc-27225bb45c44"] == "needl-ai"
        assert (
            result["workspace:65160839-6664-4325-9d3c-bf272aa7d13a"] == "feature-branch"
        )


def test_workspace_names_keeps_repo_prefixed_multiword_names():
    # Real cmux output: 2-space column gaps, `[repo] label` names (with an
    # internal space), and a trailing `[selected]` flag. A `\S+` parse truncated
    # these to `[Cockpit]`/`[beta]`, collapsing every repo's workspaces into one
    # dedupe group → spawn/rename/dedupe churned every tick.
    output = (
        "  workspace:2  cockpit\n"
        "* workspace:140  [Cockpit] trello  [selected]\n"
        "  workspace:141  [Cockpit] race\n"
        "  workspace:210  [beta] dependabot-npm-and-yarn-qs-and\n"
    )
    with patch("cockpit.lib.cmux.cmux", return_value=output):
        assert workspace_names() == {
            "workspace:2": "cockpit",
            "workspace:140": "[Cockpit] trello",
            "workspace:141": "[Cockpit] race",
            "workspace:210": "[beta] dependabot-npm-and-yarn-qs-and",
        }


def test_workspace_cwds_parses_ok_when_cmux_ok():
    payload = '{"workspaces":[{"ref":"workspace:1","current_directory":"/tmp/wt"}]}'
    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="cmux"),
        patch("cockpit.lib.cmux.cmux", return_value=payload),
    ):
        assert workspace_cwds() == {"workspace:1": Path("/tmp/wt")}


def test_workspace_cwds_parses_limux_json():
    payload = '{"workspace_id":"123","workspaces":[{"ref":"workspace:abc-def","cwd":"/tmp/wt"}]}'
    # limux path bypasses the cmux() wrapper because --json is a global flag
    # that must come before the command.
    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="limux"),
        patch("cockpit.lib.cmux.shutil.which", return_value="/usr/bin/limux"),
        patch("cockpit.lib.cmux.run", return_value=payload),
    ):
        assert workspace_cwds() == {"workspace:abc-def": Path("/tmp/wt")}


def test_workspace_cwds_limux_raises_cmux_unavailable_when_binary_absent():
    """tool=limux but the binary isn't on PATH: degrade via the catchable
    CmuxUnavailable, NOT run()'s sys.exit (which would crash the tick)."""
    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="limux"),
        patch("cockpit.lib.cmux.shutil.which", return_value=None),
        pytest.raises(CmuxUnavailable, match="not found on PATH"),
    ):
        workspace_cwds()


def test_workspace_cwds_cmux_raises_cmux_unavailable_when_binary_absent():
    """cmux backend, binary absent: cmux() raises FileNotFoundError, which must
    be converted to CmuxUnavailable (not leak past the degrade)."""
    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="cmux"),
        patch("cockpit.lib.cmux.cmux", side_effect=FileNotFoundError("cmux")),
        pytest.raises(CmuxUnavailable, match="rpc workspace.list failed"),
    ):
        workspace_cwds()


def test_workspace_names_raises_cmux_unavailable_when_binary_absent():
    """Missing backend binary: cmux() raises FileNotFoundError; workspace_names
    must surface it as CmuxUnavailable so callers' degrade catches it."""
    with (
        patch("cockpit.lib.cmux.cmux", side_effect=FileNotFoundError("limux")),
        pytest.raises(CmuxUnavailable, match="list-workspaces failed"),
    ):
        workspace_names()


def test_spawn_workspace_limux_parses_ref_and_renames():
    """limux returns 'OK workspace:<uuid>' on stdout; spawn_workspace must
    parse the ref directly and follow up with rename-workspace."""
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        if args[0] == "new-workspace":
            return "OK workspace:abc-123-def\n"
        return ""

    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="limux"),
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
    ):
        ref = spawn_workspace("my-short", Path("/tmp/wt"), "claude --help")

    assert ref == "workspace:abc-123-def"
    # new-workspace call must omit --name / --focus on limux
    new_call = next(c for c in calls if c[0] == "new-workspace")
    assert "--name" not in new_call
    assert "--focus" not in new_call
    assert "--cwd" in new_call and "/tmp/wt" in new_call
    # rename follow-up applies the desired short name
    rename_call = next(c for c in calls if c[0] == "rename-workspace")
    assert "--workspace" in rename_call
    assert "workspace:abc-123-def" in rename_call
    assert "my-short" in rename_call


def test_spawn_workspace_cmux_polls_for_new_ref():
    """cmux path still uses --name/--focus and polls list-workspaces."""
    list_outputs = iter(["workspace:1 old\n", "workspace:1 old\nworkspace:2 new\n"])

    def fake_cmux(*args, **_kwargs):
        if args[0] == "list-workspaces":
            return next(list_outputs)
        if args[0] == "new-workspace":
            # cmux's new-workspace returns nothing useful on stdout
            return ""
        return ""

    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="cmux"),
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
    ):
        ref = spawn_workspace("feat", Path("/tmp/wt"), "claude")

    assert ref == "workspace:2"


# ── deliver_followup (two-send prompt_prefix flow) ───────────────────────────


def test_deliver_followup_sends_text_then_enter_when_ready():
    """Once claude reports a `claude_code=` state (TUI up), the body is typed
    into the workspace and submitted with Enter."""
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        if args[0] == "list-status":
            return "claude_code=Idle icon=x color=#fff\n"
        return ""

    with (
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
        patch("cockpit.lib.tool.resolve_tool", return_value="cmux"),
    ):
        ok = deliver_followup("workspace:1", "the task body")

    assert ok is True
    send = next(c for c in calls if c[0] == "send")
    assert "--workspace" in send and "workspace:1" in send
    assert "the task body" in send
    assert any(c[0] == "send-key" and "enter" in c for c in calls)


def test_deliver_followup_polls_until_claude_boots():
    """Keystrokes wait for the TUI: poll `list-status` until claude registers a
    state, sleeping between polls, so the body isn't dropped mid-boot."""
    statuses = iter(["", "", "claude_code=Running icon=x\n"])

    def fake_cmux(*args, **_kwargs):
        if args[0] == "list-status":
            return next(statuses)
        return ""

    sleeps: list[float] = []
    with (
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
        patch("cockpit.lib.tool.resolve_tool", return_value="cmux"),
        patch("cockpit.lib.cmux.time.sleep", side_effect=sleeps.append),
    ):
        ok = deliver_followup("workspace:1", "body")

    assert ok is True
    assert len(sleeps) == 2  # slept after the first two not-ready polls


def test_deliver_followup_send_failure_returns_false():
    """A send failure (e.g. broken pipe) is logged, not raised."""

    def fake_cmux(*args, **_kwargs):
        if args[0] == "list-status":
            return "claude_code=Idle\n"
        if args[0] == "send":
            raise RuntimeError("broken pipe")
        return ""

    with (
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
        patch("cockpit.lib.tool.resolve_tool", return_value="cmux"),
    ):
        assert deliver_followup("workspace:1", "body") is False


# ── rename_workspace_if_needed / reconcile_workspace_names ───────────────────


def test_rename_workspace_if_needed_noop_when_matching():
    calls: list[tuple] = []
    with patch("cockpit.lib.cmux.cmux", side_effect=lambda *a, **_k: calls.append(a)):
        assert rename_workspace_if_needed("workspace:1", "feat", "feat") is False
    assert calls == []


def test_rename_workspace_if_needed_noop_when_expected_empty():
    """An empty expected name (ref not in the names dict) must never rename to ""."""
    calls: list[tuple] = []
    with patch("cockpit.lib.cmux.cmux", side_effect=lambda *a, **_k: calls.append(a)):
        assert rename_workspace_if_needed("workspace:1", "", "whatever") is False
    assert calls == []


def test_rename_workspace_if_needed_renames_when_diverged():
    calls: list[tuple] = []
    with patch("cockpit.lib.cmux.cmux", side_effect=lambda *a, **_k: calls.append(a)):
        assert rename_workspace_if_needed("workspace:1", "feat", "old-name") is True
    assert calls == [("rename-workspace", "--workspace", "workspace:1", "feat")]


def test_rename_workspace_if_needed_dry_reports_without_calling():
    calls: list[tuple] = []
    with patch("cockpit.lib.cmux.cmux", side_effect=lambda *a, **_k: calls.append(a)):
        assert (
            rename_workspace_if_needed("workspace:1", "feat", "old", dry=True) is True
        )
    assert calls == []


def test_reconcile_workspace_names_renames_cwd_matched_diverged(tmp_path):
    """Only cwd-matched, name-drifted workspaces rename; name-matched and
    cwd-unmatched refs are left alone. The expected name is the branch-derived
    `label`, NOT the dir basename — the motivating case: a dir `pe-4516` holding
    branch `khivi/pe-4608-understand-dag-builder` labels by the branch."""
    wt_a = tmp_path / "pe-4516"  # dir name diverged from its branch
    wt_a.mkdir()
    wt_b = tmp_path / "feat-b"
    wt_b.mkdir()
    wts = [
        Worktree(
            path=wt_a,
            branch="khivi/pe-4608-understand-dag-builder",
            branch_prefix="khivi/",
        ),
        Worktree(path=wt_b, branch="khivi/b", branch_prefix="khivi/"),
    ]
    names = {"workspace:1": "pe-4516", "workspace:2": "b"}
    cwds = {
        "workspace:1": wt_a,  # name tracks dir → rename to branch label
        "workspace:2": wt_b,  # already matches label "b" → skip
        "workspace:3": tmp_path / "elsewhere",  # no wt at this cwd → skip
    }
    calls: list[tuple] = []
    with patch("cockpit.lib.cmux.cmux", side_effect=lambda *a, **_k: calls.append(a)):
        renamed = reconcile_workspace_names(names, cwds, wts)

    assert renamed == [("workspace:1", "pe-4516", "understand-dag-builder")]
    assert calls == [
        ("rename-workspace", "--workspace", "workspace:1", "understand-dag-builder")
    ]


def test_reconcile_workspace_names_skips_primary_checkout(tmp_path):
    """A workspace parked on the primary checkout (e.g. one the user named
    'morning' to run skills on master) must NOT be force-renamed to the repo
    dir name. The primary dir can't be renamed to dodge it, so it's exempt."""
    primary = tmp_path / "needl-ai"
    primary.mkdir()
    wts = [Worktree(path=primary, branch="master", is_primary=True)]
    names = {"workspace:1": "morning"}  # user's custom name on the main checkout
    cwds = {"workspace:1": primary}
    calls: list[tuple] = []
    with patch("cockpit.lib.cmux.cmux", side_effect=lambda *a, **_k: calls.append(a)):
        renamed = reconcile_workspace_names(names, cwds, wts)

    assert renamed == []
    assert calls == []


def test_reconcile_workspace_names_skips_main_branch_worktree(tmp_path):
    """A feature worktree parked on `main`/`master` is exempt even when it's NOT
    the primary checkout — the bare-repo case where no sibling is ever
    `is_primary`. Its `label` collapses to the branch name, so a rename would
    clobber a sibling already named `main` and break switching."""
    wt_a = tmp_path / "feature-on-main"
    wt_a.mkdir()
    # Not primary (is_primary defaults False), but sitting on `main`.
    wts = [Worktree(path=wt_a, branch="main", branch_prefix="khivi/")]
    names = {"workspace:1": "fix-oauth"}  # diverged custom name
    cwds = {"workspace:1": wt_a}
    calls: list[tuple] = []
    with patch("cockpit.lib.cmux.cmux", side_effect=lambda *a, **_k: calls.append(a)):
        renamed = reconcile_workspace_names(names, cwds, wts)

    assert renamed == []
    assert calls == []


def test_reconcile_workspace_names_dry_reports_without_calling(tmp_path):
    wt_a = tmp_path / "feat-a"
    wt_a.mkdir()
    wts = [Worktree(path=wt_a, branch="khivi/a", branch_prefix="khivi/")]
    names = {"workspace:1": "old"}
    cwds = {"workspace:1": wt_a}
    calls: list[tuple] = []
    with patch("cockpit.lib.cmux.cmux", side_effect=lambda *a, **_k: calls.append(a)):
        renamed = reconcile_workspace_names(names, cwds, wts, dry=True)

    assert renamed == [("workspace:1", "old", "a")]
    assert calls == []


def test_close_workspace_best_effort_passes_workspace_flag():
    """`limux close-workspace <ref>` (positional) is silently misinterpreted as
    "close the focused workspace" — closing the wrong one. The call must pass
    `--workspace <ref>` explicitly. This test locks that in.
    """
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        if args[0] == "list-workspaces":
            return ""
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        cmux_close_workspace_best_effort("workspace:abc-123-def")

    close_call = next(c for c in calls if c[0] == "close-workspace")
    assert (
        "--workspace" in close_call
    ), f"close-workspace must use --workspace flag, got {close_call}"
    assert "workspace:abc-123-def" in close_call


# ── muted pill ──────────────────────────────────────────────────────────────


def test_apply_devdone_pill_sets_label_when_ticket():
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        apply_devdone_pill("workspace:1", "PE-1234")

    set_call = next(c for c in calls if c[0] == "set-status")
    assert set_call[1] == DEVDONE_KEY
    assert set_call[2] == "🏁 dev-done PE-1234"
    assert "--color" in set_call and GREEN in set_call


def test_apply_devdone_pill_clears_when_none():
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        apply_devdone_pill("workspace:1", None)

    clear_call = next(c for c in calls if c[0] == "clear-status")
    assert clear_call[1] == DEVDONE_KEY
    assert all(c[0] != "set-status" for c in calls)


def test_devdone_not_in_actionable_keys():
    # Passive slow-tick visual — must never be swept by apply_pills.
    assert DEVDONE_KEY not in ACTIONABLE_KEYS


def test_status_pills_mute_emits_muted_tuple_at_front():
    pref = NudgePref(muted=True)
    out = status_pills(_pr(), _wt(), pref=pref)
    # muted anchors the row; ci_passed still emits since muted doesn't suppress it.
    assert out[0] == (MUTED_KEY, "🔇 muted", YELLOW)
    assert any(k == "ci" for k, _, _ in out)


def test_status_pills_no_mute_no_muted_tuple():
    pref = NudgePref()
    out = status_pills(_pr(), _wt(), pref=pref)
    assert all(k != MUTED_KEY for k, _, _ in out)


def test_status_pills_muted_with_owner_pill_for_coworker():
    pref = NudgePref(muted=True)
    out = status_pills(_pr(author="bob"), _wt(), self_user="khivi", pref=pref)
    # owner is prepended for reversed set-order; muted comes from decide_pills.
    assert out[0] == ("owner", "👥 @bob", "#3b82f6")
    assert (MUTED_KEY, "🔇 muted", YELLOW) in out


def test_apply_pills_clears_muted_key():
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        apply_pills("workspace:1", _pr(), _wt())

    cleared_keys = {args[1] for args in calls if args and args[0] == "clear-status"}
    assert MUTED_KEY in cleared_keys


# ── set_workspace_color ──────────────────────────────────────────────────────


def test_set_workspace_color_builds_workspace_action_argv():
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        set_workspace_color("workspace:7", "Teal")

    assert calls == [
        (
            "workspace-action",
            "--action",
            "set-color",
            "--color",
            "Teal",
            "--workspace",
            "workspace:7",
        )
    ]


def test_set_workspace_color_noops_on_limux():
    """workspace-action is gated cmux-only (in _PILL_VERBS) — on limux it must
    resolve to no binary and never shell out, so limux users silently skip the
    sidebar tint rather than erroring."""
    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="limux"),
        patch("cockpit.lib.cmux.run") as run_mock,
    ):
        set_workspace_color("workspace:7", "Teal")

    run_mock.assert_not_called()


def test_workspace_colors_include_cockpit_defaults():
    # Defaults seeded in config.example.json must be valid cmux color names.
    assert {"Blue", "Teal", "Purple"} <= WORKSPACE_COLORS


def test_workspace_colors_derived_from_color_ansi_map():
    # Single source of truth: the valid set is exactly the log-echo map's keys,
    # so a name added to one can't be missing from the other.
    from cockpit.lib.colors import CMUX_COLOR_ANSI

    assert frozenset(CMUX_COLOR_ANSI) == WORKSPACE_COLORS


# ── nudge_if_idle ────────────────────────────────────────────────────────────


def _idle_status_lines(*, parked: bool = False) -> str:
    lines = ["idle=1"]
    if parked:
        lines.append("parked=1")
    return "\n".join(lines)


def _native_line(state: str) -> str:
    """A realistic `claude_code=` list-status line for a given native state."""
    icon = {
        "Running": "bolt.fill",
        "Idle": "pause.circle.fill",
        "Needs input": "bell.fill",
    }[state]
    return f"claude_code={state} icon={icon} color=#4C8DFF"


def test_nudge_if_idle_returns_true_on_success(capsys):
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        if args[0] == "list-status":
            return _idle_status_lines()
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        result = nudge_if_idle("workspace:1", "fix CI", tag="feat-x")

    assert result is True
    sent = [args for args in calls if args[0] == "send"]
    assert len(sent) == 1
    assert sent[0][3] == "fix CI"
    assert capsys.readouterr().out == ""


def test_nudge_if_idle_prints_error_and_returns_false_on_send_failure(capsys):
    def fake_cmux(*args, check=True, **_kwargs):
        if args[0] == "list-status":
            return _idle_status_lines()
        if args[0] == "send" and check:
            raise RuntimeError("cmux send failed: socket gone")
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        result = nudge_if_idle("workspace:1", "fix CI", tag="feat-x")

    assert result is False
    out = capsys.readouterr().out
    assert "warn" in out
    assert "workspace:1" in out


def test_nudge_if_idle_skips_when_not_idle():
    def fake_cmux(*args, **_kwargs):
        if args[0] == "list-status":
            return ""  # no idle pill
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        result = nudge_if_idle("workspace:1", "fix CI", tag="feat-x")

    assert result is False


def test_nudge_if_idle_skips_when_parked():
    def fake_cmux(*args, **_kwargs):
        if args[0] == "list-status":
            return _idle_status_lines(parked=True)
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        result = nudge_if_idle("workspace:1", "fix CI", tag="feat-x")

    assert result is False


def test_nudge_if_idle_does_not_record_nudge_on_send_failure():
    """Failed send must not record the nudge — so the next tick retries."""
    recorded: list[tuple] = []

    def fake_cmux(*args, check=True, **_kwargs):
        if args[0] == "list-status":
            return _idle_status_lines()
        if args[0] == "send" and check:
            raise RuntimeError("socket gone")
        return ""

    with (
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
        patch(
            "cockpit.lib.nudges.record_nudge", side_effect=lambda *a: recorded.append(a)
        ),
    ):
        nudge_if_idle("workspace:1", "fix CI", tag="t", pr_number=42)

    assert recorded == []


def test_nudge_if_idle_records_nudge_on_success():
    recorded: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        if args[0] == "list-status":
            return _idle_status_lines()
        return ""

    with (
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
        patch("cockpit.lib.nudges.should_nudge", return_value=True),
        patch(
            "cockpit.lib.nudges.record_nudge", side_effect=lambda *a: recorded.append(a)
        ),
    ):
        result = nudge_if_idle("workspace:1", "fix CI", tag="t", pr_number=42)

    assert result is True
    assert recorded == [(42,)]


# ── native-state gate (the stale-pill regression + permission safety) ────────


def test_nudge_fires_on_native_idle_without_pill_and_self_heals():
    """cmux reports the unambiguous native `Idle` but the Stop-hook `idle=` pill
    was dropped. Nudge must still fire AND re-assert the pill (self-heal)."""
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        if args[0] == "list-status":
            return _native_line("Idle")  # no idle= pill present
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        result = nudge_if_idle("workspace:1", "fix CI", tag="t")

    assert result is True
    assert any(a[0] == "send" for a in calls)
    set_idle = [a for a in calls if a[0] == "set-status" and a[1] == "idle"]
    assert len(set_idle) == 1, calls  # self-healed the dropped pill


def test_nudge_suppressed_on_bare_needs_input():
    """`Needs input` is ambiguous (idle-at-prompt OR a pending y/n permission).
    With no `idle=` pill it must NOT nudge — the regression-fix must not become a
    new hazard of typing into a confirmation prompt."""
    sends: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        if args[0] == "send":
            sends.append(args)
        if args[0] == "list-status":
            return _native_line("Needs input")
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        result = nudge_if_idle("workspace:1", "fix CI", tag="t")

    assert result is False
    assert sends == []


def test_nudge_fires_when_idle_pill_present_even_if_native_needs_input():
    """The persistent `idle=` pill (set only at Stop, never mid-permission) is a
    trusted safe signal. `Needs input` alongside it is genuine idle-at-prompt."""

    def fake_cmux(*args, **_kwargs):
        if args[0] == "list-status":
            return _idle_status_lines() + "\n" + _native_line("Needs input")
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        result = nudge_if_idle("workspace:1", "fix CI", tag="t")

    assert result is True


def test_nudge_suppressed_when_native_running_even_with_idle_pill():
    """Native `Running` always blocks — catches a dropped `idle=` clear that
    left a stale pill on a now-active session."""

    def fake_cmux(*args, **_kwargs):
        if args[0] == "list-status":
            return _idle_status_lines() + "\n" + _native_line("Running")
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        result = nudge_if_idle("workspace:1", "fix CI", tag="t")

    assert result is False


def test_nudge_suppressed_when_parked_even_on_native_idle():
    def fake_cmux(*args, **_kwargs):
        if args[0] == "list-status":
            return _native_line("Idle") + "\nparked=1"
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        result = nudge_if_idle("workspace:1", "fix CI", tag="t")

    assert result is False


def test_native_claude_state_parsing():
    from cockpit.lib.cmux import _native_claude_state

    assert _native_claude_state([_native_line("Needs input")]) == "Needs input"
    assert _native_claude_state([_native_line("Running")]) == "Running"
    assert _native_claude_state(["  claude_code=Idle"]) == "Idle"
    assert _native_claude_state(["idle=1", "ci=✓ ci"]) is None
