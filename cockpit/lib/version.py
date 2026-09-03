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

from . import config

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
# The current dist name first, then the legacy name a stale editable install may
# still carry, then the source-tree fallback.
_DIST_NAMES = ("cmux-cockpit", "cockpit")

_SEEN_FILE = "last-seen-version"


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


def upgraded_version() -> str:
    """The running version if it CHANGED since the last run, else `""`.

    This is what a "what's new" prompt is allowed to be built on: it compares
    cockpit against its own last run, so it needs no network and never learns
    whether a *newer* release exists. An update check is still banned — this is
    the other question, asked entirely from local state.

    The marker is machine-local runtime state, so it lives in
    `COCKPIT_RUNTIME_DIR` beside the pidfile rather than under `COCKPIT_HOME`,
    which may be inside a synced folder: two machines on different versions
    would otherwise each read the other's stamp and prompt on every launch. The
    module attribute is read at call time, not bound at import, because test
    fixtures reload `lib.config` to re-derive that path.

    Two states deliberately return `""` rather than the version:

    - **No marker at all** — a first-ever run is an install, not an upgrade, and
      "here's what changed" is a lie on a version you have never run.
    - **An unresolvable version** — `running_version()` returns `""` from a
      source tree with no metadata and no readable `pyproject.toml`; stamping
      that would make the *next* run, on a real version, read as an upgrade.

    Every failure is swallowed: the marker is a nicety, and a read-only or
    missing runtime dir must not take the TUI down on startup. A failed write
    costs one repeated prompt, never a crash.
    """
    current = running_version()
    if not current:
        return ""

    marker = config.COCKPIT_RUNTIME_DIR / _SEEN_FILE
    try:
        previous = marker.read_text().strip()
    except OSError:
        previous = ""

    if previous == current:
        return ""

    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(current + "\n")
    except OSError:
        return ""

    return current if previous else ""
