"""End-to-end test that a cold-spawned workspace really RECEIVES its seeded body.

`tests/lib/test_cmux.py` drives `deliver_followup` against a stubbed `cmux`,
which proves the function issues the calls we expect. Nothing there can notice
when those calls stop having the effect we expect — and that is not
hypothetical. The two-send `prompt_prefix` flow shipped in #211 gated its send
on `_claude_ready` (cmux reporting any `claude_code=` state) as a proxy for "the
composer will queue keystrokes". Measured against the real pair, cmux reports
that state ~2s after spawn, well before Claude Code takes input, so the body was
typed into a terminal that dropped it. `cmux send` exits 0 either way, so the
unit tests stayed green and every cold ticket spawn came up knowing only its
branch name for 79 days.

No mock could have caught it: the stub's `send` returns "" because the author
believed a send always lands, which is the exact belief under test. This module
is the other half — the same call against the real pair, with the session's own
transcript as the oracle.

WHAT THIS TEST DOES **NOT** DO, measured rather than assumed. It does not
reproduce that race on demand. Reverting `deliver_followup` to its pre-fix body
(one blind send, then Enter, then return True) and running this flow three times
gave 3/3 bodies delivered: on a warm machine Claude Code reaches a usable
composer in ~1s, so a send at ~2s lands anyway. The outage needed a slow boot —
a real repo with hooks, a large AGENTS.md, MCP connectors, several worktrees
spawning at once — and a test cannot manufacture that honestly.

So the division of labour is deliberate, and worth stating because the obvious
reading of this file is wrong. The *guarantee* — that `deliver_followup` reports
success only for a body it confirmed on screen — is a unit invariant, pinned by
`test_deliver_followup_retries_a_body_that_never_lands_then_refuses_to_submit`.
What THIS module pins is the half no unit test can reach: that the real cmux
still accepts a `send`/`send-key` pair, that Claude Code still takes synthesized
keystrokes, that `one_line` still yields one submission rather than several
truncated ones, and that a body typed this way genuinely lands in the session.
A green run here is evidence the pair still works, not evidence the race is
gone; correctness against the race comes from the fix's design, not from this.

WHAT IT COSTS, and why it is opt-in rather than merely `real_backend`-marked.
The other two e2e modules only *read* the live system (probe `cmux --help`,
render a footer inside an isolated HOME). This one spawns a real workspace into
your sidebar and starts a real Claude session, so it cannot ride along on the
routine `pytest -n auto`. It needs all three of:

    cmux installed · claude installed · COCKPIT_E2E_LIVE_DELIVERY=1

    COCKPIT_E2E_LIVE_DELIVERY=1 pytest tests/e2e/test_followup_delivery.py

Three things keep the blast radius at "one workspace, briefly":

  * The workspace cwd is a `tmp_path`, deliberately **outside every registered
    repo** — the same rule the stack-group anchor follows. `_reap_workspace_
    orphans` ignores workspaces outside every registered repo, so a running
    daemon cannot adopt, reap or tear down the one this test makes.
  * Teardown goes through `cmux_close_workspace_best_effort`, never a raw
    `cmux("close-workspace", ...)`, and runs in a `finally` so a failed
    assertion still closes the workspace.
  * `--model haiku` keeps a test run off the Opus window.

It does leave one artifact it cannot isolate: the spawned Claude writes its
transcript to the real `~/.claude/projects/<tmp-path-slug>/`, because it must be
the real authenticated binary. Reading that transcript is the whole point — it
is the one oracle *outside* cockpit's own view of the send.
"""

from __future__ import annotations

import os
import shutil
import time
from uuid import uuid4

import pytest

from cockpit.lib.cache import CLAUDE_PROJECTS_DIR, _claude_project_slug
from cockpit.lib.cmux import (
    cmux_close_workspace_best_effort,
    deliver_followup,
    spawn_workspace,
)

