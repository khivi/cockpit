"""Tests for cmux pill consumption targeting cockpit/lib/cmux.py.

Covers `apply_pills` (clear/set behavior) and `status_pills` (kind→styling
mapping from `decide_pills` output).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import cockpit.lib.cmux as cmux_mod
from cockpit.lib.cmux import (
    ACTIONABLE_KEYS,
    COCKPIT_KEY,
    DEVDONE_KEY,
    GREEN,
    MUTED_KEY,
    PARKED_KEY,
    PR_KEY,
    WORKSPACE_COLORS,
    YELLOW,
    CmuxUnavailable,
    add_to_workspace_group,
    apply_devdone_pill,
    apply_pills,
    cmux_close_workspace_best_effort,
    create_workspace_group,
    deliver_followup,
    list_workspace_groups,
    move_workspace_group_to_end,
    nudge_if_idle,
    one_line,
    reassert_idle_pills,
    reconcile_workspace_names,
    remove_from_workspace_group,
    rename_workspace_group,
    rename_workspace_if_needed,
    rest_skip_reason,
    select_workspace,
    set_workspace_color,
    spawn_workspace,
    status_pills,
    ungroup_workspaces,
    was_self_closed,
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
    base: dict = {
        "number": 1,
        "title": "t",
        "branch": "khivi/feature",
        "url": "https://example/pr/1",
        "author": "khivi",
        "is_draft": False,
        "review_decision": "REVIEW_REQUIRED",
        "mergeable": "MERGEABLE",
        "ci": "passed",
        "unaddressed": 0,
        "total_from_others": 0,
        "state": "OPEN",
        "updated_at": "",
    }
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


# ── status_pills (cmux mapper) ──────────────────────────────────────────────


OPEN_PR_PILL = ("pr", "🟢 PR #1 open ✓", "#16a34a")


def test_cmux_status_pills_matches_decisions():
    out = status_pills(_pr(ci="failed:lint", unaddressed=2), _wt(dirty=1))
    assert out == [
        ("wip", "✏️ 1 dirty", "#ff9500"),
        ("comments", "💬 2 unaddressed", "#eb445a"),
        ("pr", "🟢 PR #1 open ✗", "#eb445a"),
    ]


def test_cmux_drops_state_pill():
    # cmux suppresses the `state` pill — the `pr` pill already carries MERGED,
    # and its trailing glyph carries CI.
    out = status_pills(_pr(state="MERGED"), _wt())
    assert out == [("pr", "🟣 PR #1 merged ✓", "#8957e5")]


def test_cmux_conflict_emits_merge_key():
    out = status_pills(_pr(mergeable="CONFLICTING"), _wt())
    assert out == [
        ("merge", "⚠️ conflict", "#ff9500"),
        OPEN_PR_PILL,
    ]


def test_cmux_ci_unknown_reddens_the_pr_pill():
    out = status_pills(_pr(ci="unknown"), _wt())
    assert out == [("pr", "🟢 PR #1 open ?", "#eb445a")]


# ── pr pill (replaces cmux's native sidebar PR row) ──────────────────────────


def test_cmux_pr_pill_icon_and_color_track_state():
    assert status_pills(_pr(number=332), _wt())[-1] == (
        "pr",
        "🟢 PR #332 open ✓",
        "#16a34a",
    )
    assert status_pills(_pr(number=332, is_draft=True), _wt())[-1] == (
        "pr",
        "⚪ PR #332 draft ✓",
        "#6b7280",
    )
    assert status_pills(_pr(number=330, state="MERGED"), _wt())[-1] == (
        "pr",
        "🟣 PR #330 merged ✓",
        "#8957e5",
    )
    assert status_pills(_pr(number=296, state="CLOSED"), _wt())[-1] == (
        "pr",
        "🔴 PR #296 closed ✓",
        "#eb445a",
    )


def test_cmux_drops_ci_pills_because_the_pr_pill_carries_ci():
    # Both would print CI on one card. decide_pills still emits the `ci_*`
    # kinds for the footer — only this renderer drops them.
    for ci in ("passed", "failed:lint", "pending", "unknown"):
        out = status_pills(_pr(ci=ci), _wt())
        assert all(k != "ci" for k, _, _ in out), ci


def test_cmux_pr_pill_glyph_and_color_track_ci():
    # A pill carries one colour, so a non-passing CI takes it from the PR state.
    for ci, glyph, color in (
        ("passed", "✓", "#16a34a"),
        ("failed:lint", "✗", "#eb445a"),
        ("failed", "✗", "#eb445a"),
        ("pending", "•", "#ff9500"),
        ("unknown", "?", "#eb445a"),
    ):
        assert status_pills(_pr(ci=ci), _wt())[-1] == (
            "pr",
            f"🟢 PR #1 open {glyph}",
            color,
        ), ci


def test_cmux_failing_ci_outranks_the_pr_state_color():
    # A red ✗ on a grey draft is the whole point: the state colour would say
    # nothing about the broken build.
    assert status_pills(_pr(is_draft=True, ci="failed:1"), _wt())[-1] == (
        "pr",
        "⚪ PR #1 draft ✗",
        "#eb445a",
    )


def test_cmux_passing_ci_leaves_the_state_color_alone():
    # Otherwise every merged PR with green CI would render green, not purple.
    assert status_pills(_pr(state="MERGED"), _wt())[-1][2] == "#8957e5"


def test_cmux_pr_pill_has_no_trailing_space_without_ci():
    assert status_pills(_pr(ci="none"), _wt())[-1] == ("pr", "🟢 PR #1 open", "#16a34a")


def test_ci_stays_in_actionable_keys_so_an_upgraded_install_clears_it():
    # The renderer no longer writes `ci=`, but an install that ran an older
    # cockpit still has one on its cards; only this membership sweeps it.
    assert "ci" in ACTIONABLE_KEYS


def test_cmux_drops_draft_pill_because_the_pr_pill_says_draft():
    # Both would print draftness on one card. The footer keeps its own `draft`
    # pill — only this renderer drops it.
    out = status_pills(_pr(is_draft=True), _wt())
    assert all(k != "draft" for k, _, _ in out)
    assert out[-1] == ("pr", "⚪ PR #1 draft ✓", "#6b7280")


def test_apply_pills_clears_the_pr_key():
    # Without this the pill would survive `clear_pr_pills` on a reused branch —
    # the exact stale-PR bug cmux's native row has.
    calls: list[tuple] = []

    with patch("cockpit.lib.cmux.cmux", side_effect=lambda *a, **_k: calls.append(a)):
        apply_pills("workspace:1", _pr(), _wt())

    cleared = {args[1] for args in calls if args and args[0] == "clear-status"}
    assert PR_KEY in cleared


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


def test_workspace_cwds_skips_own_dashboard_workspace(monkeypatch):
    # The daemon's own workspace (id == $CMUX_WORKSPACE_ID) is dropped so an
    # in-place repo sharing its cwd doesn't resolve focus to self (a no-op).
    payload = (
        '{"workspaces":['
        '{"ref":"workspace:2","id":"SELF-UUID","current_directory":"/tmp/dash"},'
        '{"ref":"workspace:404","id":"OTHER-UUID","current_directory":"/tmp/wt"}'
        "]}"
    )
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "SELF-UUID")
    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="cmux"),
        patch("cockpit.lib.cmux.cmux", return_value=payload),
    ):
        assert workspace_cwds() == {"workspace:404": Path("/tmp/wt")}


def test_workspace_cwds_keeps_all_when_env_unset(monkeypatch):
    # No CMUX_WORKSPACE_ID (daemon outside cmux): skip nobody.
    payload = (
        '{"workspaces":['
        '{"ref":"workspace:2","id":"SELF-UUID","current_directory":"/tmp/dash"},'
        '{"ref":"workspace:404","id":"OTHER-UUID","current_directory":"/tmp/wt"}'
        "]}"
    )
    monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="cmux"),
        patch("cockpit.lib.cmux.cmux", return_value=payload),
    ):
        assert workspace_cwds() == {
            "workspace:2": Path("/tmp/dash"),
            "workspace:404": Path("/tmp/wt"),
        }


def test_workspace_cwds_include_self_keeps_own_workspace(monkeypatch):
    # `cockpit close` runs from inside the worktree it tears down, so it must
    # still resolve its own workspace ref (include_self=True).
    payload = (
        '{"workspaces":['
        '{"ref":"workspace:2","id":"SELF-UUID","current_directory":"/tmp/dash"}'
        "]}"
    )
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "SELF-UUID")
    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="cmux"),
        patch("cockpit.lib.cmux.cmux", return_value=payload),
    ):
        assert workspace_cwds(include_self=True) == {"workspace:2": Path("/tmp/dash")}


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


def test_deliver_followup_collapses_the_text_to_one_line():
    """`cmux send` synthesizes keypresses, so a newline arrives as Enter — i.e.
    submit. Un-normalized, "do X\ndo Y" submits "do X" as its own truncated
    instruction and leaves "do Y" behind. `nudge_if_idle` collapses inside the
    gate; this is the funnel for every send that does NOT go through it."""
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
        assert deliver_followup("workspace:1", "rebase onto main\nthen force-push")

    send = next(c for c in calls if c[0] == "send")
    body = send[-1]
    assert "\n" not in body
    assert "rebase onto main" in body and "then force-push" in body
    # One submission, not one per fragment.
    assert sum(1 for c in calls if c[0] == "send-key") == 1


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


# ── self-close ledger ───────────────────────────────────────────────────────
#
# The TUI treats a `workspace.closed` event as the user clicking cmux's X, which
# means teardown. cockpit closes workspaces itself for four reasons that are NOT
# teardown (`h`/park, fold-anchor dissolve, the dead-cwd sweep, teardown's own
# trailing close), and the event says nothing about who closed it — so its own
# closes are recorded here and filtered out there. Park is the dangerous one:
# it is documented as workspace-only, and misreading it tears down every
# worktree in the parked repo.

_WS_LIST = json.dumps(
    {
        "workspaces": [
            {"ref": "workspace:1", "id": "UUID-1", "custom_title": "feat-a"},
            {"ref": "workspace:2", "id": "UUID-2", "custom_title": "feat-b"},
        ]
    }
)


@pytest.fixture(autouse=True)
def _clear_self_close_ledger():
    """Process-global, so one case's recorded close must not answer the next."""
    cmux_mod._self_closed.clear()
    yield
    cmux_mod._self_closed.clear()


