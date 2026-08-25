"""Guard tests for dev.sh.

The happy path ends in `exec uv run cockpit watch`, a Textual TUI — deliberately
untested, same precedent as tests/test_cut_release.py. What is tested is the
isolation contract, because every one of these failing is silent: the sandbox
looks fine and the damage lands in your real state.

`--` + a non-`watch` subcommand is the seam these use: the script does all its
seeding before `exec`, so `-- --version` exercises the whole sandbox build and
then exits instead of opening a TUI. The assertions read the seeded files
rather than the exit status, since the `exec uv run` tail resolves an
environment the test does not control.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "dev.sh"

# pre-commit exports GIT_DIR / GIT_INDEX_FILE while running hooks, and a set
# GIT_DIR beats `git -C <tmp>` — so without scrubbing these, dev.sh's
# `git rev-parse --show-toplevel` retargets the developer's real checkout and
# `rm -rf .cockpit-dev` runs there. Same list tests/test_cut_release.py scrubs,
# and for the same reason: it happened.
_GIT_ENV_LEAKS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def fake_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A git repo to run dev.sh in, plus a fake real COCKPIT_HOME to snapshot.

    The real home carries one repo entry with every write-enabling flag turned
    on, so the scrub assertions below have something to actually strip.
    """
    repo = tmp_path / "worktree"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "dev.sh").write_bytes(SCRIPT.read_bytes())
    os.chmod(repo / "dev.sh", 0o755)

    real_home = tmp_path / "real-cockpit-home"
    (real_home / "cache").mkdir(parents=True)
    (real_home / "config.json").write_text(
        json.dumps(
            {
                "tool": "cmux",
                "skills": {"plan": "/plan"},
                "repos": [
                    {
                        "name": "testrepo",
                        "path": str(repo),
                        "review_prs": True,
                        "dependabot": True,
                        "review_external": True,
                        "tickets": {
                            "provider": "linear",
                            "dev_done": "Dev Done",
                            "close_on_merge": True,
                            "start_label": "started",
                            "merge_done": "Done",
                        },
                    }
                ],
            }
        )
    )
    (real_home / "cache" / "testrepo__pr-7.json").write_text('{"number": 7}')
    (real_home / "cache" / "nudges").mkdir()
    (real_home / "cache" / "nudges" / "testrepo__7.json").write_text('{"muted": true}')
    return repo, real_home


