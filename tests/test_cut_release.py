"""Guard tests for cut-release.sh.

Only the refusal paths are exercised — they all exit before the script reaches
`gh`, so the tests never touch the network or open a PR. The happy path ends in
`gh pr merge --admin` against the real repo and is deliberately untested.
"""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "cut-release.sh"


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


# Same list conftest.py's `repo` fixture scrubs. pre-commit exports GIT_DIR /
# GIT_INDEX_FILE while running hooks, and a set GIT_DIR beats `git -C <tmp>` —
# so without this every git call below (and every one cut-release.sh makes)
# retargets the developer's real checkout, bumping and committing its
# pyproject.toml. That is not hypothetical; it happened.
_GIT_ENV_LEAKS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clean repo on a feature branch with pyproject.toml at 1.5.0."""
    for var in _GIT_ENV_LEAKS:
        monkeypatch.delenv(var, raising=False)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "cockpit"\nversion = "1.5.0"\n'
    )
    _git(tmp_path, "add", "pyproject.toml")
    _git(tmp_path, "commit", "-qm", "init")
    _git(tmp_path, "switch", "-qc", "khivi/release")
    return tmp_path


def _version(repo: Path) -> str:
    import tomllib

    with open(repo / "pyproject.toml", "rb") as fh:
        return str(tomllib.load(fh)["project"]["version"])


def test_no_version_arg_is_usage_error(repo: Path) -> None:
    res = _run(repo)
    assert res.returncode == 2
    assert "usage:" in res.stderr


@pytest.mark.parametrize("bad", ["1.6", "v1.6.0", "1.6.0-rc1", "latest"])
def test_rejects_non_semver(repo: Path, bad: str) -> None:
    res = _run(repo, bad)
    assert res.returncode == 2
    assert "not a semver version" in res.stderr
    assert _version(repo) == "1.5.0"


def test_refuses_on_main(repo: Path) -> None:
    _git(repo, "switch", "-q", "main")
    res = _run(repo, "1.6.0")
    assert res.returncode == 2
    assert "cut a worktree first" in res.stderr
    assert _version(repo) == "1.5.0"


def test_refuses_dirty_tree(repo: Path) -> None:
    (repo / "stray.txt").write_text("wip\n")
    res = _run(repo, "1.6.0")
    assert res.returncode == 2
    assert "dirty" in res.stderr
    assert _version(repo) == "1.5.0"


def test_refuses_same_version(repo: Path) -> None:
    res = _run(repo, "1.5.0")
    assert res.returncode == 2
    assert "already at 1.5.0" in res.stderr


def test_bumps_then_aborts_at_the_prompt(repo: Path) -> None:
    """Declining the confirm leaves the bump in the tree, uncommitted."""
    res = subprocess.run(
        [str(SCRIPT), "1.6.0"],
        cwd=repo,
        input="n\n",
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "aborted" in res.stderr
    assert _version(repo) == "1.6.0"
    # Nothing was committed — the release commit is only made after the confirm.
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True
    )
    assert "chore(release)" not in log.stdout


def test_reverts_when_the_bump_does_not_take(repo: Path) -> None:
    """A pyproject with no version line must not leave a half-edited file."""
    (repo / "pyproject.toml").write_text('[project]\nname = "cockpit"\n')
    _git(repo, "commit", "-qam", "drop version")
    res = _run(repo, "1.6.0")
    assert res.returncode != 0
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
        == ""
    )
