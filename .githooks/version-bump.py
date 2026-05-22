#!/usr/bin/env python3
"""Pre-push hook: ensure current plugin.json version > default branch's version.

Idempotent: if current > default-branch version (by any amount), exit 0.
Otherwise rewrite plugin.json with default+patch, create a `chore: bump
version` commit, exit 1 so the user re-runs `git push`.

Default branch is resolved from `origin/HEAD` (not hardcoded). Read is local
(no network). Branch ruleset enforces rebase-to-main before merge, so the
cached ref is current at PR-merge time.

Minor/major bumps are done by hand in plugin.json — any value > default
passes.
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


def get_default_branch_ref() -> str:
    return run("rev-parse", "--abbrev-ref", "origin/HEAD")


def get_default_branch_name() -> str:
    return get_default_branch_ref().split("/", 1)[1]


def get_main_version() -> semver.Version:
    try:
        raw = run("show", f"{get_default_branch_ref()}:{PLUGIN_FILE}", check=True)
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
    if run("rev-parse", "--abbrev-ref", "HEAD") == get_default_branch_name():
        return 0

    current = semver.Version.parse(json.loads(PLUGIN_FILE.read_text())["version"])
    main_version = get_main_version()

    if current > main_version:
        return 0

    new_version = str(main_version.bump_patch())
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
