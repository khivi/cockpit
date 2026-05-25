"""Tests for scripts/lib/starship.py — field printers consumed by cship/starship."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

import scripts.lib.claude as claude_mod
import scripts.lib.starship as starship
from scripts.lib.colors import (
    Colorizer,
    amber,
    azure,
    bold_azure,
    bold_crimson,
    bold_leaf,
    bold_orange,
    bold_ruby,
    bold_shadow,
    bold_violet,
    crimson,
    green,
    leaf,
    orange,
    red,
    shadow,
    slate,
    yellow,
)


# ── field printer: context (lib.starship) ──────────────────────────────────


def test_print_context_formats_ceiling_M(cache_dir):
    (cache_dir / "context").write_text("12 1000000")
    out = starship.print_context()
    assert slate("🧠 12%/1M") == out


def test_print_context_formats_ceiling_k(cache_dir):
    (cache_dir / "context").write_text("33 200000")
    out = starship.print_context()
    assert slate("🧠 33%/200k") == out


def test_print_context_session_scoped(cache_dir, monkeypatch):
    (cache_dir / "context-S1").write_text("7 1000000")
    monkeypatch.setenv("CSHIP_SESSION_ID", "S1")
    out = starship.print_context()
    assert "🧠 7%/1M" in out


def test_print_context_fresh_session_falls_back_to_latest(cache_dir, monkeypatch):
    """Fresh-session regression: Claude Code's first statusLine ping for
    a new session has `session_id` but no `context_window`, so
    `context-<sid>` is never written. The pill must still render by
    falling back to the most recent existing `context-*` cache."""
    (cache_dir / "context-OLD").write_text("33 200000")
    time.sleep(0.01)
    (cache_dir / "context-NEWER").write_text("55 1000000")
    monkeypatch.setenv("CSHIP_SESSION_ID", "FRESH-SID-NO-CACHE-YET")
    out = starship.print_context()
    assert "🧠 55%/1M" in out


@pytest.mark.parametrize(
    "pct,color",
    [
        (5, slate),
        (75, orange),
        (95, crimson),
        (100, bold_crimson),
    ],
    ids=[
        "tier_slate_under_70",
        "tier_amber_70_to_89",
        "tier_red_90_to_99",
        "tier_red_bold_at_100",
    ],
)
def test_print_context_tier(cache_dir, pct, color: Colorizer):
    (cache_dir / "context").write_text(f"{pct} 1000000")
    out = starship.print_context()
    assert out == color(f"🧠 {pct}%/1M")


def test_print_context_fresh_session_no_history_empty(cache_dir, monkeypatch):
    """No prior session caches exist → fall back returns empty cleanly."""
    monkeypatch.setenv("CSHIP_SESSION_ID", "FRESH-SID")
    assert starship.print_context() == ""


def test_print_context_malformed_cache_empty(cache_dir):
    (cache_dir / "context").write_text("garbage")
    assert starship.print_context() == ""


def test_print_context_zero_limit_empty(cache_dir):
    (cache_dir / "context").write_text("50 0")
    assert starship.print_context() == ""


# ── field printer: rate-limit ──────────────────────────────────────────────


def test_print_rate_limit(cache_dir):
    (cache_dir / "rate-limit-5h").write_text("8 2026-05-21T15:00:00Z")
    out = starship.print_rate_limit()
    assert out == slate("⌛ 8%/5h")


def test_print_rate_limit_missing_cache_empty(cache_dir):
    assert starship.print_rate_limit() == ""


def test_print_rate_limit_fresh_session_falls_back_to_latest(cache_dir, monkeypatch):
    """Fresh-session regression: same as context — rate_limits absent in
    Claude Code's first ping, fall back to most recent cache."""
    (cache_dir / "rate-limit-5h-OLD").write_text("3 2026-01-01T00:00:00Z")
    time.sleep(0.01)
    (cache_dir / "rate-limit-5h-NEWER").write_text("17 2026-05-21T15:00:00Z")
    monkeypatch.setenv("CSHIP_SESSION_ID", "FRESH-SID-NO-CACHE-YET")
    out = starship.print_rate_limit()
    assert "⌛ 17%/5h" in out


