"""Tests for scripts/lib/cship.py — cache writers + field printers.

Isolates CACHE_DIR per test via monkeypatch so concurrent runs and the
real `$TMPDIR/cship-cache/` are never touched.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import lib.claude as claude_mod  # noqa: E402
import lib.cship as cship  # noqa: E402


@pytest.fixture
def cache_dir(tmp_path, monkeypatch) -> Path:
    """Redirect CACHE_DIR to a tmpdir for the duration of one test."""
    cdir = tmp_path / "cship-cache"
    cdir.mkdir()
    monkeypatch.setattr(cship, "CACHE_DIR", cdir)
    yield cdir


# ── stash_from_stdin ───────────────────────────────────────────────────────


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


# ── print_context ──────────────────────────────────────────────────────────


def test_print_context_formats_ceiling_M(cache_dir):
    (cache_dir / "context").write_text("12 1000000")
    assert cship.print_context() == "12%/1M"


def test_print_context_formats_ceiling_k(cache_dir):
    (cache_dir / "context").write_text("33 200000")
    assert cship.print_context() == "33%/200k"


def test_print_context_session_scoped(cache_dir, monkeypatch):
    (cache_dir / "context-S1").write_text("7 1000000")
    monkeypatch.setenv("CSHIP_SESSION_ID", "S1")
    assert cship.print_context() == "7%/1M"


def test_print_context_missing_cache_empty(cache_dir):
    assert cship.print_context() == ""


def test_print_context_malformed_cache_empty(cache_dir):
    (cache_dir / "context").write_text("garbage")
    assert cship.print_context() == ""


def test_print_context_zero_limit_empty(cache_dir):
    (cache_dir / "context").write_text("50 0")
    assert cship.print_context() == ""


# ── print_rate_limit ───────────────────────────────────────────────────────


def test_print_rate_limit(cache_dir):
    (cache_dir / "rate-limit-5h").write_text("8 2026-05-21T15:00:00Z")
    assert cship.print_rate_limit() == "⌛ 8%/5h"


def test_print_rate_limit_missing_cache_empty(cache_dir):
    assert cship.print_rate_limit() == ""


# ── print_linear ───────────────────────────────────────────────────────────


def test_print_linear_extracts_ticket(cache_dir):
    with patch.object(cship, "_current_branch", return_value="khivi/PRO-123-fix"):
        assert cship.print_linear() == "PRO-123"


def test_print_linear_no_ticket(cache_dir):
    with patch.object(cship, "_current_branch", return_value="khivi/cleanup"):
        assert cship.print_linear() == ""


def test_print_linear_no_branch(cache_dir):
    with patch.object(cship, "_current_branch", return_value=""):
        assert cship.print_linear() == ""


# ── PR cache (state / num / title / checks) ────────────────────────────────


def test_print_pr_state_fresh_cache(cache_dir):
    (cache_dir / "pr-state-khivi-foo").write_text("APPROVED")
    assert cship.print_pr_state("khivi/foo") == "APPROVED"


def test_print_pr_num_formats_hash(cache_dir):
    (cache_dir / "pr-num-khivi-foo").write_text("42")
    assert cship.print_pr_num("khivi/foo") == "#42"


def test_print_pr_num_empty_cache_empty(cache_dir):
    (cache_dir / "pr-num-khivi-foo").write_text("")
    assert cship.print_pr_num("khivi/foo") == ""


def test_print_pr_num_zero_sentinel_empty(cache_dir):
    (cache_dir / "pr-num-khivi-foo").write_text("0")
    assert cship.print_pr_num("khivi/foo") == ""


def test_print_pr_title(cache_dir):
    (cache_dir / "pr-title-khivi-foo").write_text("My PR")
    assert cship.print_pr_title("khivi/foo") == "My PR"


def test_print_pr_checks_fresh(cache_dir):
    cache = cache_dir / "pr-checks-khivi-foo"
    cache.write_text("✓")
    assert cship.print_pr_checks("khivi/foo") == "✓"


def test_print_pr_state_stale_triggers_refresh(cache_dir):
    cache = cache_dir / "pr-state-khivi-foo"
    cache.write_text("OPEN")
    # Age the file past the 60s TTL.
    old = time.time() - 3600
    import os

    os.utime(cache, (old, old))
    with patch.object(cship, "_spawn_background_refresh") as spawn:
        out = cship.print_pr_state("khivi/foo")
    assert out == "OPEN"  # stale payload still returned
    spawn.assert_called_once_with("pr-state")


# ── write_branch_pr_cache (daemon-tick path) ───────────────────────────────


def test_write_branch_pr_cache_resolves_state(cache_dir):
    cship.write_branch_pr_cache(
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
    cship.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=True,
        review_decision="",
        number=18,
        title="Draft",
    )
    assert (cache_dir / "pr-state-khivi-feature").read_text() == "DRAFT"


def test_write_branch_pr_cache_closed_state_preserved(cache_dir):
    cship.write_branch_pr_cache(
        "khivi/feature",
        state="MERGED",
        is_draft=False,
        review_decision="APPROVED",
        number=19,
        title="Done",
    )
    assert (cache_dir / "pr-state-khivi-feature").read_text() == "MERGED"


def test_write_branch_pr_cache_no_branch_noop(cache_dir):
    cship.write_branch_pr_cache(
        "",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=1,
        title="x",
    )
    assert not any(cache_dir.iterdir())


# ── refresh_pr_data via mocked gh ──────────────────────────────────────────


def test_refresh_pr_data_writes_no_pr_sentinel(cache_dir):
    with patch.object(cship, "_gh_pr_view", return_value=None):
        cship.refresh_pr_data("khivi/foo")
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
    with patch.object(cship, "_gh_pr_view", return_value=payload):
        cship.refresh_pr_data("khivi/bar")
    assert (cache_dir / "pr-state-khivi-bar").read_text() == "CHANGES_REQUESTED"
    assert (cache_dir / "pr-num-khivi-bar").read_text() == "99"
    assert (cache_dir / "pr-title-khivi-bar").read_text() == "Fix it"


# ── session-time ──────────────────────────────────────────────────────────


def test_print_session_time_no_transcript_cache(cache_dir):
    assert cship.print_session_time() == ""


def test_print_session_time_missing_transcript_file(cache_dir):
    (cache_dir / "transcript-path").write_text("/nope/missing.jsonl")
    assert cship.print_session_time() == ""


def test_print_session_time_formats_minutes(cache_dir, tmp_path):
    transcript = tmp_path / "t.jsonl"
    # Use a timestamp two hours ago in UTC; the parser strips Z + treats as UTC.
    past = time.gmtime(time.time() - 2 * 3600 - 5 * 60)
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", past) + "Z"
    transcript.write_text(json.dumps({"timestamp": iso}) + "\n")
    (cache_dir / "transcript-path").write_text(str(transcript))
    out = cship.print_session_time()
    # Allow small drift in case the test runner is slow; just check shape.
    assert out.endswith("m") and "h " in out


def test_print_session_time_skips_under_10s(cache_dir, tmp_path):
    transcript = tmp_path / "t.jsonl"
    past = time.gmtime(time.time() - 2)
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", past) + "Z"
    transcript.write_text(json.dumps({"timestamp": iso}) + "\n")
    (cache_dir / "transcript-path").write_text(str(transcript))
    assert cship.print_session_time() == ""


# ── integration: wrapper feeds fields ──────────────────────────────────────


def test_wrapper_to_context_roundtrip(cache_dir, monkeypatch):
    blob = json.dumps(
        {
            "session_id": "sess99",
            "model": {"display_name": "Opus 4.7 (1M context)"},
            "context_window": {"used_percentage": 4, "context_window_size": 1000000},
            "rate_limits": {
                "five_hour": {"used_percentage": 12, "resets_at": "2026-05-21T20:00Z"}
            },
        }
    ).encode()
    claude_mod.stash_from_stdin(blob)
    monkeypatch.setenv("CSHIP_SESSION_ID", "sess99")
    assert cship.print_context() == "4%/1M"
    assert cship.print_rate_limit() == "⌛ 12%/5h"
