"""Tests for cockpit/starship.py — the field dispatcher's statusline_hide gate.

The field printers themselves are covered in tests/lib/test_starship.py; here we
only exercise the `main` routing + the `statusline_hide` suppression.
"""

from __future__ import annotations

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