@pytest.mark.parametrize(
    "pct,color",
    [
        (5, slate),
        (72, orange),
        (95, crimson),
        (100, bold_crimson),
    ],
    ids=[
        "tier_slate_under_70",
        "tier_amber_70_to_89",
        "tier_red_90_to_99",
        "tier_red_bold_at_100",
    ],
)
def test_print_rate_limit_tier(cache_dir, pct, color: Colorizer):
    (cache_dir / "rate-limit-5h").write_text(f"{pct} 2026-05-21T15:00:00Z")
    out = starship.print_rate_limit()
    assert out == color(f"⌛ {pct}%/5h")


# ── field printer: linear ──────────────────────────────────────────────────


def test_print_linear_extracts_ticket(_clean_git_env, tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, branch="khivi/PRO-123-fix")
    monkeypatch.chdir(repo)
    assert starship.print_linear() == "PRO-123"


def test_print_linear_no_ticket(_clean_git_env, tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, branch="khivi/cleanup")
    monkeypatch.chdir(repo)
    assert starship.print_linear() == ""


# ── PR cache reads ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("APPROVED", bold_leaf("✅ APPROVED")),
        ("DRAFT", bold_shadow("📝 DRAFT")),
        ("OPEN", bold_azure("🔵 OPEN")),
        ("REVIEW_REQUIRED", bold_orange("👀 REVIEW_REQUIRED")),
        ("CHANGES_REQUESTED", bold_crimson("💬 CHANGES_REQUESTED")),
        ("MERGED", bold_violet("🟣 MERGED")),
        ("CLOSED", bold_ruby("⛔ CLOSED")),
        ("WHATEVER", "WHATEVER"),
    ],
    ids=[
        "fresh_cache",
        "draft",
        "open",
        "review_required",
        "changes_requested",
        "merged",
        "closed",
        "unknown_passes_through",
    ],
)
def test_print_pr_state(cache_dir, value, expected):
    (cache_dir / "pr-state-khivi-foo").write_text(value)
    assert starship.print_pr_state("khivi/foo") == expected


def test_print_pr_num_formats_hash(cache_dir):
    (cache_dir / "pr-num-khivi-foo").write_text("42")
    assert starship.print_pr_num("khivi/foo") == "🔗 #42"


def test_print_pr_num_zero_sentinel_empty(cache_dir):
    (cache_dir / "pr-num-khivi-foo").write_text("0")
    assert starship.print_pr_num("khivi/foo") == ""


def test_print_pr_title(cache_dir):
    (cache_dir / "pr-title-khivi-foo").write_text("My PR")
    assert starship.print_pr_title("khivi/foo") == "📄 My PR"


def test_print_pr_title_empty_returns_empty(cache_dir):
    (cache_dir / "pr-title-khivi-foo").write_text("")
    assert starship.print_pr_title("khivi/foo") == ""


def test_print_pr_muted_empty_when_not_muted(cache_dir):
    assert starship.print_pr_muted("khivi/foo") == ""


def test_print_pr_muted_full(cache_dir):
    (cache_dir / "pr-muted-khivi-foo").write_text("all")
    assert starship.print_pr_muted("khivi/foo") == yellow("🔇 muted")


def test_print_pr_muted_partial_renders_categories(cache_dir):
    (cache_dir / "pr-muted-khivi-foo").write_text("ci,comments")
    assert starship.print_pr_muted("khivi/foo") == yellow("🔇 muted: ci+comments")


@pytest.mark.parametrize(
    "glyph,expected",
    [
        ("✓", green("✓")),
        ("✗", red("✗")),
        ("•", yellow("•")),
    ],
    ids=["fresh_pass", "fresh_fail", "fresh_pending"],
)
def test_print_pr_checks(cache_dir, glyph, expected):
    (cache_dir / "pr-checks-khivi-foo").write_text(glyph)
    assert starship.print_pr_checks("khivi/foo") == expected


def test_print_pr_state_stale_triggers_refresh(cache_dir):
    cache = cache_dir / "pr-state-khivi-foo"
    cache.write_text("OPEN")
    # Age the file past the 60s TTL.
    import os

    old = time.time() - 3600
    os.utime(cache, (old, old))
    with patch.object(starship, "_spawn_background_refresh") as spawn:
        out = starship.print_pr_state("khivi/foo")
    assert "OPEN" in out  # stale payload still returned (with ANSI)
    spawn.assert_called_once_with("pr-state")


# ── session-time (lib.starship) ────────────────────────────────────────────


def test_print_session_time_no_transcript_cache(cache_dir):
    assert starship.print_session_time() == ""


def test_print_session_time_missing_transcript_file(cache_dir):
    (cache_dir / "transcript-path").write_text("/nope/missing.jsonl")
    assert starship.print_session_time() == ""