def _close_with_stub_list(target: str):
    """Run a close against a cmux whose `rpc workspace.list` returns `_WS_LIST`."""

    def fake_cmux(*args, **_kwargs):
        if args[:2] == ("rpc", "workspace.list"):
            return _WS_LIST
        return ""

    with (
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
        patch("cockpit.lib.cmux.tool.is_cmux", return_value=True),
    ):
        cmux_close_workspace_best_effort(target)


@pytest.mark.parametrize("target", ["workspace:2", "UUID-2", "feat-b"])
def test_our_own_close_is_recorded_by_uuid_however_it_was_addressed(target):
    """`cmux_close_workspace_best_effort` takes a ref, a uuid, or a name, but
    the event only ever reports a uuid — so all three must resolve to one."""
    _close_with_stub_list(target)

    assert was_self_closed("UUID-2") is True
    assert was_self_closed("UUID-1") is False


def test_a_user_close_is_not_filtered():
    _close_with_stub_list("workspace:1")

    # workspace:2 was never closed by us — the X on it is the user's gesture.
    assert was_self_closed("UUID-2") is False


def test_the_record_is_consumed_by_the_first_matching_event():
    """One recorded close answers for exactly one event. Leaving it would let a
    later user-close of a re-created workspace inherit the suppression."""
    _close_with_stub_list("workspace:1")

    assert was_self_closed("UUID-1") is True
    assert was_self_closed("UUID-1") is False


