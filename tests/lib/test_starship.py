"""Tests for scripts/lib/starship.py — field printers consumed by cship/starship."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import lib.claude as claude_mod
import lib.starship as starship


# ── field printer: context (lib.starship) ──────────────────────────────────


def test_print_context_formats_ceiling_M(cache_dir):
    (cache_dir / "context").write_text("12 1000000")
    out = starship.print_context()
    assert "🧠 12%/1M" in out
    assert "\033[38;5;243m" in out
    assert out.endswith("\033[0m")


def test_print_context_formats_ceiling_k(cache_dir):
    (cache_dir / "context").write_text("33 200000")
    out = starship.print_context()
    assert "🧠 33%/200k" in out
    assert "\033[38;5;243m" in out


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


def test_print_context_tier_slate_under_70(cache_dir):
    (cache_dir / "context").write_text("5 1000000")
    out = starship.print_context()
    assert out.startswith("\033[38;5;243m")
    assert "🧠 5%/1M" in out


def test_print_context_tier_amber_70_to_89(cache_dir):
    (cache_dir / "context").write_text("75 1000000")
    out = starship.print_context()
    assert out.startswith("\033[38;5;172m")
    assert "🧠 75%/1M" in out


def test_print_context_tier_red_90_to_99(cache_dir):
    (cache_dir / "context").write_text("95 1000000")
    out = starship.print_context()
    assert out.startswith("\033[38;5;160m")
    assert "🧠 95%/1M" in out


def test_print_context_tier_red_bold_at_100(cache_dir):
    (cache_dir / "context").write_text("100 1000000")
    out = starship.print_context()
    assert out.startswith("\033[1;38;5;160m")
    assert "🧠 100%/1M" in out


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
    assert "⌛ 8%/5h" in out
    assert out.endswith("\033[0m")


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


def test_print_rate_limit_tier_slate_under_70(cache_dir):
    (cache_dir / "rate-limit-5h").write_text("5 2026-05-21T15:00:00Z")
    out = starship.print_rate_limit()
    assert out.startswith("\033[38;5;243m")
    assert "⌛ 5%/5h" in out


def test_print_rate_limit_tier_amber_70_to_89(cache_dir):
    (cache_dir / "rate-limit-5h").write_text("72 2026-05-21T15:00:00Z")
    out = starship.print_rate_limit()
    assert out.startswith("\033[38;5;172m")
    assert "⌛ 72%/5h" in out


def test_print_rate_limit_tier_red_90_to_99(cache_dir):
    (cache_dir / "rate-limit-5h").write_text("95 2026-05-21T15:00:00Z")
    out = starship.print_rate_limit()
    assert out.startswith("\033[38;5;160m")
    assert "⌛ 95%/5h" in out


def test_print_rate_limit_tier_red_bold_at_100(cache_dir):
    (cache_dir / "rate-limit-5h").write_text("100 2026-05-21T15:00:00Z")
    out = starship.print_rate_limit()
    assert out.startswith("\033[1;38;5;160m")
    assert "⌛ 100%/5h" in out


# ── field printer: linear ──────────────────────────────────────────────────


def test_print_linear_extracts_ticket(cache_dir):
    with patch.object(starship, "_branch", return_value="khivi/PRO-123-fix"):
        assert starship.print_linear() == "PRO-123"


def test_print_linear_no_ticket(cache_dir):
    with patch.object(starship, "_branch", return_value="khivi/cleanup"):
        assert starship.print_linear() == ""


# ── PR cache reads ─────────────────────────────────────────────────────────


def test_print_pr_state_fresh_cache(cache_dir):
    (cache_dir / "pr-state-khivi-foo").write_text("APPROVED")
    out = starship.print_pr_state("khivi/foo")
    assert "APPROVED" in out
    assert out.startswith("\033[1;38;5;34m")
    assert out.endswith("\033[0m")


def test_print_pr_state_draft(cache_dir):
    (cache_dir / "pr-state-khivi-foo").write_text("DRAFT")
    out = starship.print_pr_state("khivi/foo")
    assert out == "\033[1;38;5;240mDRAFT\033[0m"


def test_print_pr_state_open(cache_dir):
    (cache_dir / "pr-state-khivi-foo").write_text("OPEN")
    out = starship.print_pr_state("khivi/foo")
    assert out == "\033[1;38;5;32mOPEN\033[0m"


def test_print_pr_state_review_required(cache_dir):
    (cache_dir / "pr-state-khivi-foo").write_text("REVIEW_REQUIRED")
    out = starship.print_pr_state("khivi/foo")
    assert out == "\033[1;38;5;172mREVIEW_REQUIRED\033[0m"


def test_print_pr_state_changes_requested(cache_dir):
    (cache_dir / "pr-state-khivi-foo").write_text("CHANGES_REQUESTED")
    out = starship.print_pr_state("khivi/foo")
    assert out == "\033[1;38;5;160mCHANGES_REQUESTED\033[0m"


def test_print_pr_state_merged(cache_dir):
    (cache_dir / "pr-state-khivi-foo").write_text("MERGED")
    out = starship.print_pr_state("khivi/foo")
    assert out == "\033[1;38;5;91mMERGED\033[0m"


def test_print_pr_state_closed(cache_dir):
    (cache_dir / "pr-state-khivi-foo").write_text("CLOSED")
    out = starship.print_pr_state("khivi/foo")
    assert out == "\033[1;38;5;88mCLOSED\033[0m"


def test_print_pr_state_unknown_passes_through(cache_dir):
    (cache_dir / "pr-state-khivi-foo").write_text("WHATEVER")
    out = starship.print_pr_state("khivi/foo")
    assert out == "WHATEVER"


def test_print_pr_num_formats_hash(cache_dir):
    (cache_dir / "pr-num-khivi-foo").write_text("42")
    assert starship.print_pr_num("khivi/foo") == "#42"


def test_print_pr_num_zero_sentinel_empty(cache_dir):
    (cache_dir / "pr-num-khivi-foo").write_text("0")
    assert starship.print_pr_num("khivi/foo") == ""


def test_print_pr_title(cache_dir):
    (cache_dir / "pr-title-khivi-foo").write_text("My PR")
    assert starship.print_pr_title("khivi/foo") == "My PR"


def test_print_pr_checks_fresh_pass(cache_dir):
    (cache_dir / "pr-checks-khivi-foo").write_text("✓")
    out = starship.print_pr_checks("khivi/foo")
    assert out == "\033[32m✓\033[0m"


def test_print_pr_checks_fresh_fail(cache_dir):
    (cache_dir / "pr-checks-khivi-foo").write_text("✗")
    out = starship.print_pr_checks("khivi/foo")
    assert out == "\033[31m✗\033[0m"


def test_print_pr_checks_fresh_pending(cache_dir):
    (cache_dir / "pr-checks-khivi-foo").write_text("•")
    out = starship.print_pr_checks("khivi/foo")
    assert out == "\033[33m•\033[0m"


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


def test_print_permission_mode_default_hidden(cache_dir):
    (cache_dir / "permission-mode").write_text("default")
    assert starship.print_permission_mode() == ""


def test_print_permission_mode_plan(cache_dir):
    (cache_dir / "permission-mode").write_text("plan")
    assert starship.print_permission_mode() == "✎ plan"


def test_print_permission_mode_accept_edits(cache_dir):
    (cache_dir / "permission-mode").write_text("acceptEdits")
    assert starship.print_permission_mode() == "✎ accept-edits"


def test_print_permission_mode_bypass(cache_dir):
    (cache_dir / "permission-mode").write_text("bypassPermissions")
    assert starship.print_permission_mode() == "✎ bypass"


def test_print_permission_mode_unknown_value_hidden(cache_dir):
    (cache_dir / "permission-mode").write_text("zaphod")
    assert starship.print_permission_mode() == ""


# ── field printer: branch_pill ─────────────────────────────────────────────


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
    assert (
        starship.print_branch_pill()
        == "\033[38;5;243m⎇ main\033[0m \033[38;5;243m\033[0m"
    )


def test_print_branch_pill_branch_name_slate_colored(_clean_git_env, monkeypatch):
    from lib import git as git_mod

    monkeypatch.setattr(starship, "current_branch", lambda _cwd: "feature")
    monkeypatch.setattr(starship, "ahead_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(starship, "behind_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(
        starship, "count_status", lambda _p: git_mod.GitStatusCounts(0, 0, 0)
    )
    out = starship.print_branch_pill()
    assert out.startswith("\033[38;5;243m⎇ feature\033[0m")


def test_print_branch_pill_not_in_repo(_clean_git_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert starship.print_branch_pill() == ""


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
    out = starship.print_branch_pill()
    assert "\033[38;5;243m⎇ feature\033[0m" in out
    assert "\033[38;5;38m↑3\033[0m" in out


def test_print_branch_pill_behind_only(_clean_git_env, monkeypatch):
    from lib import git as git_mod

    monkeypatch.setattr(starship, "current_branch", lambda _cwd: "feature")
    monkeypatch.setattr(starship, "ahead_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(starship, "behind_of_origin", lambda _cwd, _b: 2)
    monkeypatch.setattr(
        starship, "count_status", lambda _p: git_mod.GitStatusCounts(0, 0, 0)
    )
    out = starship.print_branch_pill()
    assert "\033[38;5;172m↓2\033[0m" in out


def test_print_branch_pill_ahead_and_behind(_clean_git_env, monkeypatch):
    from lib import git as git_mod

    monkeypatch.setattr(starship, "current_branch", lambda _cwd: "feature")
    monkeypatch.setattr(starship, "ahead_of_origin", lambda _cwd, _b: 3)
    monkeypatch.setattr(starship, "behind_of_origin", lambda _cwd, _b: 2)
    monkeypatch.setattr(
        starship, "count_status", lambda _p: git_mod.GitStatusCounts(0, 0, 0)
    )
    out = starship.print_branch_pill()
    assert "\033[38;5;38m↑3\033[0m" in out
    assert "\033[38;5;172m↓2\033[0m" in out


def test_print_branch_pill_staged_only(_clean_git_env, monkeypatch):
    from lib import git as git_mod

    monkeypatch.setattr(starship, "current_branch", lambda _cwd: "feature")
    monkeypatch.setattr(starship, "ahead_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(starship, "behind_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(
        starship, "count_status", lambda _p: git_mod.GitStatusCounts(1, 0, 0)
    )
    out = starship.print_branch_pill()
    assert "\033[38;5;34m●1\033[0m" in out


def test_print_branch_pill_unstaged_only(_clean_git_env, monkeypatch):
    from lib import git as git_mod

    monkeypatch.setattr(starship, "current_branch", lambda _cwd: "feature")
    monkeypatch.setattr(starship, "ahead_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(starship, "behind_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(
        starship, "count_status", lambda _p: git_mod.GitStatusCounts(0, 2, 0)
    )
    out = starship.print_branch_pill()
    assert "\033[38;5;220m✎2\033[0m" in out


def test_print_branch_pill_untracked_only(_clean_git_env, monkeypatch):
    from lib import git as git_mod

    monkeypatch.setattr(starship, "current_branch", lambda _cwd: "feature")
    monkeypatch.setattr(starship, "ahead_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(starship, "behind_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(
        starship, "count_status", lambda _p: git_mod.GitStatusCounts(0, 0, 4)
    )
    out = starship.print_branch_pill()
    assert "\033[38;5;240m✚4\033[0m" in out


def test_print_branch_pill_all_segments(_clean_git_env, monkeypatch):
    from lib import git as git_mod

    monkeypatch.setattr(starship, "current_branch", lambda _cwd: "feature")
    monkeypatch.setattr(starship, "ahead_of_origin", lambda _cwd, _b: 1)
    monkeypatch.setattr(starship, "behind_of_origin", lambda _cwd, _b: 1)
    monkeypatch.setattr(
        starship, "count_status", lambda _p: git_mod.GitStatusCounts(1, 1, 1)
    )
    out = starship.print_branch_pill()
    assert "\033[38;5;243m⎇ feature\033[0m" in out
    assert "\033[38;5;38m↑1\033[0m" in out
    assert "\033[38;5;172m↓1\033[0m" in out
    assert "\033[38;5;34m●1\033[0m" in out
    assert "\033[38;5;220m✎1\033[0m" in out
    assert "\033[38;5;240m✚1\033[0m" in out


def test_print_branch_pill_dirty_untracked_and_modified(
    _clean_git_env, tmp_path, monkeypatch
):
    _init_repo(tmp_path)
    (tmp_path / "f").write_text("y")
    (tmp_path / "new").write_text("z")
    monkeypatch.chdir(tmp_path)
    out = starship.print_branch_pill()
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


def test_print_branch_pill_base_distance_fresh(cache_dir, _clean_git_env, monkeypatch):
    _stub_branch(monkeypatch)
    now = int(time.time())
    (cache_dir / "base-distance-feature").write_text(f"7 {now}")
    out = starship.print_branch_pill()
    assert "\033[38;5;172m↻7\033[0m" in out
    assert "ago" not in out


def test_print_branch_pill_base_distance_aging(cache_dir, _clean_git_env, monkeypatch):
    """30m–6h tier dims and includes the age."""
    _stub_branch(monkeypatch)
    epoch = int(time.time()) - (2 * 3600)
    (cache_dir / "base-distance-feature").write_text(f"4 {epoch}")
    out = starship.print_branch_pill()
    assert "\033[38;5;240m↻4 (2h ago)\033[0m" in out


def test_print_branch_pill_base_distance_too_stale_hidden(
    cache_dir, _clean_git_env, monkeypatch
):
    """>6h hides the segment — stale counts breed false confidence."""
    _stub_branch(monkeypatch)
    epoch = int(time.time()) - (8 * 3600)
    (cache_dir / "base-distance-feature").write_text(f"4 {epoch}")
    out = starship.print_branch_pill()
    assert "↻" not in out


def test_print_branch_pill_base_distance_zero_hidden(
    cache_dir, _clean_git_env, monkeypatch
):
    """`0` (branch is up to date with base) renders nothing."""
    _stub_branch(monkeypatch)
    now = int(time.time())
    (cache_dir / "base-distance-feature").write_text(f"0 {now}")
    out = starship.print_branch_pill()
    assert "↻" not in out


def test_print_branch_pill_base_distance_empty_payload_hidden(
    cache_dir, _clean_git_env, monkeypatch
):
    _stub_branch(monkeypatch)
    (cache_dir / "base-distance-feature").write_text("")
    out = starship.print_branch_pill()
    assert "↻" not in out


def test_print_branch_pill_base_distance_garbage_hidden(
    cache_dir, _clean_git_env, monkeypatch
):
    _stub_branch(monkeypatch)
    (cache_dir / "base-distance-feature").write_text("not numbers")
    out = starship.print_branch_pill()
    assert "↻" not in out


def test_print_branch_pill_base_distance_no_cache_hidden(
    cache_dir, _clean_git_env, monkeypatch
):
    _stub_branch(monkeypatch)
    out = starship.print_branch_pill()
    assert "↻" not in out


def test_print_branch_pill_base_distance_slash_branch_key(
    cache_dir, _clean_git_env, monkeypatch
):
    """branch_cache slug-escapes `/` to `-`; verify the cache file path."""
    _stub_branch(monkeypatch, branch="khivi/master/foo")
    now = int(time.time())
    (cache_dir / "base-distance-khivi-master-foo").write_text(f"3 {now}")
    out = starship.print_branch_pill()
    assert "\033[38;5;172m↻3\033[0m" in out


# ── base-ahead (↗N) ────────────────────────────────────────────────────────


def test_print_branch_pill_base_ahead_fresh(cache_dir, _clean_git_env, monkeypatch):
    _stub_branch(monkeypatch)
    now = int(time.time())
    (cache_dir / "base-ahead-feature").write_text(f"7 {now}")
    out = starship.print_branch_pill()
    assert "\033[38;5;38m↗7\033[0m" in out
    assert "ago" not in out


def test_print_branch_pill_base_ahead_aging(cache_dir, _clean_git_env, monkeypatch):
    _stub_branch(monkeypatch)
    epoch = int(time.time()) - (2 * 3600)
    (cache_dir / "base-ahead-feature").write_text(f"4 {epoch}")
    out = starship.print_branch_pill()
    assert "\033[38;5;240m↗4 (2h ago)\033[0m" in out


def test_print_branch_pill_base_ahead_too_stale_hidden(
    cache_dir, _clean_git_env, monkeypatch
):
    _stub_branch(monkeypatch)
    epoch = int(time.time()) - (8 * 3600)
    (cache_dir / "base-ahead-feature").write_text(f"4 {epoch}")
    out = starship.print_branch_pill()
    assert "↗" not in out


def test_print_branch_pill_base_ahead_zero_hidden(
    cache_dir, _clean_git_env, monkeypatch
):
    _stub_branch(monkeypatch)
    now = int(time.time())
    (cache_dir / "base-ahead-feature").write_text(f"0 {now}")
    out = starship.print_branch_pill()
    assert "↗" not in out


def test_print_branch_pill_base_ahead_empty_payload_hidden(
    cache_dir, _clean_git_env, monkeypatch
):
    _stub_branch(monkeypatch)
    (cache_dir / "base-ahead-feature").write_text("")
    out = starship.print_branch_pill()
    assert "↗" not in out


def test_print_branch_pill_base_ahead_no_cache_hidden(
    cache_dir, _clean_git_env, monkeypatch
):
    _stub_branch(monkeypatch)
    out = starship.print_branch_pill()
    assert "↗" not in out


def test_print_branch_pill_base_ahead_before_base_distance(
    cache_dir, _clean_git_env, monkeypatch
):
    """`↗N` (ahead) renders before `↻N` (behind) so the two base-relative
    segments read left-to-right as ahead-then-behind."""
    _stub_branch(monkeypatch)
    now = int(time.time())
    (cache_dir / "base-ahead-feature").write_text(f"7 {now}")
    (cache_dir / "base-distance-feature").write_text(f"3 {now}")
    out = starship.print_branch_pill()
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
    out = starship.print_branch_pill()
    sep = ""
    assert sep in out
    sep_pos = out.index(sep)
    assert out.index("⎇ feature") < out.index("↑2") < sep_pos
    assert out.index("↗9") < sep_pos
    assert sep_pos < out.index("●1")
    assert sep_pos < out.index("✎1")
    assert sep_pos < out.index("✚1")
    assert sep_pos < out.index("↓1")
    assert sep_pos < out.index("↻5")


def test_print_branch_pill_separator_always_renders(_clean_git_env, monkeypatch):
    """Powerline separator renders even when no trailing status segments exist."""
    from lib import git as git_mod

    monkeypatch.setattr(starship, "current_branch", lambda _cwd: "feature")
    monkeypatch.setattr(starship, "ahead_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(starship, "behind_of_origin", lambda _cwd, _b: 0)
    monkeypatch.setattr(
        starship, "count_status", lambda _p: git_mod.GitStatusCounts(0, 0, 0)
    )
    out = starship.print_branch_pill()
    assert "" in out

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