def _run(repo: Path, real_home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_LEAKS}
    env["COCKPIT_HOME"] = str(real_home)
    return subprocess.run(
        ["./dev.sh", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def _sandbox_config(repo: Path) -> dict:
    return json.loads((repo / ".cockpit-dev" / "config.json").read_text())


def test_refuses_setup(fake_repo):
    """`cockpit setup` bakes this worktree's .venv/bin/python into
    ~/.claude/settings.json and ~/.config/starship.toml — both OUTSIDE the
    sandbox, so COCKPIT_HOME does not contain the damage. It is the one
    irreversible mistake here (the "footer disappeared" bug), so it must be
    refused rather than merely not-suggested."""
    repo, real_home = fake_repo
    res = _run(repo, real_home, "--", "setup")
    assert res.returncode == 2
    assert "refusing to run" in res.stderr
    # Refused before seeding: nothing was written, so a refusal cannot be
    # mistaken for a sandbox that is ready to use.
    assert not (repo / ".cockpit-dev").exists()


def test_sandbox_forces_tool_none(fake_repo):
    """`tool: none` is what makes every cmux write a no-op — spawn, close,
    rename, set-color, workspace-group, and `send` into a live Claude session.
    The real config says cmux; the sandbox must override it, not inherit it."""
    repo, real_home = fake_repo
    _run(repo, real_home, "--", "--version")
    assert _sandbox_config(repo)["tool"] == "none"


def test_snapshot_scrubs_write_enabling_config(fake_repo):
    """--dry already gates these, but they are the irreversible class (a closed
    ticket, a labelled issue, a review agent spawned onto someone's PR) and the
    scrub also covers `./dev.sh -- <subcommand>` runs, which never go near the
    daemon's dry flag at all."""
    repo, real_home = fake_repo
    _run(repo, real_home, "--", "--version")
    cfg = _sandbox_config(repo)
    entry = cfg["repos"][0]

    assert entry["review_prs"] is False
    assert entry["dependabot"] is False
    assert entry["review_external"] is False
    assert "close_on_merge" not in entry["tickets"]
    assert "start_label" not in entry["tickets"]
    assert "merge_done" not in entry["tickets"]
    # Read-only values survive — the scrub targets writes, not fidelity. Strip
    # these and the sandbox stops rendering what it is supposed to show.
    assert entry["tickets"]["provider"] == "linear"
    assert entry["tickets"]["dev_done"] == "Dev Done"
    # Seeded first turns shell out to `claude -p`; a sandbox must spawn no agents.
    assert "skills" not in cfg
    # The repo itself still has to survive the scrub, or the table is empty and
    # snapshot mode is pointless.
    assert entry["name"] == "testrepo"


def test_snapshot_copies_pr_cache_but_not_nudge_prefs(fake_repo):
    """PR snapshots are what let the table render under --dry (which suppresses
    every cache write). Nudge prefs are excluded on purpose: a mute or snooze is
    a real decision about a real PR and a dev build has no business rewriting
    one."""
    repo, real_home = fake_repo
    _run(repo, real_home, "--", "--version")
    cache = repo / ".cockpit-dev" / "cache"

    assert (cache / "testrepo__pr-7.json").exists()
    assert not (cache / "nudges").exists()


def test_empty_mode_takes_no_repos_from_the_real_config(fake_repo):
    repo, real_home = fake_repo
    _run(repo, real_home, "--empty", "--", "--version")
    cfg = _sandbox_config(repo)

    assert cfg["repos"] == []
    assert cfg["tool"] == "none"
    assert not (repo / ".cockpit-dev" / "cache" / "testrepo__pr-7.json").exists()


def test_reseeds_from_scratch_each_run(fake_repo):
    """A snapshot drifts as soon as the real daemon ticks. Worse, a leftover
    file from a previous run is indistinguishable from live state, so stale
    rows read as a rendering bug in whatever you are working on."""
    repo, real_home = fake_repo
    _run(repo, real_home, "--", "--version")
    stale = repo / ".cockpit-dev" / "cache" / "testrepo__pr-999.json"
    stale.write_text('{"number": 999}')

    _run(repo, real_home, "--", "--version")
    assert not stale.exists()


def test_sandbox_isolates_the_runtime_dir_separately(fake_repo):
    """The pidfile and close-request queue do NOT follow COCKPIT_HOME — that is
    the point of the split, since COCKPIT_HOME is often synced and these are
    machine-local. So isolating COCKPIT_HOME alone would still leave the dev
    build claiming the installed daemon's pidfile and draining its real
    teardown queue."""
    repo, real_home = fake_repo
    res = _run(repo, real_home, "--", "--version")
    env_lines = dict(
        ln.removeprefix("dev.sh: ").split("=", 1)
        for ln in res.stdout.splitlines()
        if ln.startswith("dev.sh: COCKPIT_")
    )

    assert env_lines["COCKPIT_RUNTIME_DIR"] == str(repo / ".cockpit-dev" / "runtime")
    assert env_lines["COCKPIT_RUNTIME_DIR"] != env_lines["COCKPIT_HOME"]
    assert (repo / ".cockpit-dev" / "runtime").is_dir()


def test_sandbox_home_is_never_the_real_one(fake_repo):
    """The failure this whole script exists to prevent. A shared COCKPIT_HOME
    means the dev build fights the installed daemon for the pidfile and drains
    its close-request queue — tearing down real worktrees."""
    repo, real_home = fake_repo
    res = _run(repo, real_home, "--", "--version")
    reported = [
        ln.split("=", 1)[1]
        for ln in res.stdout.splitlines()
        if ln.startswith("dev.sh: COCKPIT_HOME=")
    ]

    assert reported == [str(repo / ".cockpit-dev")]
    assert not (real_home / "cockpit.pid").exists()
