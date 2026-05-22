"""Tests for starship field printers + cockpit-cache writers.

Cache writers live in `lib.cache` (flat cockpit-cache section); field
printers live in `lib.starship`; the Claude Code stdin parser lives in
`lib.claude`. Each fixture redirects `FLAT_CACHE_DIR` to a tmpdir so
concurrent runs and the real `$TMPDIR/cockpit-cache/` are never touched.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import lib.cache as cache_mod  # noqa: E402
import lib.claude as claude_mod  # noqa: E402
import lib.starship as starship  # noqa: E402


@pytest.fixture
def cache_dir(tmp_path, monkeypatch) -> Path:
    """Redirect FLAT_CACHE_DIR to a tmpdir for the duration of one test."""
    cdir = tmp_path / "cockpit-cache"
    cdir.mkdir()
    monkeypatch.setattr(cache_mod, "FLAT_CACHE_DIR", cdir)
    yield cdir


# ── stash_from_stdin (lib.claude) ──────────────────────────────────────────


def test_stash_writes_context_rate_transcript(cache_dir):
    blob = json.dumps(
        {
            "session_id": "abc",
            "model": {"display_name": "Opus 4.7 (1M context)"},
            "transcript_path": "/tmp/t.jsonl",
            "context_window": {"used_percentage": 12, "context_window_size": 1000000},
            "rate_limits": {
                "five_hour": {
                    "used_percentage": 4.6,
                    "resets_at": "2026-05-21T15:00:00Z",
                }
            },
        }
    ).encode()
    mutated, sid = claude_mod.stash_from_stdin(blob)
    assert sid == "abc"
    out = json.loads(mutated)
    assert out["model"]["display_name"] == "Opus 4.7"
    assert (cache_dir / "context-abc").read_text() == "12 1000000"
    assert (cache_dir / "transcript-path-abc").read_text() == "/tmp/t.jsonl"
    assert (cache_dir / "rate-limit-5h-abc").read_text() == "5 2026-05-21T15:00:00Z"


def test_stash_no_session_id_uses_unsuffixed_files(cache_dir):
    blob = json.dumps(
        {
            "context_window": {"used_percentage": 50, "context_window_size": 200000},
            "transcript_path": "/tmp/t.jsonl",
        }
    ).encode()
    mutated, sid = claude_mod.stash_from_stdin(blob)
    assert sid is None
    assert (cache_dir / "context").read_text() == "50 200000"
    assert (cache_dir / "transcript-path").read_text() == "/tmp/t.jsonl"


def test_stash_handles_malformed_json(cache_dir):
    mutated, sid = claude_mod.stash_from_stdin(b"not json")
    assert mutated == b"not json"
    assert sid is None
    assert not any(cache_dir.iterdir())


def test_stash_handles_empty_blob(cache_dir):
    mutated, sid = claude_mod.stash_from_stdin(b"")
    assert mutated == b""
    assert sid is None


def test_stash_strips_only_trailing_paren_suffix(cache_dir):
    blob = json.dumps({"model": {"display_name": "Claude 4.7 (something)"}}).encode()
    mutated, _ = claude_mod.stash_from_stdin(blob)
    assert json.loads(mutated)["model"]["display_name"] == "Claude 4.7"


def test_stash_coerces_iso_resets_at_to_epoch(cache_dir):
    """cship 1.7.x rejects the entire JSON if `resets_at` is a string;
    when it does, the footer renders empty. Coerce ISO → epoch in the
    outgoing blob so cship's u64 parser is satisfied."""
    blob = json.dumps(
        {
            "session_id": "x",
            "rate_limits": {
                "five_hour": {
                    "used_percentage": 4.6,
                    "resets_at": "2026-05-22T15:00:00Z",
                }
            },
        }
    ).encode()
    mutated, _ = claude_mod.stash_from_stdin(blob)
    out = json.loads(mutated)
    epoch = out["rate_limits"]["five_hour"]["resets_at"]
    assert isinstance(epoch, int), f"resets_at not coerced: {epoch!r}"
    assert epoch == 1779462000


