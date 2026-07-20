"""The running cockpit version.

Single-sourced from `pyproject.toml`'s static `version`. For an installed
build (brew/wheel) that value is baked into the package metadata, so
`importlib.metadata` resolves it. When cockpit runs from a source checkout
that was never installed (a dev `python -m cockpit.cli`, or pytest in an
isolated venv that doesn't install the package), there is no metadata — fall
back to reading `pyproject.toml` from the source tree.
"""

from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def running_version() -> str:
    """Cockpit's version string, or `""` if neither source resolves."""
    try:
        return metadata.version("cockpit").strip()
    except (metadata.PackageNotFoundError, ValueError):
        pass
    try:
        data = tomllib.loads(_PYPROJECT.read_text())
        return str(data["project"]["version"]).strip()
    except (OSError, tomllib.TOMLDecodeError, KeyError):
        return ""