def test_print_session_time_formats_minutes(cache_dir, tmp_path):
    transcript = tmp_path / "t.jsonl"
    # Use a timestamp two hours ago in UTC; the parser strips Z + treats as UTC.
    past = time.gmtime(time.time() - 2 * 3600 - 5 * 60)
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", past) + "Z"
    transcript.write_text(json.dumps({"timestamp": iso}) + "\n")
    (cache_dir / "transcript-path").write_text(str(transcript))
    out = starship.print_session_time()
    # Allow small drift in case the test runner is slow; just check shape.
    assert out.endswith("m") and "h " in out


def test_print_session_time_skips_under_10s(cache_dir, tmp_path):
    transcript = tmp_path / "t.jsonl"
    past = time.gmtime(time.time() - 2)
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", past) + "Z"
    transcript.write_text(json.dumps({"timestamp": iso}) + "\n")
    (cache_dir / "transcript-path").write_text(str(transcript))
    assert starship.print_session_time() == ""


# ── field printer: model ───────────────────────────────────────────────────


def test_print_model_reads_session_cache(cache_dir, monkeypatch):
    (cache_dir / "model-S").write_text("Opus 4.7")
    monkeypatch.setenv("CSHIP_SESSION_ID", "S")
    assert starship.print_model() == "Opus 4.7"


def test_print_model_fresh_session_falls_back(cache_dir, monkeypatch):
    (cache_dir / "model-OLD").write_text("Sonnet 4.6")
    time.sleep(0.01)
    (cache_dir / "model-NEWER").write_text("Opus 4.7")
    monkeypatch.setenv("CSHIP_SESSION_ID", "FRESH")
    assert starship.print_model() == "Opus 4.7"


def test_print_model_missing_empty(cache_dir):
    assert starship.print_model() == ""


# ── field printer: permission_mode ─────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("default", ""),
        ("plan", "✎ plan"),
        ("acceptEdits", "✎ accept-edits"),
        ("bypassPermissions", "✎ bypass"),
        ("zaphod", ""),
    ],
    ids=["default_hidden", "plan", "accept_edits", "bypass", "unknown_value_hidden"],
)
def test_print_permission_mode(cache_dir, value, expected):
    (cache_dir / "permission-mode").write_text(value)
    assert starship.print_permission_mode() == expected


# ── field printers: branch_identity + worktree_status ──────────────────────


from tests.fixtures import make_git_repo as _make_repo  # noqa: E402


def test_branch_identity_clean(_clean_git_env, tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, branch="main")
    monkeypatch.chdir(repo)
    assert starship.print_branch_identity() == slate("⎇ main")