def test_the_record_expires():
    _close_with_stub_list("workspace:1")

    with patch(
        "cockpit.lib.cmux.time.monotonic",
        return_value=time.monotonic() + cmux_mod._SELF_CLOSE_TTL_SECONDS + 1,
    ):
        assert was_self_closed("UUID-1") is False
    assert cmux_mod._self_closed == {}  # swept, not merely skipped


def test_the_ledger_is_recorded_before_the_close_lands():
    """Resolved while the workspace is still listable — cmux drops it from
    `workspace.list` the moment it closes, so recording after would find
    nothing and every self-close would leak through as a user gesture."""
    order: list[str] = []

    def fake_cmux(*args, **_kwargs):
        order.append(args[0] if args[0] != "rpc" else "rpc")
        return _WS_LIST if args[:2] == ("rpc", "workspace.list") else ""

    with (
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
        patch("cockpit.lib.cmux.tool.is_cmux", return_value=True),
    ):
        cmux_close_workspace_best_effort("workspace:1")

    assert order.index("rpc") < order.index("close-workspace")


def test_an_unresolvable_list_still_closes():
    """A cmux hiccup must never block the close itself — the handler's own
    blockers gate is the backstop for the unfiltered event."""
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        if args[:2] == ("rpc", "workspace.list"):
            raise RuntimeError("rpc down")
        return ""

    with (
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
        patch("cockpit.lib.cmux.tool.is_cmux", return_value=True),
    ):
        cmux_close_workspace_best_effort("workspace:1")

    assert any(c[0] == "close-workspace" for c in calls)
    assert cmux_mod._self_closed == {}


