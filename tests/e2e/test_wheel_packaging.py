"""End-to-end guard that the built wheel ships the plugin manifests.

The runtime update check (`cockpit/lib/version.py`) reads
`parents[2]/.claude-plugin/{plugin.json,marketplace.json}`, which for the
installed wheel is `site-packages/.claude-plugin/`. The wheel packages only
`cockpit/` by default, so without the `force-include` in pyproject those files
are absent — `running_version()`/`install_repo()` fail and the daemon's
"update available" header is silently never shown. This builds the real wheel
and asserts both manifests are present, so a packaging regression fails CI.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_wheel_bundles_plugin_manifests(tmp_path):
    subprocess.run(
        ["uv", "build", "--wheel", "-o", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"
    names = set(zipfile.ZipFile(wheels[0]).namelist())
    assert ".claude-plugin/plugin.json" in names
    assert ".claude-plugin/marketplace.json" in names
