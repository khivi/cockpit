"""Pill decisions + consumer round-trips.

`decide_pills` is the single source of truth; cmux and footer each consume
its output via their own kind-to-styling maps. These tests pin both the
decisions and the renderer mappings.
"""

from __future__ import annotations

from pathlib import Path


from lib.cmux import status_pills
from lib.footer import _legacy_pr_segment, _pr_segment
from lib.gh import PR
from lib.git import Worktree
from lib.pills import decide_pills


def _pr(**overrides) -> PR:
    base = dict(
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


# ── decide_pills ────────────────────────────────────────────────────────────


def test_clean_open_pr_emits_no_pills():
    assert decide_pills(_pr(), _wt()) == []


def test_ci_failed_carries_phase():
    pills = decide_pills(_pr(ci="failed:lint"), _wt())
    assert pills == [{"kind": "ci_failed", "phase": "lint"}]


def test_ci_failed_without_phase_marker():
    # `ci` is "failed" with no `:phase`; phase becomes empty string.
    pills = decide_pills(_pr(ci="failed"), _wt())
    assert pills == [{"kind": "ci_failed", "phase": ""}]


def test_ci_pending():
    assert decide_pills(_pr(ci="pending"), _wt()) == [{"kind": "ci_pending"}]


def test_unaddressed_supersedes_changes_requested():
    pills = decide_pills(_pr(unaddressed=3, review_decision="CHANGES_REQUESTED"), _wt())
    kinds = [p["kind"] for p in pills]
    assert "unaddressed" in kinds
    assert "changes_requested" not in kinds


def test_changes_requested_alone():
    pills = decide_pills(_pr(review_decision="CHANGES_REQUESTED"), _wt())
    assert pills == [{"kind": "changes_requested"}]


def test_conflict_pill():
    pills = decide_pills(_pr(mergeable="CONFLICTING"), _wt())
    assert pills == [{"kind": "conflict"}]


def test_draft_and_approved_coexist():
    pills = decide_pills(_pr(is_draft=True, review_decision="APPROVED"), _wt())
    kinds = [p["kind"] for p in pills]
    assert kinds == ["draft", "approved"]


def test_state_pill_only_for_non_open():
    assert decide_pills(_pr(state="OPEN"), _wt()) == []
    assert decide_pills(_pr(state="MERGED"), _wt()) == [
        {"kind": "state", "state": "MERGED"}
    ]
    assert decide_pills(_pr(state="CLOSED"), _wt()) == [
        {"kind": "state", "state": "CLOSED"}
    ]


def test_worktree_pills_independent_of_pr():
    pills = decide_pills(_pr(), _wt(rebasing=True, dirty=4))
    assert pills == [
        {"kind": "rebase"},
        {"kind": "wip", "count": 4},
    ]


def test_wip_dropped_when_no_worktree():
    # PR exists but worktree is unknown (e.g. external repo): no wip pill.
    pills = decide_pills(_pr(ci="failed:test"), None)
    kinds = [p["kind"] for p in pills]
    assert "wip" not in kinds
    assert "ci_failed" in kinds


def test_full_house_canonical_order():
    pills = decide_pills(
        _pr(
            is_draft=True,
            review_decision="APPROVED",
            mergeable="CONFLICTING",
            ci="failed:tests",
            unaddressed=2,
            state="OPEN",
        ),
        _wt(merging=True, dirty=3),
    )
    assert [p["kind"] for p in pills] == [
        "merge",
        "wip",
        "ci_failed",
        "unaddressed",
        "conflict",
        "draft",
        "approved",
    ]


# ── cmux mapper ─────────────────────────────────────────────────────────────


def test_cmux_status_pills_matches_decisions():
    out = status_pills(_pr(ci="failed:lint", unaddressed=2), _wt(dirty=1))
    assert out == [
        ("wip", "✏️ 1 dirty", "#ff9500"),
        ("ci", "❌ ci:lint", "#eb445a"),
        ("comments", "💬 2 unaddressed", "#eb445a"),
    ]


def test_cmux_drops_state_pill():
    out = status_pills(_pr(state="MERGED"), _wt())
    assert out == []


def test_cmux_conflict_emits_merge_key():
    out = status_pills(_pr(mergeable="CONFLICTING"), _wt())
    assert out == [("merge", "⚠️ conflict", "#ff9500")]


# ── footer mapper ───────────────────────────────────────────────────────────


def test_footer_pr_segment_renders_pills_array(monkeypatch):
    payload = {
        "number": 42,
        "branch": "khivi/feature",
        "pills": [
            {"kind": "wip", "count": 3},
            {"kind": "ci_failed", "phase": "lint"},
            {"kind": "approved"},
        ],
    }
    monkeypatch.setattr("lib.footer.find_pr_payload", lambda b: payload)
    assert _pr_segment("khivi/feature") == "#42 khivi/feature · ✏️ 3 · ✗ lint · approved"


def test_footer_pr_segment_empty_pills_array(monkeypatch):
    payload = {"number": 7, "branch": "khivi/feature", "pills": []}
    monkeypatch.setattr("lib.footer.find_pr_payload", lambda b: payload)
    assert _pr_segment("khivi/feature") == "#7 khivi/feature"


def test_footer_no_pr_fallback(monkeypatch):
    monkeypatch.setattr("lib.footer.find_pr_payload", lambda b: None)
    assert _pr_segment("khivi/feature") == "khivi/feature · no PR"


def test_footer_renders_state_pill(monkeypatch):
    payload = {
        "number": 9,
        "branch": "khivi/old",
        "pills": [{"kind": "state", "state": "MERGED"}],
    }
    monkeypatch.setattr("lib.footer.find_pr_payload", lambda b: payload)
    assert _pr_segment("khivi/old") == "#9 khivi/old · merged"


def test_footer_skips_unknown_kind(monkeypatch):
    payload = {
        "number": 1,
        "branch": "b",
        "pills": [{"kind": "future_kind"}, {"kind": "approved"}],
    }
    monkeypatch.setattr("lib.footer.find_pr_payload", lambda b: payload)
    assert _pr_segment("b") == "#1 b · approved"


def test_footer_legacy_fallback_when_pills_missing(monkeypatch):
    """Pre-0.3.0 cache files have no `pills` — render from raw fields once."""
    payload = {
        "number": 5,
        "branch": "khivi/feature",
        "ci": "failed:test",
        "state": "OPEN",
        "isDraft": False,
        "review": "CHANGES_REQUESTED",
    }
    monkeypatch.setattr("lib.footer.find_pr_payload", lambda b: payload)
    assert _pr_segment("khivi/feature") == "#5 khivi/feature · ✗ · changes-requested"


def test_legacy_pr_segment_merged_state():
    payload = {
        "number": 11,
        "ci": "passed",
        "state": "MERGED",
        "isDraft": False,
        "review": "APPROVED",
    }
    assert _legacy_pr_segment("khivi/old", payload) == "#11 khivi/old · ✓ · merged"


# ── 5h threshold pill ───────────────────────────────────────────────────────


def test_five_hour_pill_colors():
    from lib.footer import _five_hour_pill

    assert _five_hour_pill(0) == "⌛ 5h 0%"
    assert _five_hour_pill(59.4) == "⌛ 5h 59%"
    assert _five_hour_pill(60).startswith("\033[33m")
    assert _five_hour_pill(79.9).startswith("\033[33m")
    assert _five_hour_pill(80).startswith("\033[31m")
    assert _five_hour_pill(99).startswith("\033[31m")


def test_session_pills_prepends_clock():
    from lib.footer import _session_pills

    blob = '{"model":{"display_name":"Opus"},"context_window":{"used_percentage":10,"context_window_size":200000},"rate_limits":{"five_hour":{"used_percentage":5}}}'
    pills = _session_pills(blob)
    assert pills[0].startswith("🕐 "), pills


# ── cache round-trip ────────────────────────────────────────────────────────


def test_write_pr_cache_includes_pills(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import lib.config as cockpit_config

    importlib.reload(cockpit_config)
    import lib.cache as cache_mod

    importlib.reload(cache_mod)

    pr = _pr(ci="failed:lint", review_decision="APPROVED")
    wt = _wt(dirty=2)
    payload = cache_mod.write_pr_cache("testrepo", pr, wt)

    assert "pills" in payload
    kinds = [p["kind"] for p in payload["pills"]]
    assert kinds == ["wip", "ci_failed", "approved"]

    on_disk = cache_mod.find_pr_payload("khivi/feature", repo_name="testrepo")
    assert on_disk is not None
    assert [p["kind"] for p in on_disk["pills"]] == kinds


def test_write_pr_cache_without_worktree(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import lib.config as cockpit_config

    importlib.reload(cockpit_config)
    import lib.cache as cache_mod

    importlib.reload(cache_mod)

    pr = _pr(ci="failed:lint")
    payload = cache_mod.write_pr_cache("testrepo", pr)

    assert "pills" in payload
    # Without wt, no rebase/merge/wip pills appear.
    kinds = [p["kind"] for p in payload["pills"]]
    assert "wip" not in kinds
    assert "ci_failed" in kinds
