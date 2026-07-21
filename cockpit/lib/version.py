"""The running cockpit version.

Single-sourced from `pyproject.toml`'s static `version`. For an installed
build (brew/PyPI/wheel) that value is baked into the package metadata, so
`importlib.metadata` resolves it — the PyPI *distribution* is `cmux-cockpit`
(the bare name `cockpit` collides with Red Hat's Cockpit), while the import
package + console script stay `cockpit`. When cockpit runs from a source
checkout that was never installed (a dev `python -m cockpit.cli`, or pytest in
an isolated venv that doesn't install the package), there is no metadata — fall
back to reading `pyproject.toml` from the source tree.
"""

from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
# The current dist name first, then the legacy name a stale editable install may
# still carry, then the source-tree fallback.
_DIST_NAMES = ("cmux-cockpit", "cockpit")


def running_version() -> str:
    """Cockpit's version string, or `""` if no source resolves."""
    for dist in _DIST_NAMES:
        try:
            return metadata.version(dist).strip()
        except (metadata.PackageNotFoundError, ValueError):
            continue
    try:
        data = tomllib.loads(_PYPROJECT.read_text())
        return str(data["project"]["version"]).strip()
    except (OSError, tomllib.TOMLDecodeError, KeyError):
        return ""
