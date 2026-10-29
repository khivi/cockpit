"""`scripts/check_doc_cli_drift.py`: docs name only subcommands and flags the CLI accepts."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_doc_cli_drift.py"
_spec = importlib.util.spec_from_file_location("check_doc_cli_drift", SCRIPT)
assert _spec and _spec.loader
drift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drift)


def test_real_docs_have_no_cli_drift():
    assert drift.main([]) == 0


def test_flags_unknown_subcommands_and_flags(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text(
        "Run `cockpit new --pr 5`, `cockpit nudge mute --until 2h`, `cockpit --version`.\n"
        "Not `cockpit frobnicate` or `cockpit new --no-such-flag`.\n"
        "```bash\n"
        "cockpit nudge snooze --until 2h\n"
        "cockpit new feat -- 'free text --ignored'\n"
        "```\n"
        "Prose about cockpit new --unquoted is not read.\n"
    )
    errors = drift.check_file(doc, drift.parsers())
    assert errors == [
        f"{doc}:2: unknown subcommand 'frobnicate'",
        f"{doc}:2: --no-such-flag is not accepted by `cockpit new`",
        f"{doc}:4: --until is not accepted by `cockpit nudge snooze`",
    ]
