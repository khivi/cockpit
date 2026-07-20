"""The running cockpit version, single-sourced from pyproject via package
metadata."""

from __future__ import annotations

from importlib import metadata


def running_version() -> str:
    """Installed package version (single-sourced from pyproject), or `""`."""
    try:
        return metadata.version("cockpit").strip()
    except (metadata.PackageNotFoundError, ValueError):
        return ""
