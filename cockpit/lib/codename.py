"""Deterministic "cool" codename generator.

A spawn source that carries no human-readable name of its own — currently a
Slack thread URL (`spawn.detect_source` → `slack` mode) — still needs a branch
name at worktree-creation time, *before* the spawned Claude has read the thread.
This module synthesizes a memorable `<adjective>-<noun>` codename (e.g.
`cosmic-otter`) from an arbitrary seed string.

Pure and **deterministic**: the same seed always yields the same codename. That
matters because cockpit re-runs are idempotent — re-spawning the same Slack URL
must resolve to the same branch rather than spraying `cosmic-otter`,
`lunar-otter`, … across the worktree list on every retry. The codebase also
forbids `random`/time-based nondeterminism in this kind of derivation, so the
seed is hashed (`hashlib.blake2b`) and the digest indexes into two curated word
lists. Collisions across *different* seeds are fine — `spawn._bump_until_free`
appends `-2`/`-3` when a branch already exists.
"""

from __future__ import annotations

import hashlib

# Curated for legibility as branch slugs: short, lowercase, no hyphens, no
# easily-confused homophones. Space / nature / texture themes keep them
# pronounceable and pleasant. Lengths are intentionally not powers of two — the
# modulo below tolerates any size.
ADJECTIVES = (
    "cosmic",
    "lunar",
    "solar",
    "nebula",
    "quantum",
    "amber",
    "crimson",
    "azure",
    "verdant",
    "golden",
    "silver",
    "obsidian",
    "electric",
    "frosted",
    "gilded",
    "hidden",
    "swift",
    "silent",
    "ember",
    "twilight",
    "radiant",
    "stormy",
    "molten",
    "polar",
    "feral",
    "noble",
    "rugged",
    "velvet",
    "crystal",
    "shadow",
)

NOUNS = (
    "otter",
    "falcon",
    "badger",
    "lynx",
    "heron",
    "marten",
    "ibex",
    "raven",
    "viper",
    "kestrel",
    "panther",
    "wombat",
    "tapir",
    "narwhal",
    "gecko",
    "mantis",
    "comet",
    "pulsar",
    "quasar",
    "meteor",
    "cypress",
    "juniper",
    "bramble",
    "thistle",
    "boulder",
    "canyon",
    "glacier",
    "harbor",
    "lantern",
    "compass",
)


def codename(seed: str) -> str:
    """Return a deterministic `<adjective>-<noun>` codename for `seed`.

    Hashes `seed` with blake2b (stable across processes and Python versions —
    unlike the salted builtin `hash()`), then slices the digest into two
    independent indices so the adjective and noun vary independently. An empty
    seed is allowed and maps to a fixed codename like any other input.
    """
    digest = hashlib.blake2b(seed.encode("utf-8"), digest_size=8).digest()
    n = int.from_bytes(digest, "big")
    adj = ADJECTIVES[(n >> 16) % len(ADJECTIVES)]
    noun = NOUNS[n % len(NOUNS)]
    return f"{adj}-{noun}"