pytestmark = [
    # Execs the real binaries — that is this file's whole purpose. Opts out of
    # the suite-wide `_no_live_backend` guard in `tests/conftest.py`. One list
    # rather than a second `pytestmark =`, which silently replaces the first.
    pytest.mark.real_backend,
    pytest.mark.skipif(
        shutil.which("cmux") is None or shutil.which("claude") is None,
        reason="cmux or claude binary not installed",
    ),
    pytest.mark.skipif(
        os.environ.get("COCKPIT_E2E_LIVE_DELIVERY") != "1",
        reason=(
            "spawns a real workspace and a real Claude session; "
            "opt in with COCKPIT_E2E_LIVE_DELIVERY=1"
        ),
    ),
]

# The initial turn only has to make Claude boot with a turn in flight, which is
# the window the body used to vanish into. A trivial prompt reproduces the same
# cold-start timing as a `prompt_prefix` slash command for a fraction of the
# wall clock.
_INITIAL_PROMPT = "reply with the single word BOOTED"
_TRANSCRIPT_TIMEOUT_SECONDS = 180.0
_TRANSCRIPT_POLL_SECONDS = 2.0


def _transcript_blob(cwd) -> str:
    """Every transcript byte Claude Code has written for `cwd`.

    Read as raw text rather than parsed into messages: a long body arrives as
    `[Pasted text #N]` chunks and lands in a different content-block shape than
    a typed message, so a structured reader silently misses it — which cost an
    hour during the original diagnosis.
    """
    proj = CLAUDE_PROJECTS_DIR / _claude_project_slug(cwd)
    try:
        return "".join(
            p.read_text(errors="ignore") for p in sorted(proj.glob("*.jsonl"))
        )
    except OSError:
        return ""


def test_a_cold_spawned_workspace_actually_receives_its_followup(tmp_path):
    """The body reaches the session's transcript, not merely cmux's send.

    Asserting the PAIR is the point. `deliver_followup` returning True is
    cockpit's own opinion of the send; the nonce appearing in the transcript is
    the session's. Every unit test in `tests/lib/test_cmux.py` can only ever
    check the first, because the second lives in another process's file — and it
    was the gap between the two that stayed invisible for 81 releases.

    A failure here means the two disagree, which is one of: cmux changed `send`
    or `send-key`, Claude Code stopped accepting synthesized keystrokes, or
    `one_line` regressed and the body submitted as fragments. See the module
    docstring for what this deliberately does not cover.
    """
    nonce = uuid4().hex[:12]
    # The nonce sits inside the first 24 characters on purpose: that prefix is
    # what `deliver_followup` looks for on screen (`_FOLLOWUP_ECHO_PREFIX_CHARS`),
    # so a per-run nonce keeps a previous run's scrollback from satisfying the
    # echo check. Multi-line so the delivery also exercises `one_line` — an
    # un-collapsed body submits its first fragment as a truncated prompt.
    body = f"COCKPIT-E2E {nonce}\nreply with the single word ACK and nothing else"

    ws_cwd = tmp_path / "delivery"
    ws_cwd.mkdir()
    assert not _transcript_blob(ws_cwd), "tmp_path already has a Claude transcript"

    ref = spawn_workspace(
        f"cockpit-e2e-{nonce}",
        ws_cwd,
        f"claude --model haiku {_INITIAL_PROMPT!r}",
    )
    assert ref is not None, "cmux did not report a ref for the new workspace"

    try:
        # No sleep before this call. It will not reliably reproduce the race
        # (see the module docstring), but waiting for the session to settle
        # first would guarantee it never could.
        assert deliver_followup(ref, body) is True

        deadline = time.monotonic() + _TRANSCRIPT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if nonce in _transcript_blob(ws_cwd):
                break
            time.sleep(_TRANSCRIPT_POLL_SECONDS)
        else:
            pytest.fail(
                f"deliver_followup reported success but {nonce} never reached the "
                f"session transcript within {_TRANSCRIPT_TIMEOUT_SECONDS:.0f}s — "
                "the body was typed into a terminal that dropped it"
            )
    finally:
        cmux_close_workspace_best_effort(ref)