def test_limux_never_pays_for_the_lookup():
    """limux has no event stream, so there is nothing to filter."""
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        return ""

    with (
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
        patch("cockpit.lib.cmux.tool.is_cmux", return_value=False),
    ):
        cmux_close_workspace_best_effort("workspace:1")

    assert not any(c[:2] == ("rpc", "workspace.list") for c in calls)


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
    assert set_call[2] == "🏁 PE-1234"
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
    # muted anchors the row; the pr pill still emits since muted doesn't suppress it.
    assert out[0] == (MUTED_KEY, "🔇 muted", YELLOW)
    assert out[-1] == OPEN_PR_PILL


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
    """workspace-action is gated cmux-only (in _CMUX_ONLY_VERBS) — on limux it must
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


@pytest.mark.parametrize(
    "status, reason",
    [
        (_native_line("Running"), "mid-turn"),
        ("idle=1\n" + _native_line("Running"), "mid-turn"),
        (_native_line("Needs input"), "not at rest (Needs input)"),
        ("", "not at rest (no Claude session)"),
        (_idle_status_lines(parked=True), "parked"),
    ],
)
def test_nudge_if_idle_records_the_skip_reason(status, reason):
    skips: dict[str, str] = {}

    def fake_cmux(*args, **_kwargs):
        return status if args[0] == "list-status" else ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        assert nudge_if_idle("workspace:1", "hi", tag="t", skips=skips) is False

    assert skips == {"workspace:1": reason}


def test_nudge_if_idle_leaves_skips_empty_when_eligible_under_dry():
    """Under `dry` an eligible workspace returns False but is not a skip."""
    skips: dict[str, str] = {}

    def fake_cmux(*args, **_kwargs):
        return _idle_status_lines() if args[0] == "list-status" else ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        assert (
            nudge_if_idle("workspace:1", "hi", tag="t", dry=True, skips=skips) is False
        )

    assert skips == {}


def test_nudge_if_idle_records_send_failure_as_a_skip():
    skips: dict[str, str] = {}

    def fake_cmux(*args, check=True, **_kwargs):
        if args[0] == "list-status":
            return _idle_status_lines()
        if args[0] == "send" and check:
            raise RuntimeError("cmux send failed: socket gone")
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        assert nudge_if_idle("workspace:1", "hi", tag="t", skips=skips) is False

    assert skips == {"workspace:1": "send failed"}


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
        nudge_if_idle("workspace:1", "fix CI", tag="t", pref_key="acme__42")

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
        result = nudge_if_idle("workspace:1", "fix CI", tag="t", pref_key="acme__42")

    assert result is True
    assert recorded == [("acme__42",)]


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


def test_nudge_skips_muted_pr_without_touching_cmux():
    """A file-backed mute (`should_nudge` False) suppresses the nudge before any
    cmux round-trip — the mute survives daemon restarts, so it must gate ahead of
    list-status, not just before send."""
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        return _idle_status_lines()

    with (
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
        patch("cockpit.lib.nudges.should_nudge", return_value=False),
    ):
        result = nudge_if_idle("workspace:1", "fix CI", tag="t", pref_key="acme__42")

    assert result is False
    assert calls == []  # short-circuits before list-status


def test_nudge_dry_run_reports_without_sending_or_self_healing(capsys):
    """`dry=True` on an idle-eligible workspace observes only: it prints the plan,
    sends nothing, and must NOT self-heal the dropped pill (the `and not dry`
    guard — a dry run mutates nothing)."""
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        if args[0] == "list-status":
            return _native_line("Idle")  # eligible + would self-heal when not dry
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        result = nudge_if_idle("workspace:1", "fix CI", tag="t", dry=True)

    assert result is False
    assert [a for a in calls if a[0] == "send"] == []
    assert [a for a in calls if a[0] == "set-status"] == []  # no self-heal in dry
    assert "[dry]" in capsys.readouterr().out


def test_native_claude_state_parsing():
    from cockpit.lib.cmux import _native_claude_state

    assert _native_claude_state([_native_line("Needs input")]) == "Needs input"
    assert _native_claude_state([_native_line("Running")]) == "Running"
    assert _native_claude_state(["  claude_code=Idle"]) == "Idle"
    assert _native_claude_state(["idle=1", "ci=✓ ci"]) is None


# ── workspace groups (stacked PRs) ───────────────────────────────────────────


def _group_json(
    ref: str, name: str, anchor: str, members: list[str], icon: str | None = None
) -> dict:
    return {
        "ref": ref,
        "name": name,
        "anchor_workspace_ref": anchor,
        "member_workspace_refs": members,
        "custom_color": None,
        "icon_symbol": icon,
        "is_collapsed": False,
        "is_pinned": False,
        "member_count": len(members),
    }


def test_list_workspace_groups_parses_cmux_json():
    payload = json.dumps(
        {
            "groups": [
                _group_json(
                    "workspace_group:1",
                    "auth (2)",
                    "w:1",
                    ["w:1", "w:2"],
                    icon="square.stack",
                )
            ],
            "window_ref": "window:1",
        }
    )

    with patch("cockpit.lib.cmux.cmux", return_value=payload):
        groups = list_workspace_groups()

    assert len(groups) == 1
    assert groups[0].ref == "workspace_group:1"
    assert groups[0].name == "auth (2)"
    assert groups[0].anchor == "w:1"
    assert groups[0].members == ("w:1", "w:2")
    # The icon is how the reconcile tells cockpit's own stranded anchor-only
    # group from a fold the user built by hand.
    assert groups[0].icon == "square.stack"


def test_list_workspace_groups_survives_garbage():
    # Grouping is additive — a broken read means "reconcile nothing", never a
    # traceback into the tick.
    with patch("cockpit.lib.cmux.cmux", return_value="not json"):
        assert list_workspace_groups() == []


class _FakeCmux:
    """Stand-in for the cmux CLI covering group creation.

    `cmux workspace-group create` always spawns a fresh workspace to own the
    group, so the fake mirrors that: the created group is anchored on a
    workspace the caller never asked for.
    """

    def __init__(self):
        self.calls: list[tuple] = []
        self.anchor = "workspace:99"  # the anchor `workspace-group create` spawns
        self.members = ["workspace:99", "workspace:2", "workspace:1"]
        # `create_workspace_group` re-anchors onto a workspace it spawns itself,
        # so the fake has to serve `new-workspace` + `list-workspaces` or every
        # caller silently takes the respawn-failed path instead.
        self.workspaces = ["workspace:99", "workspace:2", "workspace:1"]
        self.next_spawn: str | None = "workspace:500"

    def __call__(self, *args, **_kwargs):
        self.calls.append(args)
        if args[0] == "new-workspace":
            if self.next_spawn is not None:
                self.workspaces.append(self.next_spawn)
            return ""
        if args[0] == "list-workspaces":
            return "\n".join(self.workspaces)
        if args[:2] == ("workspace-group", "create"):
            return json.dumps(
                {
                    "group": _group_json(
                        "workspace_group:5", "auth (2)", self.anchor, self.members
                    )
                }
            )
        if args[:2] == ("workspace-group", "list"):
            return json.dumps(
                {
                    "groups": [
                        _group_json(
                            "workspace_group:5", "auth (2)", self.anchor, self.members
                        )
                    ]
                }
            )
        if args[0] == "close-workspace":
            self.members = [m for m in self.members if m != args[2]]
            self.workspaces = [w for w in self.workspaces if w != args[2]]
        return ""

    def verbs(self) -> list[tuple]:
        return [a[:2] for a in self.calls if a[0] == "workspace-group"]


def test_create_workspace_group_keeps_a_dedicated_workspace_as_the_header():
    # The anchor's row IS the group header, so anchoring on a stack member
    # would swallow that member's row — a 2-PR stack showing one row under a
    # header that says (2). The anchor stays a dedicated workspace of its own
    # (which one it is changes when it is swapped for a durable one; that it is
    # never a member is the invariant).
    fake = _FakeCmux()

    with patch("cockpit.lib.cmux.cmux", side_effect=fake):
        group = create_workspace_group("auth (2)", ["workspace:1", "workspace:2"])

    assert group is not None
    assert group.anchor not in ("workspace:1", "workspace:2")
    assert {"workspace:1", "workspace:2"} <= set(group.members)


def test_create_workspace_group_spawns_the_anchor_outside_every_repo():
    # An anchor sitting inside a registered repo would be reaped as an orphan
    # workspace (`_reap_workspace_orphans`), taking the group down with it.
    fake = _FakeCmux()

    with patch("cockpit.lib.cmux.cmux", side_effect=fake):
        create_workspace_group("auth (2)", ["workspace:1", "workspace:2"])

    create = next(a for a in fake.calls if a[:2] == ("workspace-group", "create"))
    assert create[create.index("--cwd") + 1] == str(Path.home())


def test_create_workspace_group_reanchors_onto_a_workspace_with_a_live_shell():
    # `workspace-group create` spawns its anchor with no command, and a
    # command-less cmux workspace has no terminal surface at all — so cmux
    # reaps it and the fold dies silently (no dissolve runs, so nothing is
    # logged), costing a full rebuild next slow cycle. The spawned anchor is
    # therefore swapped for one that owns a live shell.
    fake = _FakeCmux()

    with patch("cockpit.lib.cmux.cmux", side_effect=fake):
        group = create_workspace_group("auth (2)", ["workspace:1", "workspace:2"])

    assert group is not None
    assert group.anchor == "workspace:500"
    assert fake.verbs() == [
        ("workspace-group", "create"),
        ("workspace-group", "set-icon"),
        ("workspace-group", "add"),
        ("workspace-group", "set-anchor"),
    ]
    # Carrying a command is the whole point — that is what gives the workspace a
    # terminal — and $HOME keeps it outside every registered repo.
    spawn = next(a for a in fake.calls if a[0] == "new-workspace")
    assert spawn[spawn.index("--command") + 1] == cmux_mod.ANCHOR_KEEPALIVE_COMMAND
    assert spawn[spawn.index("--cwd") + 1] == str(Path.home())


def test_create_workspace_group_closes_the_husk_anchor_through_the_self_close_funnel():
    # A raw `close-workspace` would leave the resulting `workspace.closed` event
    # looking like the user clicking cmux's ✕, which routes into teardown.
    fake = _FakeCmux()

    with (
        patch("cockpit.lib.cmux.cmux", side_effect=fake),
        patch("cockpit.lib.cmux.cmux_close_workspace_best_effort") as close,
    ):
        create_workspace_group("auth (2)", ["workspace:1", "workspace:2"])

    close.assert_called_once_with("workspace:99")


def test_create_workspace_group_keeps_the_husk_anchor_when_the_respawn_fails():
    # Fails open: a fold that may churn beats no fold at all.
    fake = _FakeCmux()
    fake.next_spawn = None

    with patch("cockpit.lib.cmux.cmux", side_effect=fake):
        group = create_workspace_group("auth (2)", ["workspace:1", "workspace:2"])

    assert group is not None
    assert group.anchor == "workspace:99"
    assert fake.verbs() == [
        ("workspace-group", "create"),
        ("workspace-group", "set-icon"),
    ]
    assert [a for a in fake.calls if a[0] == "close-workspace"] == []


def test_create_workspace_group_passes_refs_leaf_first():
    # cmux prepends each --from entry, so the root has to go last to land on top.
    fake = _FakeCmux()

    with patch("cockpit.lib.cmux.cmux", side_effect=fake):
        create_workspace_group("auth (2)", ["workspace:1", "workspace:2"])

    create = next(a for a in fake.calls if a[:2] == ("workspace-group", "create"))
    assert "workspace:2,workspace:1" in create


def test_create_workspace_group_returns_none_on_malformed_json():
    with patch("cockpit.lib.cmux.cmux", return_value="not json"):
        assert (
            create_workspace_group("auth (2)", ["workspace:1", "workspace:2"]) is None
        )


def test_create_workspace_group_accepts_a_single_member():
    # cmux drops a group only when its *anchor* is the last workspace, and the
    # anchor is dedicated — so one member plus the header is a real group, which
    # is what lets a lone coworker review fold instead of sitting loose.
    with patch(
        "cockpit.lib.cmux.cmux",
        return_value=json.dumps(
            {
                "group": {
                    "ref": "workspace_group:1",
                    "name": "acme reviews (1)",
                    "anchor_workspace_ref": "workspace:9",
                    "member_workspace_refs": ["workspace:9", "workspace:1"],
                }
            }
        ),
    ):
        group = create_workspace_group("acme reviews (1)", ["workspace:1"])

    assert group is not None
    assert group.ref == "workspace_group:1"


def test_create_workspace_group_leaves_the_group_expanded_by_default():
    # A stack is the live queue — folding it shut would hide my own PR rows.
    fake = _FakeCmux()

    with patch("cockpit.lib.cmux.cmux", side_effect=fake):
        create_workspace_group("auth (2)", ["workspace:1", "workspace:2"])

    assert ("workspace-group", "collapse") not in fake.verbs()


def test_create_workspace_group_collapses_when_asked():
    # cmux creates every group expanded (`is_collapsed: false`), so a trailing
    # pile would pop open on the tick that built it. Collapse lands after the
    # icon, on the created group's own ref.
    fake = _FakeCmux()

    with patch("cockpit.lib.cmux.cmux", side_effect=fake):
        group = create_workspace_group(
            "acme reviews (1)", ["workspace:1"], collapsed=True
        )

    assert group is not None
    assert fake.verbs() == [
        ("workspace-group", "create"),
        ("workspace-group", "set-icon"),
        ("workspace-group", "collapse"),
        ("workspace-group", "add"),
        ("workspace-group", "set-anchor"),
    ]
    collapse = next(a for a in fake.calls if a[:2] == ("workspace-group", "collapse"))
    assert collapse[2] == group.ref


def test_create_workspace_group_does_not_collapse_a_group_it_failed_to_create():
    with patch("cockpit.lib.cmux.cmux", return_value="not json") as cmux_mock:
        assert (
            create_workspace_group("acme reviews (1)", ["workspace:1"], collapsed=True)
            is None
        )

    assert [c for c in cmux_mock.call_args_list if "collapse" in c.args] == []


def test_create_workspace_group_refuses_an_empty_member_list():
    with patch("cockpit.lib.cmux.cmux") as cmux_mock:
        assert create_workspace_group("acme reviews (0)", []) is None

    cmux_mock.assert_not_called()


def test_move_workspace_group_to_end_clamps_past_the_sidebar():
    with patch("cockpit.lib.cmux.cmux") as cmux_mock:
        move_workspace_group_to_end("workspace_group:1")

    cmux_mock.assert_called_once_with(
        "workspace-group",
        "move",
        "workspace_group:1",
        "--to-index",
        "9999",
        check=False,
    )


def test_group_verbs_noop_on_limux():
    # workspace-group is cmux-only; limux users silently skip stack folding.
    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="limux"),
        patch("cockpit.lib.cmux.run") as run_mock,
    ):
        assert list_workspace_groups() == []
        add_to_workspace_group("workspace_group:1", "workspace:2")
        remove_from_workspace_group("workspace:2")
        rename_workspace_group("workspace_group:1", "auth (2)")
        move_workspace_group_to_end("workspace_group:1")
        ungroup_workspaces("workspace_group:1")

    run_mock.assert_not_called()


# ── send-text normalization (every newline is an Enter) ──────────────────────


def test_one_line_collapses_real_newlines():
    assert one_line("first\nsecond") == "first second"
    assert one_line("a\r\nb") == "a b"


def test_one_line_collapses_literal_backslash_escapes():
    r"""The two-character `\n` is what `cmux send` documents as Enter, so it is
    just as dangerous as a real newline — and far likelier, since it survives
    a shell single-quote (`cockpit broadcast 'fix the \n handling'`)."""
    assert one_line(r"fix the \n handling") == "fix the handling"
    assert one_line(r"a\rb") == "a b"
    assert one_line(r"a\tb") == "a b"


def test_one_line_leaves_a_plain_message_untouched():
    assert one_line("fix CI") == "fix CI"
    assert one_line("/compact") == "/compact"


def test_one_line_folds_runs_of_whitespace_and_strips():
    assert one_line("  a   b  ") == "a b"
    assert one_line("") == ""


def test_nudge_if_idle_sends_multiline_text_as_one_line():
    """A multi-line message must reach `cmux send` as ONE argv with no newline:
    cmux synthesizes keypresses (not a bracketed paste), so each newline would
    arrive as Enter and submit a truncated fragment as its own prompt."""
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        if args[0] == "list-status":
            return _idle_status_lines()
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        result = nudge_if_idle("workspace:1", "rebase onto main\nthen force-push")

    assert result is True
    sent = [args for args in calls if args[0] == "send"]
    assert len(sent) == 1
    assert sent[0][3] == "rebase onto main then force-push"
    assert "\n" not in sent[0][3]


def test_nudge_if_idle_neutralizes_literal_backslash_n():
    r"""Regression: `cockpit broadcast 'fix the \n handling'` used to submit
    `fix the ` to every idle session and `handling` as a second prompt."""
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        if args[0] == "list-status":
            return _idle_status_lines()
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        nudge_if_idle("workspace:1", r"fix the \n handling")

    sent = [args for args in calls if args[0] == "send"]
    assert sent[0][3] == "fix the handling"


def test_nudge_dry_run_reports_the_normalized_text(capsys):
    """`--dry` must show what would actually be delivered, not the raw input —
    so the normalize happens before the print."""

    def fake_cmux(*args, **_kwargs):
        if args[0] == "list-status":
            return _idle_status_lines()
        return ""

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        nudge_if_idle("workspace:1", "one\ntwo", tag="t", dry=True)

    out = capsys.readouterr().out
    assert "one two" in out


# ── rest_skip_reason (the gate's own verdict, for display callers) ───────────


def test_rest_skip_reason_is_none_when_a_send_would_land():
    with patch("cockpit.lib.cmux.cmux", return_value=_idle_status_lines()):
        assert rest_skip_reason("workspace:1") is None


def test_rest_skip_reason_never_sends():
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        return _idle_status_lines()

    with patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux):
        rest_skip_reason("workspace:1")

    assert [a[0] for a in calls] == ["list-status"]


def test_rest_skip_reason_agrees_with_the_gate_on_every_case():
    """The whole point: a display caller must not be able to disagree with the
    decision. Same inputs through both paths, same verdict."""
    cases = (
        _native_line("Running") + "\nidle=1",
        _idle_status_lines(),
        _native_line("Idle"),
        _native_line("Needs input"),
        f"idle=1\n{PARKED_KEY}=1",
    )
    for lines in cases:
        with patch(
            "cockpit.lib.cmux.cmux",
            side_effect=lambda *a, _l=lines, **k: (_l if a[0] == "list-status" else ""),
        ):
            would_land = rest_skip_reason("workspace:1") is None
            fired = nudge_if_idle("workspace:1", "m")
        assert would_land is fired, lines


def test_nudge_gate_order_unchanged_by_the_shared_read():
    """Regression guard on the refactor: the four documented outcomes must be
    byte-for-byte the same decisions as before the gate was factored out."""
    cases = [
        (_native_line("Running") + "\nidle=1", False),  # running beats idle pill
        (_idle_status_lines(), True),  # idle pill fires
        (_native_line("Idle"), True),  # native Idle fires (+ self-heal)
        (_native_line("Needs input"), False),  # ambiguous → never
        (f"idle=1\n{PARKED_KEY}=1", False),  # parked beats idle pill
    ]
    for lines, expected in cases:
        with patch(
            "cockpit.lib.cmux.cmux",
            # `_l=lines` binds per iteration — a bare closure over the loop
            # variable would read the last case for every case (ruff B023).
            side_effect=lambda *a, _l=lines, **k: (_l if a[0] == "list-status" else ""),
        ):
            assert nudge_if_idle("workspace:1", "m") is expected, lines


def _reassert_calls(
    statuses: dict[str, str], screens: dict[str, str] | None = None
) -> tuple[list[str], list[tuple]]:
    """Drive `reassert_idle_pills` over `statuses` (ref -> list-status text)
    and `screens` (ref -> read-screen text, consulted only for the
    no-native-state fallback; defaults to "" — no screen evidence), returning
    the healed refs and every non-read cmux call it made. `is_cmux` is pinned
    True so the fallback's own gate doesn't depend on the test environment's
    real config."""
    writes: list[tuple] = []
    screens = screens or {}

    def fake_cmux(*args, **_kwargs):
        if args[0] == "list-status":
            return statuses[args[2]]
        if args[0] == "read-screen":
            return screens.get(args[2], "")
        writes.append(args)
        return ""

    with (
        patch("cockpit.lib.cmux.cmux", side_effect=fake_cmux),
        patch("cockpit.lib.cmux.tool.is_cmux", return_value=True),
    ):
        healed = reassert_idle_pills(list(statuses))
    return healed, writes


def test_reassert_writes_the_pill_when_native_idle_and_pill_missing():
    healed, writes = _reassert_calls({"workspace:1": _native_line("Idle")})
    assert healed == ["workspace:1"]
    assert any("set-status" in a for a in writes[0]), writes


@pytest.mark.parametrize(
    "status",
    [
        _native_line("Running"),
        _native_line("Needs input"),
        # Already pilled — nothing to heal, and re-writing would be pure churn.
        "idle=1\n" + _native_line("Idle"),
        # No native state, and (with no screens override) no screen evidence
        # either — the fallback refuses just as the ordinary path does.
        "",
    ],
)
def test_reassert_writes_nothing_without_an_unambiguous_idle(status):
    healed, writes = _reassert_calls({"workspace:1": status})
    assert healed == []
    assert writes == []


_IDLE_SCREEN = "some output\n─────\n❯  \n─────\nbranch\n-- INSERT -- auto mode on"


def test_reassert_heals_no_native_state_when_the_screen_confirms_idle():
    """The one case the `Idle`-only path can't reach: cmux never registered
    `claude_code=` for this ref at all. `_screen_signals_idle` is the
    documented fallback for exactly this gap."""
    healed, writes = _reassert_calls(
        {"workspace:1": ""}, screens={"workspace:1": _IDLE_SCREEN}
    )
    assert healed == ["workspace:1"]
    assert any("set-status" in a for a in writes[0]), writes


@pytest.mark.parametrize(
    "screen",
    [
        "",  # read failed / empty
        "─────\n❯  \n─────\nbranch\nno insert-mode marker here",  # missing indicator
        "─────\n❯ half-typed text\n─────\n-- INSERT --",  # not an empty prompt
        _IDLE_SCREEN + "\n1. Yes\n2. No\nEnter to select · Esc to cancel",
    ],
)
def test_reassert_refuses_no_native_state_on_inconclusive_or_pending_screen(screen):
    """Fails closed: a read failure, a missing indicator, a non-empty prompt,
    or ANY pending-choice marker (even alongside otherwise-idle-looking text)
    all refuse — a false positive here would mean typing into a live y/n or
    AskUserQuestion prompt, the exact thing the whole gate exists to prevent."""
    healed, writes = _reassert_calls(
        {"workspace:1": ""}, screens={"workspace:1": screen}
    )
    assert healed == []
    assert writes == []


def test_reassert_never_clears_a_pill():
    """It is a one-way door by design: a stale `idle=` is already caught by the
    gate's `Running` guard, but a wrongly-cleared one silences a live session."""
    _, writes = _reassert_calls({"workspace:1": "idle=1\n" + _native_line("Running")})
    assert not any("clear-status" in a for a in writes), writes


