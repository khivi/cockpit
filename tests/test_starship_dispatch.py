"""Tests for cockpit/starship.py — the field dispatcher's statusline_hide gate.

The field printers themselves are covered in tests/lib/test_starship.py; here we
only exercise the `main` routing + the `statusline_hide` suppression.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import cockpit.starship as starship_cli


def test_hidden_field_prints_nothing(monkeypatch, capsys):
    monkeypatch.setattr(starship_cli, "statusline_hidden", lambda: {"cost"})
    monkeypatch.setattr(starship_cli, "print_cost", lambda: "💰 $9.99")
    assert starship_cli.main(["cockpit-starship", "cost"]) == 0
    assert capsys.readouterr().out == ""


def test_visible_field_still_prints(monkeypatch, capsys):
    monkeypatch.setattr(starship_cli, "statusline_hidden", lambda: {"cost"})
    monkeypatch.setattr(starship_cli, "print_model", lambda: "Opus 4.8")
    assert starship_cli.main(["cockpit-starship", "model"]) == 0
    assert capsys.readouterr().out == "Opus 4.8"


def test_warm_never_gated(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(starship_cli, "statusline_hidden", lambda: {"warm"})
    monkeypatch.setattr(
        starship_cli, "warm_all", lambda: calls.__setitem__("n", calls["n"] + 1)
    )
    assert starship_cli.main(["cockpit-starship", "warm"]) == 0
    assert calls["n"] == 1


# ── field dispatch table, read out of the source ────────────────────────────
#
# 16 of `main`'s 17 dispatch arms (every field but `warm`) are near-identical
# `if cmd == "<field>": return _emit(print_x())` statements. Hand-listing them
# here would silently drift from the real dispatch the moment a field is
# added, renamed, or fat-fingered; parsing them out of the source instead
# means the field list below is always exactly what `main` actually does.


def _dispatch_arms() -> list[tuple[str, str]]:
    """`(field, printer_name)` for every `cmd == "<field>": return
    _emit(print_x())` arm inside `main`'s try-block.

    Deliberately excludes the `statusline_hidden` guard (a `BoolOp`, not a
    `cmd == "..."` compare) and the `warm` arm (two statements, not one
    `return`) — neither has this shape.
    """
    source = Path(inspect.getsourcefile(starship_cli)).read_text()
    tree = ast.parse(source)
    main_func = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    try_node = next(node for node in ast.walk(main_func) if isinstance(node, ast.Try))
    arms: list[tuple[str, str]] = []
    for stmt in try_node.body:
        if not isinstance(stmt, ast.If):
            continue
        test = stmt.test
        if not (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "cmd"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
        ):
            continue  # the statusline_hidden guard: `cmd != "warm" and ...`
        field = test.comparators[0].value
        if len(stmt.body) != 1 or not isinstance(stmt.body[0], ast.Return):
            continue  # the `warm` arm: `warm_all(); return 0`
        call = stmt.body[0].value
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_emit"
        ):
            continue
        inner = call.args[0]
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
            arms.append((field, inner.func.id))
    return arms


_EXPECTED_FIELDS = [
    "context",
    "session-time",
    "rate-limit",
    "model",
    "cost",
    "permission-mode",
    "repo",
    "branch-identity",
    "worktree-status",
    "ticket",
    "pr-state",
    "pr-num",
    "pr-comments",
    "pr-checks",
    "pr-title",
    "pr-muted",
]

_DISPATCH_ARMS = _dispatch_arms()


def test_dispatch_arms_cover_every_documented_field():
    """Pins the dispatch table's shape: 16 `cmd == "<field>": return
    _emit(print_x())` arms, one per field the module docstring documents. A
    missing, renamed, or reordered arm here means a field silently stopped
    dispatching to its printer.
    """
    assert [field for field, _ in _DISPATCH_ARMS] == _EXPECTED_FIELDS


@pytest.mark.parametrize(
    "field,printer_name", _DISPATCH_ARMS, ids=[field for field, _ in _DISPATCH_ARMS]
)
def test_field_dispatches_to_its_printer_and_emits_return_value(
    monkeypatch, capsys, field, printer_name
):
    """Every field name dispatches to its matching printer, and that
    printer's return value is what reaches stdout — a typo in either half of
    the mapping renders an empty statusline segment with no error anywhere.
    """
    monkeypatch.setattr(starship_cli, "statusline_hidden", lambda: set())
    sentinel = f"<<{field}-sentinel>>"
    monkeypatch.setattr(starship_cli, printer_name, lambda: sentinel)
    assert starship_cli.main(["cockpit-starship", field]) == 0
    assert capsys.readouterr().out == sentinel


@pytest.mark.parametrize(
    "field,printer_name", _DISPATCH_ARMS, ids=[field for field, _ in _DISPATCH_ARMS]
)
def test_hidden_field_never_calls_its_printer(monkeypatch, capsys, field, printer_name):
    """A field listed in `statusline_hidden()` renders empty because the
    printer is never called at all — not because the printer happened to
    return an empty string.
    """
    calls = {"n": 0}

    def _tracked():
        calls["n"] += 1
        return "should never be seen"

    monkeypatch.setattr(starship_cli, "statusline_hidden", lambda: {field})
    monkeypatch.setattr(starship_cli, printer_name, _tracked)
    assert starship_cli.main(["cockpit-starship", field]) == 0
    assert capsys.readouterr().out == ""
    assert calls["n"] == 0


def test_warm_is_not_gated_by_statusline_hide(monkeypatch):
    """`warm` is not a field: hiding it via `statusline_hide` must not stop
    it running, since it's the synchronous PR-cache prewarm, not a printer.
    """
    calls = {"n": 0}
    monkeypatch.setattr(starship_cli, "statusline_hidden", lambda: {"warm"})
    monkeypatch.setattr(
        starship_cli, "warm_all", lambda: calls.__setitem__("n", calls["n"] + 1)
    )
    assert starship_cli.main(["cockpit-starship", "warm"]) == 0
    assert calls["n"] == 1


def test_warm_calls_warm_all_and_writes_nothing(monkeypatch, capsys):
    monkeypatch.setattr(starship_cli, "statusline_hidden", lambda: set())
    monkeypatch.setattr(starship_cli, "warm_all", lambda: None)
    assert starship_cli.main(["cockpit-starship", "warm"]) == 0
    assert capsys.readouterr().out == ""


def test_emit_writes_nothing_for_empty_string_but_returns_0(capsys):
    assert starship_cli._emit("") == 0
    assert capsys.readouterr().out == ""


def test_emit_writes_value_and_returns_0(capsys):
    assert starship_cli._emit("hello") == 0
    assert capsys.readouterr().out == "hello"


def test_unknown_command_returns_0_and_writes_nothing(monkeypatch, capsys):
    monkeypatch.setattr(starship_cli, "statusline_hidden", lambda: set())
    assert starship_cli.main(["cockpit-starship", "not-a-real-field"]) == 0
    assert capsys.readouterr().out == ""


def test_argv_too_short_returns_0(capsys):
    assert starship_cli.main(["cockpit-starship"]) == 0
    assert capsys.readouterr().out == ""


def test_printer_exception_is_swallowed_and_returns_0(monkeypatch, capsys):
    """THE load-bearing invariant: the whole dispatch is wrapped in
    `except Exception: return 0` because the statusline must never crash
    Claude Code. A printer raising an arbitrary exception must not
    propagate out of `main`.
    """
    monkeypatch.setattr(starship_cli, "statusline_hidden", lambda: set())

    def _boom():
        raise RuntimeError("printer blew up")

    monkeypatch.setattr(starship_cli, "print_cost", _boom)
    assert starship_cli.main(["cockpit-starship", "cost"]) == 0
    assert capsys.readouterr().out == ""


def test_warm_all_exception_is_also_swallowed(monkeypatch, capsys):
    monkeypatch.setattr(starship_cli, "statusline_hidden", lambda: set())

    def _boom():
        raise ValueError("warm blew up")

    monkeypatch.setattr(starship_cli, "warm_all", _boom)
    assert starship_cli.main(["cockpit-starship", "warm"]) == 0
    assert capsys.readouterr().out == ""
