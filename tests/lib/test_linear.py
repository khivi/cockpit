"""Tests for scripts/lib/linear.py — regex + extract_ticket.

No network surface here: Linear ticket bodies are fetched by Claude via the
Linear MCP from the spawned workspace, not by cockpit. See `test_spawn.py`
for the spawn-side dispatch (which only inspects the id).
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from scripts.lib.linear import (
    LINEAR_RE,
    LINEAR_RE_CI,
    extract_ticket,
    linear_mcp_available,
)


def test_linear_re_matches_uppercase_only():
    assert LINEAR_RE.search("khivi/PE-1234-foo")
    assert not LINEAR_RE.search("khivi/pe-1234-foo")


def test_linear_re_ci_matches_either_case():
    assert LINEAR_RE_CI.fullmatch("PE-1234")
    assert LINEAR_RE_CI.fullmatch("pe-1234")
    assert LINEAR_RE_CI.fullmatch("EnG-99")


def test_linear_re_ci_rejects_out_of_bound_prefix():
    assert not LINEAR_RE_CI.fullmatch("TOOLONG-1")  # 7-char prefix
    assert not LINEAR_RE_CI.fullmatch("A-1")  # 1-char prefix


def test_extract_ticket_returns_first_match():
    assert extract_ticket("khivi/PE-1234-add-foo") == "PE-1234"


def test_extract_ticket_handles_lowercase_prefix():
    # Linear generates branch names with lowercase prefixes (e.g. pe-1234)
    assert extract_ticket("khivi/pe-1234-add-foo") == "PE-1234"


def test_extract_ticket_double_ticket_returns_first():
    # Branch names like pe-4547-pe-4176-foo contain two ticket ids; return the first
    assert extract_ticket("khivi/pe-4547-pe-4176-async-lifecycle-follow") == "PE-4547"


def test_extract_ticket_empty_returns_empty():
    assert extract_ticket("") == ""
    assert extract_ticket("khivi/no-ticket") == ""


# ────────────────────────────────────────────────────────────────────────────
# linear_mcp_available — pre-flight against `claude mcp list`
# ────────────────────────────────────────────────────────────────────────────


def _fake_completed(
    stdout: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude", "mcp", "list"], returncode=returncode, stdout=stdout, stderr=""
    )


def test_linear_mcp_available_returns_none_when_claude_missing():
    """No `claude` on PATH → FileNotFoundError → None (can't tell)."""
    with patch("scripts.lib.linear.subprocess.run", side_effect=FileNotFoundError):
        assert linear_mcp_available() is None


def test_linear_mcp_available_returns_none_on_timeout():
    with patch(
        "scripts.lib.linear.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=3),
    ):
        assert linear_mcp_available() is None


def test_linear_mcp_available_returns_none_on_nonzero_exit():
    """`claude mcp list` ran but failed → can't tell → None."""
    with patch(
        "scripts.lib.linear.subprocess.run",
        return_value=_fake_completed(stdout="", returncode=1),
    ):
        assert linear_mcp_available() is None


def test_linear_mcp_available_true_when_output_contains_linear():
    with patch(
        "scripts.lib.linear.subprocess.run",
        return_value=_fake_completed(
            stdout="linear: https://mcp.linear.app/sse (HTTP)\n",
        ),
    ):
        assert linear_mcp_available() is True


def test_linear_mcp_available_case_insensitive():
    with patch(
        "scripts.lib.linear.subprocess.run",
        return_value=_fake_completed(stdout="LINEAR Connector enabled\n"),
    ):
        assert linear_mcp_available() is True


def test_linear_mcp_available_false_when_no_linear_entry():
    with patch(
        "scripts.lib.linear.subprocess.run",
        return_value=_fake_completed(stdout="github: gh-stuff\nfilesystem: fs-thing\n"),
    ):
        assert linear_mcp_available() is False


def test_linear_mcp_available_false_on_empty_output():
    with patch(
        "scripts.lib.linear.subprocess.run",
        return_value=_fake_completed(stdout=""),
    ):
        assert linear_mcp_available() is False


def test_linear_mcp_available_returns_none_on_oserror():
    with patch(
        "scripts.lib.linear.subprocess.run",
        side_effect=OSError("permission denied"),
    ):
        assert linear_mcp_available() is None


def test_linear_mcp_available_uses_bumped_timeout():
    """The pre-flight budget must outlast a managed-connector handshake
    (~6s typical, 30s+ under load) so a slow-but-connecting Linear MCP yields
    a definitive answer instead of timing out at the old 3s budget."""
    with patch(
        "scripts.lib.linear.subprocess.run",
        return_value=_fake_completed(stdout="linear: ...\n"),
    ) as run:
        linear_mcp_available()
    assert run.call_args.kwargs["timeout"] >= 15
