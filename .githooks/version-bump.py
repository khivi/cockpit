#!/usr/bin/env python3
"""Pre-push hook: ensure .claude-plugin/plugin.json has a patch bump vs HEAD~1.

Idempotent: if current version is already >= HEAD~1 + patch, exit 0.
Otherwise rewrite plugin.json, create a `chore: bump version` commit, exit 1
so the user re-runs `git push` to include the new commit.

Minor/major bumps are done by hand in plugin.json — the idempotency check
sees `current >= expected` and exits 0.

Base for the bump is HEAD~1's plugin.json. Branch ruleset enforces
rebase-to-main before merge, so HEAD~1 reflects the latest main state.
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


def get_parent_version() -> semver.Version:
    try:
        raw = run("show", f"HEAD~1:{PLUGIN_FILE}", check=True)
        return semver.Version.parse(json.loads(raw)["version"])
    except subprocess.CalledProcessError:
        return semver.Version(0, 0, 0)


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
    expected = get_parent_version().bump_patch()

    if current >= expected:
        return 0

    new_version = str(expected)
    write_version(new_version)
    run("add", str(PLUGIN_FILE))
    run("commit", "-m", f"{BUMP_COMMIT_PREFIX} to {new_version}")

    print(
        f"Bumped {current} -> {new_version}. Re-run `git push`.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
