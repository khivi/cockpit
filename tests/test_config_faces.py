"""The config surface has three faces and they drift silently — this pins the
one nothing else covers.

`lib/config.py` is the authoritative reader; `config.example.json` and
`docs/config.md` are mirrors. The example is already held honest by
`test_preflight.py::test_config_example_passes_validation`, which runs it
through the real validators. `docs/config.md` had no test at all, so a field
added to a provider's `CONFIG_FIELDS` shipped documented or not depending on
whether the author remembered — and an undocumented field is worse than a
missing one, since the operator's only reference says it doesn't exist.

The assertion is deliberately weak: a field must *appear* as a table row, not
that its prose is right. Prose can't be checked and pretending otherwise would
make this a test people edit to pass.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cockpit.lib.tickets import _COMMON_CONFIG_FIELDS, _PROVIDER_CONFIG_FIELDS

CONFIG_DOC = Path(__file__).resolve().parent.parent / "docs" / "config.md"

# A field deliberately left out of the operator doc. Empty, and a new entry here
# needs a reason in this comment — it is not a place to park a field someone
# didn't get round to documenting.
UNDOCUMENTED_BY_DESIGN: frozenset[str] = frozenset()


def _documented_fields() -> set[str]:
    """The field names `docs/config.md` carries as table rows (`| \\`name\\` | …`)."""
    return set(re.findall(r"^\| `([\w.]+)`", CONFIG_DOC.read_text(), re.MULTILINE))


def _declared_fields() -> dict[str, str]:
    """`{field name: the provider declaring it}` across every ticket provider."""
    out = {name: "(common)" for name, _kind in _COMMON_CONFIG_FIELDS}
    for provider, fields in _PROVIDER_CONFIG_FIELDS.items():
        for name, _kind in fields:
            out.setdefault(name, provider)
    return out


def test_every_declared_ticket_field_is_documented():
    declared = _declared_fields()
    missing = sorted(
        f"{name} ({provider})"
        for name, provider in declared.items()
        if name not in _documented_fields() and name not in UNDOCUMENTED_BY_DESIGN
    )
    assert not missing, (
        f"declared in CONFIG_FIELDS but absent from docs/config.md: {missing}. "
        "A config change updates all three faces in the same PR — the reader, "
        "config.example.json, and the operator doc."
    )


def test_the_guard_would_catch_an_undocumented_field(monkeypatch):
    """Guard the guard: the regex has to actually miss a field that isn't there,
    or this passes forever on a doc it never really read."""
    monkeypatch.setitem(
        _PROVIDER_CONFIG_FIELDS, "trello", (("a_field_nobody_documented", "str"),)
    )
    with pytest.raises(AssertionError, match="a_field_nobody_documented"):
        test_every_declared_ticket_field_is_documented()


def test_the_doc_table_is_actually_being_parsed():
    """The other half of guarding the guard — a regex that matched nothing would
    make the test above vacuously true in the other direction."""
    documented = _documented_fields()
    assert len(documented) > 40
    # Spot-check one field per provider, so a table reformat that breaks the
    # regex fails here rather than silently disarming the real assertion.
    assert {"board", "label", "keys", "site_url", "inbox_states"} <= documented
