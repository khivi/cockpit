#!/usr/bin/env bash
# Cut a release: bump [project] version in pyproject.toml, open the
# `chore(release): <version>` PR, and merge it.
#
# MANUAL FALLBACK. The normal path is release-please, which keeps a rolling
# release PR open — merging it does all of this for you. Reach for this script
# when that action is broken or a version has to be forced, and update
# .release-please-manifest.json to match afterwards or release-please will
# propose its next bump from the stale baseline.
#
# That merge is the whole trigger — tag.yml sees pyproject.toml change on main
# and pushes `v<version>`, which fires release.yml (Homebrew tap bump) and
# publish.yml (PyPI). Nothing here tags anything. See AGENTS.md "Release
# versioning".
#
# Run it from a worktree on a feature branch (`cockpit new`), never on main:
# `main` is protected and the repo works one-worktree-per-branch.
#
#   ./cut-release.sh 1.6.0        # confirms before merging
#   ./cut-release.sh 1.6.0 -y     # no prompt
set -euo pipefail

version="${1-}"
assume_yes="${2-}"

if [ -z "$version" ]; then
  echo "usage: ./cut-release.sh <version> [-y]   (e.g. ./cut-release.sh 1.6.0)" >&2
  exit 2
fi
if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "not a semver version: $version" >&2
  exit 2
fi

cd "$(git rev-parse --show-toplevel)"

branch="$(git rev-parse --abbrev-ref HEAD)"
case "$branch" in
  main | master)
    echo "on $branch — cut a worktree first (cockpit new), then re-run here" >&2
    exit 2
    ;;
esac

if [ -n "$(git status --porcelain)" ]; then
  echo "working tree is dirty — commit or stash first" >&2
  exit 2
fi

read_version() {
  python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])'
}

current="$(read_version)"
if [ "$current" = "$version" ]; then
  echo "pyproject.toml is already at $version" >&2
  exit 2
fi

python3 - "$version" <<'PY'
import re
import sys

target = sys.argv[1]
src = open("pyproject.toml").read()
# Only the first top-level `version = "..."` — [project] declares it before any
# other table, and the tomllib re-read below is the real guard against a miss.
out, n = re.subn(r'(?m)^(version\s*=\s*")[^"]+(")', rf"\g<1>{target}\g<2>", src, count=1)
if n != 1:
    sys.exit("could not find a version line in pyproject.toml")
open("pyproject.toml", "w").write(out)
PY

if [ "$(read_version)" != "$version" ]; then
  echo "bump did not take — pyproject.toml [project] version is not $version" >&2
  git checkout -- pyproject.toml
  exit 1
fi
echo "pyproject.toml: $current -> $version"

if [ "$assume_yes" != "-y" ]; then
  read -r -p "open + merge the release PR for $version? [y/N] " reply
  case "$reply" in
    [yY]) ;;
    *)
      echo "aborted — bump left uncommitted" >&2
      exit 1
      ;;
  esac
fi

git add pyproject.toml
git commit -q -m "chore(release): $version"
git push -q -u origin HEAD

# --admin: main's ruleset requires an approving review, which a solo author
# can't give their own PR. See AGENTS.md "Commit / PR-title convention".
gh pr create --base main \
  --title "chore(release): $version" \
  --body "Version bump only, $current -> $version.

On merge, \`tag.yml\` pushes \`v$version\`, which fires \`release.yml\` (Homebrew tap) and \`publish.yml\` (PyPI)."
gh pr merge --squash --admin

echo "merged — watch the tag land: gh run list --workflow tag.yml"
