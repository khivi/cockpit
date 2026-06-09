"""Tests for cockpit/lib/codename.py — the deterministic codename generator.

Pure function, so these assert the three properties spawn relies on:
determinism (same seed → same name, which keeps Slack re-spawns idempotent),
the `<adjective>-<noun>` shape (so it slugifies cleanly into a branch), and a
reasonable spread across the word lists (so distinct threads rarely collide).
"""

from __future__ import annotations

from cockpit.lib.codename import ADJECTIVES, NOUNS, codename


def test_deterministic_same_seed_same_name():
    assert codename("C0123/1700000000.1") == codename("C0123/1700000000.1")


def test_different_seeds_usually_differ():
    a = codename("C0123/1700000000.1")
    b = codename("C0123/1700000000.2")
    assert a != b


def test_shape_is_adjective_dash_noun_from_word_lists():
    name = codename("anything")
    adj, sep, noun = name.partition("-")
    assert sep == "-"
    assert adj in ADJECTIVES
    assert noun in NOUNS


def test_empty_seed_allowed_and_stable():
    assert codename("") == codename("")
    assert "-" in codename("")


def test_words_are_branch_safe_slugs():
    # Every word must already be a clean lowercase slug fragment so the
    # resulting `<adj>-<noun>` survives `git.slugify` unchanged.
    for word in (*ADJECTIVES, *NOUNS):
        assert word.islower()
        assert word.isalnum()


def test_reasonable_spread_across_seeds():
    # 200 distinct seeds should land on many distinct codenames — a generator
    # collapsing to a handful of names would make every Slack worktree collide
    # and lean on `-2`/`-3` bumping. Expect well over half unique.
    names = {codename(f"seed-{i}") for i in range(200)}
    assert len(names) > 100
