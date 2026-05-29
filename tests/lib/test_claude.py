"""Tests for scripts/lib/claude.py — stash_from_stdin parser."""

from __future__ import annotations

import json

import scripts.lib.claude as claude_mod

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


# ── stash: cost cache ──────────────────────────────────────────────────────


def test_stash_writes_cost_cache(cache_dir):
    blob = json.dumps(
        {
            "session_id": "S",
            "cost": {"total_cost_usd": 0.4237},
        }
    ).encode()
    claude_mod.stash_from_stdin(blob)
    assert (cache_dir / "cost-S").read_text() == "0.4237"


def test_stash_writes_zero_cost(cache_dir):
    """Zero is a valid cost (session just started). The emitter hides
    the pill at $0.00, but the cache write itself must not skip — that
    way a refresh from a non-zero back to zero (rare, but possible if
    Claude Code reports per-message cost) keeps the cache in sync."""
    blob = json.dumps({"session_id": "S", "cost": {"total_cost_usd": 0}}).encode()
    claude_mod.stash_from_stdin(blob)
    assert (cache_dir / "cost-S").read_text() == "0.0000"


def test_stash_skips_missing_cost(cache_dir):
    blob = json.dumps({"session_id": "S"}).encode()
    claude_mod.stash_from_stdin(blob)
    assert not (cache_dir / "cost-S").exists()


def test_stash_skips_non_numeric_cost(cache_dir):
    blob = json.dumps(
        {"session_id": "S", "cost": {"total_cost_usd": "not-a-number"}}
    ).encode()
    claude_mod.stash_from_stdin(blob)
    assert not (cache_dir / "cost-S").exists()


def test_stash_skips_negative_cost(cache_dir):
    """Defensive: negative cost shouldn't be possible, but if Claude Code
    ever ships a bug that sends one, don't poison the cache."""
    blob = json.dumps({"session_id": "S", "cost": {"total_cost_usd": -1.5}}).encode()
    claude_mod.stash_from_stdin(blob)
    assert not (cache_dir / "cost-S").exists()
