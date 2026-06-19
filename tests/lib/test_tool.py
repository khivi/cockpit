"""Tests for cockpit/lib/tool.py — workspace-backend policy predicates."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cockpit.lib import tool


@pytest.mark.parametrize(
    "backend,expected",
    [("cmux", True), ("limux", True), ("none", False)],
)
def test_has_workspace_backend(backend, expected):
    """A workspace tool (cmux or limux) is present iff the backend isn't 'none'.

    This is the gate that lets worktree teardown (autoclose, branch-ref reap,
    close-request drain) run on limux, not just cmux — pills stay `is_cmux`-only.
    """
    with patch("cockpit.lib.tool.resolve_tool", return_value=backend):
        assert tool.has_workspace_backend() is expected
