#!/usr/bin/env python3
"""Pre-push hook: bump .claude-plugin/plugin.json version from conventional commits.

Idempotent: if the current version is already >= the expected bump, exit 0.
Otherwise rewrite plugin.json, create a `chore: bump version` commit, exit 1
so the user re-runs `git push` to include the new commit.

Base for the bump is HEAD~1's plugin.json. Branch ruleset enforces
rebase-to-main before merge, so HEAD~1 reflects the latest main state — no
remote fetch needed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import semver

PLUGIN_FILE = Path(".claude-plugin/plugin.json")
BUMP_COMMIT_PREFIX = "chore: bump version"


def run(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=check)
    return result.stdout.strip()


def bump_version(
    base: semver.Version, messages: list[str]
) -> tuple[semver.Version, str]:
    kind = "patch"
    for msg in messages:
        if msg.lower().startswith("break") or msg.startswith("BREAK"):
            kind = "major"
            break
        if msg.lower().startswith("feat"):
            kind = "minor"
    bumped = {
        "major": base.bump_major,
        "minor": base.bump_minor,
        "patch": base.bump_patch,
    }[kind]()
    return bumped, kind


def get_parent_version() -> semver.Version:
    try:
        raw = run("show", f"HEAD~1:{PLUGIN_FILE}", check=True)
        return semver.Version.parse(json.loads(raw)["version"])
    except subprocess.CalledProcessError:
        return semver.Version(0, 0, 0)


def get_bump_range() -> str:
    last_bump = run(
        "log",
        "--format=%H",
        f"--grep=^{re.escape(BUMP_COMMIT_PREFIX)}",
        "-1",
        check=False,
    )
    return f"{last_bump}..HEAD" if last_bump else "HEAD"


def write_version(new_version: str) -> None:
    text = PLUGIN_FILE.read_text()
    updated, n = re.subn(
        r'("version"\s*:\s*")[^"]+(")',
        rf"\g<1>{new_version}\g<2>",
        text,
        count=1,
    )
    if n != 1:
        sys.exit(f'version-bump: failed to locate "version" field in {PLUGIN_FILE}')
    PLUGIN_FILE.write_text(updated)


def main() -> int:
    if run("rev-parse", "--abbrev-ref", "HEAD") == "main":
        return 0

    current = semver.Version.parse(json.loads(PLUGIN_FILE.read_text())["version"])
    parent = get_parent_version()
    messages = run("log", get_bump_range(), "--format=%s").splitlines()
    expected, kind = bump_version(parent, messages)

    if current >= expected:
        return 0

    new_version = str(expected)
    write_version(new_version)
    run("add", str(PLUGIN_FILE))
    run("commit", "-m", f"{BUMP_COMMIT_PREFIX} to {new_version}")

    print(
        f"Bumped {current} -> {new_version} ({kind}). Re-run `git push`.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
