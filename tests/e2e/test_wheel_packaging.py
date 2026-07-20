"""End-to-end guard on the built wheel's packaged contents.

The version is now static in `pyproject.toml` (cut as a `v<version>` git tag
at release), read at runtime via `importlib.metadata` — no manifest files are
bundled into the wheel for it. This builds the real wheel and asserts the
`cockpit` package + its version metadata are present, so a packaging
regression fails CI.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_wheel_bundles_cockpit_package(tmp_path):
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
    assert "cockpit/cli.py" in names
    assert any(n.endswith(".dist-info/METADATA") for n in names)
    # The Claude Code idle-pill hook shell script must ride along in the wheel —
    # `cockpit idle-pill <phase>` execs it, and a brew install has no plugin dir.
    assert "cockpit/hooks/cmux-idle-pill.sh" in names