def test_reassert_heals_only_the_eligible_refs_in_a_mixed_fleet():
    healed, _ = _reassert_calls(
        {
            "workspace:1": _native_line("Idle"),
            "workspace:2": _native_line("Running"),
            "workspace:3": "",
            "workspace:4": _native_line("Idle"),
        }
    )
    assert sorted(healed) == ["workspace:1", "workspace:4"]


def test_screen_signals_idle_is_cmux_only():
    """Never issues (or trusts) a `read-screen` under limux — the pill it
    would feed is a cmux-only no-op there anyway."""
    from cockpit.lib.cmux import _screen_signals_idle

    with (
        patch("cockpit.lib.cmux.tool.is_cmux", return_value=False),
        patch("cockpit.lib.cmux.cmux") as m,
    ):
        assert _screen_signals_idle("workspace:1") is False
    m.assert_not_called()


def test_reassert_on_empty_fleet_makes_no_cmux_calls():
    with patch("cockpit.lib.cmux.cmux") as m:
        assert reassert_idle_pills([]) == []
    m.assert_not_called()


# --- render_diff -----------------------------------------------------------
#
# The one `cmux diff` invocation, shared by the TUI's `d` and `cockpit diff`.
# The two callers diverge on exactly two inputs and in opposite directions, so
# that asymmetry is what these pin.


