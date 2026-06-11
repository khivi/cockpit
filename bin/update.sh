#!/usr/bin/env bash
# First-time bootstrap for cockpit. This exists only for the case where the
# `cockpit` command is NOT yet on PATH (a fresh `/plugin install`): it
# bootstraps `uv` if missing, installs the `cockpit` command from this checkout,
# then hands the rest of the flow (marketplace/plugin refresh + statusLine
# setup) off to the in-wheel Python updater (`cockpit/lib/updater.py`).
#
# Once installed, update with `cockpit update` (or the TUI's `u` key) — both run
# the same Python updater; no shell is involved. `cockpit update --check`
# reports availability (exit 10 = update available, 0 = current).
set -euo pipefail

# Resolve repo_root from the script's real location, following any symlinks
# (e.g. ~/bin/update.sh -> the installed plugin dir). Plain `readlink` in a loop
# is portable (BSD/macOS lacks `readlink -f`); the loop is a no-op for a regular
# file, so this stays correct whether or not a symlink is involved.
_src="${BASH_SOURCE[0]}"
while [ -h "${_src}" ]; do
  _dir="$(cd -P "$(dirname "${_src}")" && pwd)"
  _src="$(readlink "${_src}")"
  case "${_src}" in /*) ;; *) _src="${_dir}/${_src}" ;; esac
done
repo_root="$(cd -P "$(dirname "${_src}")/.." && pwd)"

# Ensure uv is present — bootstrap it on a first-time install.
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found — installing it..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv installed but 'uv' is not on PATH — open a new shell and re-run ${repo_root}/bin/update.sh." >&2
  exit 1
fi

# Install the cockpit command from this checkout. --no-cache is load-bearing,
# not belt-and-suspenders: the wheel version is read at build time from
# .claude-plugin/plugin.json (hatch dynamic version), but uv keys its build
# cache on the source *path*, not that file's contents. A version-only bump
# therefore leaves the cache key unchanged, so a plain `--force` reinstall
# rebuilds nothing and re-serves the stale wheel. --no-cache forces a rebuild.
echo "installing the cockpit command from ${repo_root}..."
uv tool install --force --no-cache "${repo_root}"

# Hand off the rest to the freshly-installed Python updater. --skip-install: the
# uv install above already placed this version, so the updater must not reinstall
# (which would redirect to the newest cached dir and could differ from what we
# just bootstrapped). It does the marketplace/plugin refresh + `cockpit setup`.
if command -v cockpit >/dev/null 2>&1; then
  exec cockpit update --skip-install
fi

echo "error: uv tool install ran but 'cockpit' is not on PATH — open a new shell and re-run ${repo_root}/bin/update.sh." >&2
exit 1
