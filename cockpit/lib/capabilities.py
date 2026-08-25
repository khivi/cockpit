"""What the resolved cmux can actually do — verbs from `--help`, capability ids
from `cmux capabilities`.

`lib.tool` answers *which* backend is in effect (presence only, via
`shutil.which`); this module answers whether that backend is new enough. Two
independent axes, because cmux exposes them separately:

  - **CLI verbs** — `send` / `send-key` / `list-status` / `list-workspaces` /
    `new-workspace`. There is no JSON verb list, so they're read out of the
    `Commands:` section of `cmux --help`. cockpit shells out to 15 verbs in
    total; these five are the ones gated here, because the rest are either
    best-effort (`_CMUX_ONLY_VERBS` — a missed pill or tint is cosmetic),
    gated on the capability axis instead (`events`), or self-gating
    (`capabilities`). See `docs/cmux-surface-audit.md` for the full map.
  - **Capability ids** — `cmux capabilities` returns a negotiated JSON list
    (`events.v1`, `workspace.groups.v1`, …). `capabilities` is itself a recent
    verb, so its *absence from the verb list* is the "cmux is too old" signal —
    not "this cmux has no capabilities".

Probed at most once per process (`probe` is `lru_cache`d). Deliberately NOT
folded into `tool.resolve_tool`, which stays uncached per call so tests can
vary PATH and config without cache leakage.

Nothing here dies: a missing verb or capability warns at preflight and leaves
the affected tier degraded, matching how cockpit already falls back to
cache-only mode when there's no backend at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

# The verbs cockpit shells out to. Each names the tier that stops working
# without it, so the preflight warning can say what the user loses.
REQUIRED_VERBS: dict[str, str] = {
    "send": "nudges and broadcast",
    "send-key": "nudges and broadcast",
    "list-status": "the nudge idle-gate",
    "list-workspaces": "workspace inventory",
    "new-workspace": "workspace spawning",
}

# Capability ids cockpit (and the features built on top of it) negotiate for.
# Every entry must name a tier cockpit actually HAS — a required id for an
# unbuilt feature tells the user to upgrade cmux for nothing. `terminal.replay.v1`
# ("screen preview") and `notification.feed.v1` ("the notification feed") were
# both exactly that and are deliberately absent: the preview was dropped
# plan-only, and cockpit calls no notification verb.
REQUIRED_CAPABILITIES: dict[str, str] = {
    "workspace.read_state.v1": "workspace state reads",
    "terminal.input.ordered.v1": "ordered nudge delivery",
    "events.v1": "the cmux event stream",
    # The only gate `workspace-group` can have: it is absent from `cmux --help`,
    # so `parse_verbs` cannot see it and `REQUIRED_VERBS` would report it
    # permanently missing. The umbrella id is deliberate — its absence means no
    # folds at all, where the narrower `group_actions`/`group_create` pair would
    # add warning noise without a distinct failure mode.
    "workspace.groups.v1": "sidebar folds (stacks, reviews, snoozed)",
}


@dataclass(frozen=True)
class BackendProbe:
    """One snapshot of the resolved cmux's verbs + capability ids."""

    verbs: frozenset[str]
    capabilities: frozenset[str]

    @property
    def supports_capabilities(self) -> bool:
        """False on a cmux predating the `capabilities` verb — i.e. too old.

        Distinct from an empty `capabilities` set, which would mean a cmux that
        answered the question with nothing.
        """
        return "capabilities" in self.verbs

    def missing_verbs(self) -> tuple[str, ...]:
        return tuple(v for v in REQUIRED_VERBS if v not in self.verbs)

    def missing_capabilities(self) -> tuple[str, ...]:
        return tuple(c for c in REQUIRED_CAPABILITIES if c not in self.capabilities)


def parse_verbs(help_text: str) -> frozenset[str]:
    """First token of every command line in `cmux --help`'s `Commands:` section.

    The section runs from the `Commands:` header to the next unindented one
    (`Environment:`). A line may list alternatives (`next-window |
    previous-window`) and always trails into `[flags]` / `<args>` / `(prose)`.

    ponytail: parses help text because cmux ships no machine-readable list of
    *verbs*. It does ship one of RPC *methods* — `cmux capabilities` returns a
    303-entry `methods` array that `parse_capabilities` currently discards — so
    the swap is available for anything reachable via `cmux rpc`, but not for the
    CLI surface this gate covers. See `docs/cmux-surface-audit.md`. A parse miss
    degrades to a warning, never a die.
    """
    verbs: set[str] = set()
    in_section = False
    for line in help_text.splitlines():
        if not line.startswith((" ", "\t")):
            if line.strip():
                in_section = line.strip() == "Commands:"
            continue
        if not in_section:
            continue
        head = line.strip()
        for stop in ("[", "<", "("):
            idx = head.find(stop)
            if idx != -1:
                head = head[:idx]
        for alternative in head.split("|"):
            token = alternative.split()
            if token:
                verbs.add(token[0])
    return frozenset(verbs)


def parse_capabilities(blob: str) -> frozenset[str]:
    """Capability ids out of `cmux capabilities` JSON; empty on anything odd."""
    try:
        payload = json.loads(blob)
    except (ValueError, TypeError):
        return frozenset()
    if not isinstance(payload, dict):
        return frozenset()
    return frozenset(c for c in payload.get("capabilities") or () if isinstance(c, str))


@lru_cache(maxsize=1)
def probe() -> BackendProbe:
    """Probe the resolved backend once per process (cleared in tests).

    Returns an empty probe when the backend isn't cmux — limux and `none`
    already warn for themselves in `preflight`, and neither answers these
    questions.
    """
    from .cmux import cmux
    from .tool import is_cmux

    if not is_cmux():
        return BackendProbe(frozenset(), frozenset())
    verbs = parse_verbs(cmux("--help", check=False))
    capabilities = (
        parse_capabilities(cmux("capabilities", check=False))
        if "capabilities" in verbs
        else frozenset()
    )
    return BackendProbe(verbs, capabilities)


def has_capability(name: str) -> bool:
    """True when the resolved cmux negotiated `name`.

    The gate for cmux features built on top of the baseline — pair it with the
    existing `is_cmux()` check rather than replacing it.
    """
    return name in probe().capabilities