def _render(**kw):
    """Call `render_diff` against a stubbed cmux, returning `(argv, env, rc_msg)`."""
    seen: dict = {}

    class _Proc:
        returncode = kw.pop("_rc", 0)
        stderr = kw.pop("_stderr", "")

    def fake_run(cmd, **rkw):
        seen["cmd"] = cmd
        seen["env"] = rkw.get("env")
        seen["input"] = rkw.get("input")
        seen["cwd"] = rkw.get("cwd")
        return _Proc()

    with (
        patch("cockpit.lib.tool.resolve_tool", return_value="cmux"),
        patch("cockpit.lib.cmux.shutil.which", return_value="/usr/bin/cmux"),
        patch("cockpit.lib.cmux.subprocess.run", fake_run),
    ):
        msg = cmux_mod.render_diff(**kw)
    return seen, msg


def test_render_diff_never_touches_the_environment(monkeypatch):
    """It has one caller and that caller runs INSIDE the workspace it targets,
    so cmux's own `$CMUX_WORKSPACE_ID` / `$CMUX_SURFACE_ID` defaults are already
    right. The daemon-side `d` key needed the opposite of both — it had to name
    a workspace and strip the stale surface — and was removed rather than
    parameterised back in here."""
    monkeypatch.setenv("CMUX_SURFACE_ID", "surface:mine")
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "workspace:mine")
    seen, msg = _render(patch="diff", cwd="/repo", title="t")
    assert msg == ""
    assert seen["env"] is None, "the child must inherit, not be handed a copy"
    assert "--workspace" not in seen["cmd"]
    assert "--surface" not in seen["cmd"]