def test_branch_identity_not_in_repo(_clean_git_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert starship.print_branch_identity() == ""


def test_branch_identity_ahead_origin(_clean_git_env, tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, ahead=3)
    monkeypatch.chdir(repo)
    out = starship.print_branch_identity()
    assert slate("⎇ feature") in out
    assert azure("↑3") in out


def test_worktree_status_clean(_clean_git_env, tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, branch="main")
    monkeypatch.chdir(repo)
    assert starship.print_worktree_status() == ""


def test_worktree_status_not_in_repo(_clean_git_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert starship.print_worktree_status() == ""


def test_worktree_status_leads_with_separator_when_non_empty(
    _clean_git_env, tmp_path, monkeypatch
):
    repo = _make_repo(tmp_path, status=(1, 0, 0))
    monkeypatch.chdir(repo)
    out = starship.print_worktree_status()
    assert out.startswith(slate(starship.POWERLINE_BRANCH))
    assert leaf("●1") in out


@pytest.mark.parametrize(
    "behind,status,expected_fragment",
    [
        (2, (0, 0, 0), orange("↓2")),
        (0, (1, 0, 0), leaf("●1")),
        (0, (0, 2, 0), amber("✎2")),
        (0, (0, 0, 4), shadow("✚4")),
    ],
    ids=["behind_only", "staged_only", "unstaged_only", "untracked_only"],
)
def test_worktree_status_segments(
    _clean_git_env, tmp_path, monkeypatch, behind, status, expected_fragment
):
    repo = _make_repo(tmp_path, behind=behind, status=status)
    monkeypatch.chdir(repo)
    assert expected_fragment in starship.print_worktree_status()


def test_worktree_status_all_segments(_clean_git_env, tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, behind=1, status=(1, 1, 1))
    monkeypatch.chdir(repo)
    out = starship.print_worktree_status()
    for frag in (orange("↓1"), leaf("●1"), amber("✎1"), shadow("✚1")):
        assert frag in out


def test_worktree_status_real_repo_dirty_and_untracked(
    _clean_git_env, tmp_path, monkeypatch
):
    repo = _make_repo(tmp_path, branch="main")
    (repo / "f").write_text("y")
    (repo / "new").write_text("z")
    monkeypatch.chdir(repo)
    out = starship.print_worktree_status()
    assert "✎1" in out
    assert "✚1" in out


# ── base-distance (↻N) on worktree_status ──────────────────────────────────


@pytest.mark.parametrize(
    "setup,check",
    [
        (
            lambda p, now: p.write_text(f"7 {now}"),
            lambda out: orange("↻7") in out and "ago" not in out,
        ),
        (
            lambda p, now: p.write_text(f"4 {now - 2 * 3600}"),
            lambda out: shadow("↻4 (2h ago)") in out,
        ),
        (
            lambda p, now: p.write_text(f"4 {now - 8 * 3600}"),
            lambda out: "↻" not in out,
        ),
        (lambda p, now: p.write_text(f"0 {now}"), lambda out: "↻" not in out),
        (lambda p, now: p.write_text(""), lambda out: "↻" not in out),
        (lambda p, now: None, lambda out: "↻" not in out),
    ],
    ids=["fresh", "aging", "too_stale", "zero", "empty_payload", "no_cache"],
)
def test_worktree_status_base_distance(
    cache_dir, _clean_git_env, tmp_path, monkeypatch, setup, check
):
    repo = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    setup(cache_dir / "base-distance-feature", int(time.time()))
    assert check(starship.print_worktree_status())


def test_worktree_status_base_distance_garbage_hidden(
    cache_dir, _clean_git_env, tmp_path, monkeypatch
):
    repo = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    (cache_dir / "base-distance-feature").write_text("not numbers")
    assert "↻" not in starship.print_worktree_status()


def test_worktree_status_base_distance_slash_branch_key(
    cache_dir, _clean_git_env, tmp_path, monkeypatch
):
    """branch_cache slug-escapes `/` to `-`; verify the cache file path."""
    repo = _make_repo(tmp_path, branch="khivi/master/foo")
    monkeypatch.chdir(repo)
    now = int(time.time())
    (cache_dir / "base-distance-khivi-master-foo").write_text(f"3 {now}")
    assert orange("↻3") in starship.print_worktree_status()


# ── base-ahead (↗N) on branch_identity ─────────────────────────────────────


@pytest.mark.parametrize(
    "setup,check",
    [
        (
            lambda p, now: p.write_text(f"7 {now}"),
            lambda out: azure("↗7") in out and "ago" not in out,
        ),
        (
            lambda p, now: p.write_text(f"4 {now - 2 * 3600}"),
            lambda out: shadow("↗4 (2h ago)") in out,
        ),
        (
            lambda p, now: p.write_text(f"4 {now - 8 * 3600}"),
            lambda out: "↗" not in out,
        ),
        (lambda p, now: p.write_text(f"0 {now}"), lambda out: "↗" not in out),
        (lambda p, now: p.write_text(""), lambda out: "↗" not in out),
        (lambda p, now: None, lambda out: "↗" not in out),
    ],
    ids=["fresh", "aging", "too_stale", "zero", "empty_payload", "no_cache"],
)
def test_branch_identity_base_ahead(
    cache_dir, _clean_git_env, tmp_path, monkeypatch, setup, check
):
    repo = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    setup(cache_dir / "base-ahead-feature", int(time.time()))
    assert check(starship.print_branch_identity())


# ── integration: stash feeds field printers ────────────────────────────────


def test_stash_to_context_roundtrip(cache_dir, monkeypatch):
    blob = json.dumps(
        {
            "session_id": "sess99",
            "model": {"display_name": "Opus 4.7 (1M context)"},
            "context_window": {"used_percentage": 4, "context_window_size": 1000000},
            "rate_limits": {
                "five_hour": {
                    "used_percentage": 12,
                    "resets_at": "2026-05-21T20:00Z",
                }
            },
        }
    ).encode()
    claude_mod.stash_from_stdin(blob)
    monkeypatch.setenv("CSHIP_SESSION_ID", "sess99")
    assert "🧠 4%/1M" in starship.print_context()
    assert "⌛ 12%/5h" in starship.print_rate_limit()