def test_stash_leaves_numeric_resets_at_alone(cache_dir):
    """Numeric `resets_at` is already what cship wants — don't touch it."""
    blob = json.dumps(
        {
            "session_id": "x",
            "rate_limits": {
                "five_hour": {"used_percentage": 1, "resets_at": 1779462000},
            },
        }
    ).encode()
    mutated, _ = claude_mod.stash_from_stdin(blob)
    out = json.loads(mutated)
    assert out["rate_limits"]["five_hour"]["resets_at"] == 1779462000


def test_stash_leaves_unparseable_resets_at_alone(cache_dir):
    """If the string can't be parsed as ISO, leave it. cship will still
    reject this case — but that's an upstream schema bug, not ours to
    silently mask."""
    blob = json.dumps(
        {
            "session_id": "x",
            "rate_limits": {
                "five_hour": {"used_percentage": 1, "resets_at": "garbage"},
            },
        }
    ).encode()
    mutated, _ = claude_mod.stash_from_stdin(blob)
    out = json.loads(mutated)
    assert out["rate_limits"]["five_hour"]["resets_at"] == "garbage"


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


def test_print_context_missing_cache_empty(cache_dir):
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


def test_print_linear_no_branch(cache_dir):
    with patch.object(starship, "_branch", return_value=""):
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


def test_print_pr_num_empty_cache_empty(cache_dir):
    (cache_dir / "pr-num-khivi-foo").write_text("")
    assert starship.print_pr_num("khivi/foo") == ""


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


# ── write_branch_pr_cache (daemon-tick path, lib.cache) ────────────────────


def test_write_branch_pr_cache_resolves_state(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="APPROVED",
        number=17,
        title="Hello",
        ci_glyph="✓",
    )
    assert (cache_dir / "pr-state-khivi-feature").read_text() == "APPROVED"
    assert (cache_dir / "pr-num-khivi-feature").read_text() == "17"
    assert (cache_dir / "pr-title-khivi-feature").read_text() == "Hello"
    assert (cache_dir / "pr-checks-khivi-feature").read_text() == "✓"


def test_write_branch_pr_cache_draft_overrides_open(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=True,
        review_decision="",
        number=18,
        title="Draft",
    )
    assert (cache_dir / "pr-state-khivi-feature").read_text() == "DRAFT"


def test_write_branch_pr_cache_closed_state_preserved(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="MERGED",
        is_draft=False,
        review_decision="APPROVED",
        number=19,
        title="Done",
    )
    assert (cache_dir / "pr-state-khivi-feature").read_text() == "MERGED"


def test_write_branch_pr_cache_no_branch_noop(cache_dir):
    cache_mod.write_branch_pr_cache(
        "",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=1,
        title="x",
    )
    assert not any(cache_dir.iterdir())


# ── refresh_pr_data via mocked gh (lib.cache) ──────────────────────────────


def test_refresh_pr_data_writes_no_pr_sentinel(cache_dir):
    with patch.object(cache_mod, "_gh_pr_view", return_value=None):
        cache_mod.refresh_pr_data("khivi/foo")
    assert (cache_dir / "pr-state-khivi-foo").read_text() == ""
    assert (cache_dir / "pr-num-khivi-foo").read_text() == ""
    assert (cache_dir / "pr-title-khivi-foo").read_text() == ""


def test_refresh_pr_data_populates_from_gh(cache_dir):
    payload = {
        "state": "OPEN",
        "isDraft": False,
        "reviewDecision": "CHANGES_REQUESTED",
        "number": 99,
        "title": "Fix it",
    }
    with patch.object(cache_mod, "_gh_pr_view", return_value=payload):
        cache_mod.refresh_pr_data("khivi/bar")
    assert (cache_dir / "pr-state-khivi-bar").read_text() == "CHANGES_REQUESTED"
    assert (cache_dir / "pr-num-khivi-bar").read_text() == "99"
    assert (cache_dir / "pr-title-khivi-bar").read_text() == "Fix it"


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


# ── stash: model + permission_mode caches ──────────────────────────────────


