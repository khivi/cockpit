"""Tests for scripts/lib/linear.py — regex + extract_ticket.

No network surface here: Linear ticket bodies are fetched by Claude via the
Linear MCP from the spawned workspace, not by cockpit. See `test_spawn.py`
for the spawn-side dispatch (which only inspects the id).
"""

from __future__ import annotations

from scripts.lib.linear import LINEAR_RE, LINEAR_RE_CI, extract_ticket


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


def test_extract_ticket_empty_returns_empty():
    assert extract_ticket("") == ""
    assert extract_ticket("khivi/no-ticket") == ""
