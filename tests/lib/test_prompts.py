"""Tests for cockpit/lib/prompts.py prompt-prefix splitting + command build.

`split_prompt_prefix` decides whether a configured `prompt_prefix` slash
command runs as its own initial turn with the task body delivered separately
(two sends) vs. a plain single send when no prefix is set. `claude_command`
quotes the *initial* half into a `claude '<prompt>'` shell command.
"""

from __future__ import annotations

import pytest

import cockpit.lib.prompts as prompts
from cockpit.lib.prompts import claude_command, split_prompt_prefix


@pytest.fixture
def prefix(monkeypatch):
    """Set the configured `prompt_prefix` for a test (default: none)."""

    def _set(value: str) -> None:
        monkeypatch.setattr(prompts, "prompt_prefix", lambda: value)

    _set("")
    return _set


def test_split_no_prefix_body_only(prefix):
    # No prefix → body rides in as the initial command, no follow-up.
    assert split_prompt_prefix("do the task") == ("do the task", None)


def test_split_no_prefix_no_body(prefix):
    assert split_prompt_prefix(None) == (None, None)


def test_split_prefix_and_body_two_sends(prefix):
    prefix("/session-coordination")
    initial, followup = split_prompt_prefix("do the task")
    # Prefix alone first; body delivered separately — never concatenated.
    assert initial == "/session-coordination"
    assert followup == "do the task"


def test_split_prefix_only(prefix):
    prefix("/session-coordination")
    assert split_prompt_prefix(None) == ("/session-coordination", None)


def test_split_prefix_never_embeds_body(prefix):
    """Regression guard: the body must NOT be folded onto the prefix line —
    that collapse (the old `f'{prefix}\\n\\n{body}'`) is exactly what the
    two-send flow replaced."""
    prefix("/session-coordination")
    initial, _ = split_prompt_prefix("the task body")
    assert initial is not None
    assert "the task body" not in initial


def test_claude_command_quotes_prompt():
    assert claude_command("hi there") == "claude 'hi there'"


def test_claude_command_escapes_single_quotes():
    assert claude_command("it's fine") == "claude 'it'\\''s fine'"


def test_claude_command_none_is_bare_claude():
    assert claude_command(None) == "claude"