def test_stash_writes_model_cache(cache_dir):
    blob = json.dumps(
        {
            "session_id": "S",
            "model": {"display_name": "Opus 4.7 (1M context)"},
        }
    ).encode()
    claude_mod.stash_from_stdin(blob)
    assert (cache_dir / "model-S").read_text() == "Opus 4.7"


def test_stash_writes_permission_mode_cache(cache_dir):
    blob = json.dumps({"session_id": "S", "permission_mode": "plan"}).encode()
    claude_mod.stash_from_stdin(blob)
    assert (cache_dir / "permission-mode-S").read_text() == "plan"


def test_stash_skips_empty_permission_mode(cache_dir):
    blob = json.dumps({"session_id": "S", "permission_mode": ""}).encode()
    claude_mod.stash_from_stdin(blob)
    assert not (cache_dir / "permission-mode-S").exists()


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


def test_print_permission_mode_missing_empty(cache_dir):
    assert starship.print_permission_mode() == ""


# ── field printer: branch_pill ─────────────────────────────────────────────


@pytest.fixture
def _clean_git_env(monkeypatch) -> None:
    """Strip ambient GIT_* env vars so git commands target the tmpdir's
    repo (or no repo) instead of inheriting the caller's repo. Pre-commit
    in particular exports GIT_DIR pointing at the host repo, which makes
    `git -C tmp_path log` return the host's HEAD instead of failing
    cleanly when tmp_path is not a repo."""
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(var, raising=False)


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
    assert starship.print_branch_pill() == "\033[38;5;243m⎇ main\033[0m"


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


# ── write_base_distance (lib.cache) ────────────────────────────────────────


def test_write_base_distance_writes_payload(cache_dir):
    cache_mod.write_base_distance("khivi/feature", 5, 1700000000)
    assert (cache_dir / "base-distance-khivi-feature").read_text() == "5 1700000000"


def test_write_base_distance_empty_on_negative_count(cache_dir):
    cache_mod.write_base_distance("khivi/feature", -1, 1700000000)
    assert (cache_dir / "base-distance-khivi-feature").read_text() == ""


def test_write_base_distance_empty_on_missing_epoch(cache_dir):
    cache_mod.write_base_distance("khivi/feature", 3, 0)
    assert (cache_dir / "base-distance-khivi-feature").read_text() == ""


def test_write_base_distance_zero_count_is_valid(cache_dir):
    """0 commits behind base is a legitimate, fresh observation; the
    reader hides 0 but the writer should preserve it for staleness gating."""
    cache_mod.write_base_distance("khivi/feature", 0, 1700000000)
    assert (cache_dir / "base-distance-khivi-feature").read_text() == "0 1700000000"


def test_write_base_distance_no_branch_noop(cache_dir):
    cache_mod.write_base_distance("", 3, 1700000000)
    assert not any(cache_dir.iterdir())


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


# ── write_base_ahead (lib.cache) ───────────────────────────────────────────


def test_write_base_ahead_writes_payload(cache_dir):
    cache_mod.write_base_ahead("khivi/feature", 5, 1700000000)
    assert (cache_dir / "base-ahead-khivi-feature").read_text() == "5 1700000000"


def test_write_base_ahead_empty_on_negative_count(cache_dir):
    cache_mod.write_base_ahead("khivi/feature", -1, 1700000000)
    assert (cache_dir / "base-ahead-khivi-feature").read_text() == ""


def test_write_base_ahead_empty_on_missing_epoch(cache_dir):
    cache_mod.write_base_ahead("khivi/feature", 3, 0)
    assert (cache_dir / "base-ahead-khivi-feature").read_text() == ""


def test_write_base_ahead_zero_count_is_valid(cache_dir):
    cache_mod.write_base_ahead("khivi/feature", 0, 1700000000)
    assert (cache_dir / "base-ahead-khivi-feature").read_text() == "0 1700000000"


def test_write_base_ahead_no_branch_noop(cache_dir):
    cache_mod.write_base_ahead("", 3, 1700000000)
    assert not any(cache_dir.iterdir())


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
