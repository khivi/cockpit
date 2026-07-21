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


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the real wheel once and share it across the packaging tests."""
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run(
        ["uv", "build", "--wheel", "-o", str(out)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"
    return wheels[0]


def test_wheel_bundles_cockpit_package(built_wheel):
    # PyPI distribution is `cmux-cockpit` (normalized to `cmux_cockpit` in the
    # wheel filename); the import package + console script stay `cockpit`.
    assert built_wheel.name.startswith("cmux_cockpit-"), built_wheel.name
    names = set(zipfile.ZipFile(built_wheel).namelist())
    assert "cockpit/cli.py" in names
    assert any(n.endswith(".dist-info/METADATA") for n in names)
    # The Claude Code idle-pill hook shell script must ride along in the wheel —
    # `cockpit idle-pill <phase>` execs it, and a brew install has no plugin dir.
    assert "cockpit/hooks/cmux-idle-pill.sh" in names
    # Same for the bundled `/cockpit-new` + `/cockpit-close` user-command
    # templates — `cockpit setup` reads them via importlib.resources at
    # install time, so they must ship in the wheel too.
    assert "cockpit/claude_commands/cockpit-new.md" in names
    assert "cockpit/claude_commands/cockpit-close.md" in names


def test_installed_wheel_console_script_runs(built_wheel, tmp_path):
    """Install the wheel into a clean venv and run the console script.

    The namelist assertions above prove files *ride in* the wheel; this proves
    the *installed* dist actually runs — the one failure mode unit tests (which
    import from the source tree via `uv run`) can't see: a missing runtime
    dependency, a broken `[project.scripts]` entry point, or an
    `importlib.resources` lookup that only resolves against the source layout.
    This is the bug class the brew/pip distribution introduces.
    """
    venv = tmp_path / "venv"
    subprocess.run(
        ["uv", "venv", str(venv)], check=True, capture_output=True, text=True
    )
    py = venv / "bin" / "python"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(py), str(built_wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    cockpit = venv / "bin" / "cockpit"
    assert cockpit.exists(), "console script not installed"

    # `--version` imports cockpit.lib.version → importlib.metadata: proves the
    # dist metadata resolves under the installed name (cmux-cockpit).
    ver = subprocess.run(
        [str(cockpit), "--version"], check=True, capture_output=True, text=True
    )
    assert ver.stdout.strip().startswith("cockpit "), ver.stdout

    # `new --help` pulls the full spawn import graph (config/git/gh/cmux +
    # templates via importlib.resources) then exits 0 on argparse --help before
    # any network/preflight — a real import smoke of the packaged resources.
    helped = subprocess.run(
        [str(cockpit), "new", "--help"], check=True, capture_output=True, text=True
    )
    assert "usage:" in helped.stdout.lower()