def test_render_diff_pipes_a_patch_and_sets_both_cwd_inputs():
    """`--cwd` AND the process cwd, because cmux keys the diff-comment store by
    repo root and which of the two it reads for a piped patch is undocumented."""
    seen, _ = _render(patch="the patch", cwd="/repo/wt", title="t")
    assert seen["input"] == "the patch"
    assert seen["cwd"] == "/repo/wt"
    assert seen["cmd"][seen["cmd"].index("--cwd") + 1] == "/repo/wt"
    assert "-" in seen["cmd"] and "--source" not in seen["cmd"]


def test_render_diff_uses_a_source_instead_of_stdin():
    seen, _ = _render(source="branch", base="origin/stage", cwd="/repo", title="t")
    assert seen["input"] is None
    assert seen["cmd"][seen["cmd"].index("--source") + 1] == "branch"
    assert seen["cmd"][seen["cmd"].index("--base") + 1] == "origin/stage"


def test_render_diff_is_always_unified():
    """Split columns overprint each other in a pane cut beside the dashboard."""
    seen, _ = _render(patch="d", cwd="/repo", title="t")
    assert seen["cmd"][seen["cmd"].index("--layout") + 1] == "unified"


def test_render_diff_needs_exactly_one_of_patch_or_source():
    with pytest.raises(ValueError):
        cmux_mod.render_diff(cwd="/repo", title="t")
    with pytest.raises(ValueError):
        cmux_mod.render_diff(patch="d", source="branch", cwd="/repo", title="t")


def test_render_diff_names_the_browser_fix():
    """The viewer is a browser surface and the browser is a runtime toggle, so
    this failure gets named precisely rather than dumped as raw stderr."""
    _, msg = _render(patch="d", cwd="/r", title="t", _rc=1, _stderr="browser_disabled")
    assert "cmux enable-browser" in msg


def test_render_diff_reports_other_failures_verbatim():
    _, msg = _render(patch="d", cwd="/r", title="t", _rc=1, _stderr="kaboom")
    assert "kaboom" in msg


def test_render_diff_is_inert_without_cmux():
    """`diff` is in `_CMUX_ONLY_VERBS`, so limux and `tool: none` degrade to a
    message instead of shelling out — which is what makes `dev.sh` safe here."""
    with patch("cockpit.lib.tool.resolve_tool", return_value="none"):
        msg = cmux_mod.render_diff(patch="d", cwd="/r", title="t")
    assert "requires cmux" in msg
