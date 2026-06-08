"""Plugin version: the running version, the latest on the install repo's
default branch, and a comparator — for the slow-tick update check.

`running_version` reads the bundled `.claude-plugin/plugin.json`, falling back
to the installed package metadata. The daemon runs from the uv-tool install
(`cockpit` console script), NOT the plugin cache dir — the wheel bundles the
manifests into `site-packages/.claude-plugin/` via a hatch `force-include` (see
pyproject) so the file read resolves there; `importlib.metadata` is the
belt-and-suspenders fallback (the wheel version is single-sourced from the same
plugin.json at build time, so the two can't disagree). `latest_version` reads
plugin.json on the install source's default branch via `gh api`, which is the
marketplace's source of truth — the repo cuts no releases/tags, so main's
plugin.json version is what `/plugin install`/`/plugin update` resolves. Both
degrade to `""`/`None` on any error (network, auth, parse) so the slow-tick
check never raises.
"""

from __future__ import annotations

import json
import subprocess
from importlib import metadata
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parents[2] / ".claude-plugin"
_PLUGIN_JSON = _PLUGIN_DIR / "plugin.json"
_MARKETPLACE_JSON = _PLUGIN_DIR / "marketplace.json"


def _read_version(path: Path) -> str:
    try:
        return str(json.loads(path.read_text()).get("version", "")).strip()
    except (OSError, json.JSONDecodeError):
        return ""


def running_version() -> str:
    """Version string from the bundled plugin.json, falling back to the
    installed package metadata, or `""` if neither is available."""
    bundled = _read_version(_PLUGIN_JSON)
    if bundled:
        return bundled
    try:
        return metadata.version("cockpit").strip()
    except (metadata.PackageNotFoundError, ValueError):
        return ""


def install_repo() -> str | None:
    """`owner/name` the plugin installs from (marketplace source), or None.

    Read from marketplace.json's first plugin `source.repo` rather than
    hard-coded so a fork's manifest drives its own update check.
    """
    try:
        data = json.loads(_MARKETPLACE_JSON.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    for plugin in data.get("plugins", []):
        repo = plugin.get("source", {}).get("repo")
        if repo:
            return str(repo)
    return None


def latest_version() -> str | None:
    """Version on the install repo's default branch, or None on any failure.

    Reads `.claude-plugin/plugin.json` raw via `gh api repos/{repo}/contents/...`
    with the `raw` media type (no base64 hop). Network/auth/parse failures all
    return None so the caller logs nothing.
    """
    repo = install_repo()
    if not repo:
        return None
    try:
        out = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repo}/contents/.claude-plugin/plugin.json",
                "-H",
                "Accept: application/vnd.github.raw",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    try:
        return str(json.loads(out).get("version", "")).strip() or None
    except (ValueError, json.JSONDecodeError):
        return None


def _parse(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in v.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    """True iff `candidate` is a strictly higher dotted version than `current`.

    Empty/unparsable inputs are never "newer" — a failed fetch must not
    trigger a spurious update notice.
    """
    if not candidate or not current:
        return False
    return _parse(candidate) > _parse(current)
