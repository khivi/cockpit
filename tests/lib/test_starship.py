"""Tests for scripts/lib/starship.py — field printers consumed by cship/starship."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import lib.claude as claude_mod
import lib.starship as starship
from lib.colors import (
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


def test_print_linear_extracts_ticket(cache_dir):
    with patch.object(starship, "_branch", return_value="khivi/PRO-123-fix"):
        assert starship.print_linear() == "PRO-123"


def test_print_linear_no_ticket(cache_dir):
    with patch.object(starship, "_branch", return_value="khivi/cleanup"):
        assert starship.print_linear() == ""


# ── PR cache reads ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("APPROVED", bold_leaf("APPROVED")),
        ("DRAFT", bold_shadow("DRAFT")),
        ("OPEN", bold_azure("OPEN")),
        ("REVIEW_REQUIRED", bold_orange("REVIEW_REQUIRED")),
        ("CHANGES_REQUESTED", bold_crimson("CHANGES_REQUESTED")),
        ("MERGED", bold_violet("MERGED")),
        ("CLOSED", bold_ruby("CLOSED")),
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
    assert starship.print_pr_num("khivi/foo") == "#42"


def test_print_pr_num_zero_sentinel_empty(cache_dir):
    (cache_dir / "pr-num-khivi-foo").write_text("0")
    assert starship.print_pr_num("khivi/foo") == ""


def test_print_pr_title(cache_dir):
    (cache_dir / "pr-title-khivi-foo").write_text("My PR")
    assert starship.print_pr_title("khivi/foo") == "My PR"


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


# ── field printer: branch_pill ─────────────────────────────────────────────


def _full_pill() -> str:
    """Composite of the two split segments — used by tests that exercise
    cross-segment behavior (e.g. layout/ordering). Tests scoped to a single
    segment should call the underlying printer directly.
    """
    identity = starship.print_branch_identity()
    status = starship.print_worktree_status()
    return f"{identity} {status}".strip()


def _init_repo(path: Path) -> None:
    import subprocess as sp

    sp.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True)
    sp.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    sp.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "f").write_text("x")
    sp.run(["git", "-C", str(path), "add", "f"], check=True)
    sp.run(
        ["git", "-C", str(path), "commit", "-q", "-m", "init"],
        check=True,
    )


def test_print_branch_pill_clean(_clean_git_env, tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    # Clean repo: no status segments → no powerline separator emitted.
    assert _full_pill() == slate("⎇ main")


def test_print_branch_pill_branch_name_slate_colored(_clean_git_env, monkeypatch):
    from lib import git as git_mod

    monkeypatch.setattr(starship, "current_branch", lambda _cwd: "feature")
    monkeypatch.setattr(starship, "ahead_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(starship, "behind_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(
        starship, "count_status", lambda _p: git_mod.GitStatusCounts(0, 0, 0)
    )
    out = _full_pill()
    assert out.startswith(slate("⎇ feature"))


def test_print_branch_pill_not_in_repo(_clean_git_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _full_pill() == ""


def _stub_branch_pill(monkeypatch, *, ahead=0, behind=0, status=(0, 0, 0)) -> None:
    from lib import git as git_mod

    monkeypatch.setattr(starship, "current_branch", lambda _cwd: "feature")
    monkeypatch.setattr(starship, "ahead_of_origin", lambda _cwd, _b: ahead)
    monkeypatch.setattr(starship, "behind_of_origin", lambda _cwd, _b: behind)
    monkeypatch.setattr(
        starship,
        "count_status",
        lambda _p: git_mod.GitStatusCounts(*status),
    )


def test_print_branch_pill_ahead_only(_clean_git_env, monkeypatch):
    from lib import git as git_mod

    monkeypatch.setattr(git_mod, "current_branch", lambda _cwd: "feature")
    monkeypatch.setattr(git_mod, "ahead_of_origin", lambda _cwd, _b: 3)
    monkeypatch.setattr(git_mod, "behind_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(
        git_mod, "count_status", lambda _p: git_mod.GitStatusCounts(0, 0, 0)
    )
    monkeypatch.setattr(starship, "current_branch", git_mod.current_branch)
    monkeypatch.setattr(starship, "ahead_of_origin", git_mod.ahead_of_origin)
    monkeypatch.setattr(starship, "behind_of_origin", git_mod.behind_of_origin)
    monkeypatch.setattr(starship, "count_status", git_mod.count_status)
    out = _full_pill()
    assert slate("⎇ feature") in out
    assert azure("↑3") in out


@pytest.mark.parametrize(
    "ahead,behind,status,expected_fragments",
    [
        (0, 2, (0, 0, 0), [orange("↓2")]),
        (3, 2, (0, 0, 0), [azure("↑3"), orange("↓2")]),
        (0, 0, (1, 0, 0), [leaf("●1")]),
        (0, 0, (0, 2, 0), [amber("✎2")]),
        (0, 0, (0, 0, 4), [shadow("✚4")]),
    ],
    ids=[
        "behind_only",
        "ahead_and_behind",
        "staged_only",
        "unstaged_only",
        "untracked_only",
    ],
)
def test_print_branch_pill_segments(
    _clean_git_env, monkeypatch, ahead, behind, status, expected_fragments
):
    _stub_branch_pill(monkeypatch, ahead=ahead, behind=behind, status=status)
    out = _full_pill()
    for frag in expected_fragments:
        assert frag in out


def test_print_branch_pill_all_segments(_clean_git_env, monkeypatch):
    from lib import git as git_mod

    monkeypatch.setattr(starship, "current_branch", lambda _cwd: "feature")
    monkeypatch.setattr(starship, "ahead_of_origin", lambda _cwd, _b: 1)
    monkeypatch.setattr(starship, "behind_of_origin", lambda _cwd, _b: 1)
    monkeypatch.setattr(
        starship, "count_status", lambda _p: git_mod.GitStatusCounts(1, 1, 1)
    )
    out = _full_pill()
    assert slate("⎇ feature") in out
    assert azure("↑1") in out
    assert orange("↓1") in out
    assert leaf("●1") in out
    assert amber("✎1") in out
    assert shadow("✚1") in out


def test_print_branch_pill_dirty_untracked_and_modified(
    _clean_git_env, tmp_path, monkeypatch
):
    _init_repo(tmp_path)
    (tmp_path / "f").write_text("y")
    (tmp_path / "new").write_text("z")
    monkeypatch.chdir(tmp_path)
    out = _full_pill()
    assert "⎇ main" in out
    assert "✎1" in out
    assert "✚1" in out
    assert "↑" not in out


# ── base-distance (↻N) ─────────────────────────────────────────────────────


def _stub_branch(monkeypatch, branch: str = "feature") -> None:
    from lib import git as git_mod

    monkeypatch.setattr(starship, "current_branch", lambda _cwd: branch)
    monkeypatch.setattr(starship, "ahead_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(starship, "behind_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(
        starship, "count_status", lambda _p: git_mod.GitStatusCounts(0, 0, 0)
    )


@pytest.mark.parametrize(
    "glyph,cache_prefix",
    [("↻", "base-distance"), ("↗", "base-ahead")],
    ids=["base_distance", "base_ahead"],
)
@pytest.mark.parametrize(
    "scenario",
    [
        "fresh",
        "aging",
        "too_stale_hidden",
        "zero_hidden",
        "empty_payload_hidden",
        "no_cache_hidden",
    ],
)
def test_print_branch_pill_base_relative(
    cache_dir, _clean_git_env, monkeypatch, glyph, cache_prefix, scenario
):
    _stub_branch(monkeypatch)
    fresh_color: Colorizer = orange if glyph == "↻" else azure
    cache_path = cache_dir / f"{cache_prefix}-feature"
    now = int(time.time())
    if scenario == "fresh":
        cache_path.write_text(f"7 {now}")
        out = _full_pill()
        assert fresh_color(f"{glyph}7") in out
        assert "ago" not in out
    elif scenario == "aging":
        epoch = now - (2 * 3600)
        cache_path.write_text(f"4 {epoch}")
        out = _full_pill()
        assert shadow(f"{glyph}4 (2h ago)") in out
    elif scenario == "too_stale_hidden":
        epoch = now - (8 * 3600)
        cache_path.write_text(f"4 {epoch}")
        assert glyph not in _full_pill()
    elif scenario == "zero_hidden":
        cache_path.write_text(f"0 {now}")
        assert glyph not in _full_pill()
    elif scenario == "empty_payload_hidden":
        cache_path.write_text("")
        assert glyph not in _full_pill()
    elif scenario == "no_cache_hidden":
        assert glyph not in _full_pill()


def test_print_branch_pill_base_distance_garbage_hidden(
    cache_dir, _clean_git_env, monkeypatch
):
    _stub_branch(monkeypatch)
    (cache_dir / "base-distance-feature").write_text("not numbers")
    out = _full_pill()
    assert "↻" not in out


def test_print_branch_pill_base_distance_slash_branch_key(
    cache_dir, _clean_git_env, monkeypatch
):
    """branch_cache slug-escapes `/` to `-`; verify the cache file path."""
    _stub_branch(monkeypatch, branch="khivi/master/foo")
    now = int(time.time())
    (cache_dir / "base-distance-khivi-master-foo").write_text(f"3 {now}")
    out = _full_pill()
    assert orange("↻3") in out


# ── base-ahead (↗N) ────────────────────────────────────────────────────────


def test_print_branch_pill_base_ahead_before_base_distance(
    cache_dir, _clean_git_env, monkeypatch
):
    """`↗N` (ahead) renders before `↻N` (behind) so the two base-relative
    segments read left-to-right as ahead-then-behind."""
    _stub_branch(monkeypatch)
    now = int(time.time())
    (cache_dir / "base-ahead-feature").write_text(f"7 {now}")
    (cache_dir / "base-distance-feature").write_text(f"3 {now}")
    out = _full_pill()
    assert out.index("↗7") < out.index("↻3")


def test_print_branch_pill_layout_ahead_before_separator_before_status(
    cache_dir, _clean_git_env, monkeypatch
):
    """Ahead counters (↑, ↗) sit between branch and the powerline separator;
    working-tree + behind + stale segments sit after the separator."""
    from lib import git as git_mod

    monkeypatch.setattr(starship, "current_branch", lambda _cwd: "feature")
    monkeypatch.setattr(starship, "ahead_of_origin", lambda _cwd, _b: 2)
    monkeypatch.setattr(starship, "behind_of_origin", lambda _cwd, _b: 1)
    monkeypatch.setattr(
        starship, "count_status", lambda _p: git_mod.GitStatusCounts(1, 1, 1)
    )
    now = int(time.time())
    (cache_dir / "base-ahead-feature").write_text(f"9 {now}")
    (cache_dir / "base-distance-feature").write_text(f"5 {now}")
    out = _full_pill()
    sep = starship.POWERLINE_BRANCH
    assert sep in out
    sep_pos = out.index(sep)
    assert out.index("⎇ feature") < out.index("↑2") < sep_pos
    assert out.index("↗9") < sep_pos
    assert sep_pos < out.index("●1")
    assert sep_pos < out.index("✎1")
    assert sep_pos < out.index("✚1")
    assert sep_pos < out.index("↓1")
    assert sep_pos < out.index("↻5")


def test_print_branch_pill_separator_hidden_without_status(_clean_git_env, monkeypatch):
    """Powerline separator is hidden when no trailing status segments exist."""
    from lib import git as git_mod

    monkeypatch.setattr(starship, "current_branch", lambda _cwd: "feature")
    monkeypatch.setattr(starship, "ahead_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(starship, "behind_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(
        starship, "count_status", lambda _p: git_mod.GitStatusCounts(0, 0, 0)
    )
    out = _full_pill()
    assert starship.POWERLINE_BRANCH not in out


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
