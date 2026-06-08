#!/usr/bin/env bash
# Install or update cockpit end-to-end. Safe for a first-time install or an
# upgrade — re-runnable. Steps:
#   1. ensure `uv` is on PATH (bootstrap it if missing)
#   2. refresh the Claude Code marketplace + plugin via the `claude` CLI
#   3. (re)install the `cockpit` command from this source via `uv`
# Restart Claude Code and `cockpit watch` afterwards to apply (plugin updates
# require a restart). On a first-time install the plugin refresh in step 2 is a
# no-op warning (the plugin isn't added yet) — add it from inside Claude Code
# with /plugin; the `uv` step is what puts the `cockpit` command on PATH.
#
# `--check`: compare the running version against the latest on the install repo
# and report, WITHOUT updating. Exits 0 when up to date, 10 when an update is
# available, 1 when the check can't run. This is the same comparison the TUI's
# header indicator uses, exposed for scripts.
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

if [ "${1:-}" = "--check" ]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found — can't run the version check. Run this plugin dir's bin/update.sh (${repo_root}/bin/update.sh) to install." >&2
    exit 1
  fi
  uv run --project "${repo_root}" python - <<'PY'
import sys

from cockpit.lib import version

running = version.running_version()
latest = version.latest_version()
if latest and version.is_newer(latest, running):
    print(f"update available: {running or '?'} -> {latest}")
    sys.exit(10)
print(f"up to date ({running or 'unknown'})")
PY
  exit $?
fi

# Ensure uv is present — bootstrap it on a first-time install.
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found — installing it..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi

# Plugin + marketplace names come from the manifests, so this never drifts.
read_name() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$1"
}
plugin="$(read_name "${repo_root}/.claude-plugin/plugin.json")"
marketplace="$(read_name "${repo_root}/.claude-plugin/marketplace.json")"

if command -v claude >/dev/null 2>&1; then
  echo "refreshing marketplace ${marketplace}..."
  # `marketplace update` takes the bare marketplace name; `plugin update` needs
  # the fully-qualified `<plugin>@<marketplace>` id (a bare name yields
  # `Plugin "cockpit" not found`). Both stay manifest-derived.
  claude plugin marketplace update "${marketplace}" \
    || echo "marketplace refresh failed; continuing to the uv reinstall." >&2
  echo "updating plugin ${plugin}@${marketplace}..."
  # Non-fatal: the uv reinstall below is what actually swaps the running daemon,
  # so a plugin-refresh failure must not (under `set -e`) abort before it. On a
  # first-time install this warns because the plugin isn't added yet — expected.
  claude plugin update "${plugin}@${marketplace}" \
    || echo "plugin refresh failed; continuing to the uv reinstall." >&2
else
  echo "claude CLI not found — update the plugin from inside Claude Code with /plugin." >&2
fi

# Pick the source to (re)install the daemon from. Normally that's this script's
# own checkout (`repo_root`) — what a developer iterating locally wants. But
# when update.sh is run from inside the installed plugin cache, a prior
# `/plugin update` (from inside Claude Code) may have dropped a NEWER version
# dir alongside the one we're running from. Reinstall from the newest cached
# version so the plugin and the uv-installed daemon can't drift — this is what
# makes "`/plugin update` then `bin/update.sh`" sync the daemon regardless of
# which version dir launched the script.
install_src="${repo_root}"
case "${repo_root}" in
"${HOME}/.claude/plugins/cache/"*)
  cache_root="${HOME}/.claude/plugins/cache/${marketplace}/${plugin}"
  # Version-named dirs (e.g. .../cockpit/0.27.91); newest by version sort wins.
  newest="$(find "${cache_root}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -V | tail -1 || true)"
  [ -n "${newest}" ] && install_src="${newest}"
  ;;
esac

if command -v uv >/dev/null 2>&1; then
  echo "(re)installing the cockpit command from ${install_src}..."
  # --no-cache is load-bearing, not belt-and-suspenders. The wheel version is
  # read at build time from .claude-plugin/plugin.json (hatch dynamic version),
  # but uv keys its build cache on the source *path*, not that file's contents.
  # A version-only bump (the common case — the pre-push hook touches nothing but
  # plugin.json) therefore leaves the cache key unchanged, so a plain
  # `--force` reinstall rebuilds nothing and re-serves the stale wheel: the
  # daemon stays pinned to the old version no matter how many times you run this.
  # --no-cache forces an actual rebuild so the new version is picked up.
  uv tool install --force --no-cache "${install_src}"
else
  echo "error: uv installed but 'uv' is not on PATH — open a new shell and re-run ${repo_root}/bin/update.sh." >&2
  exit 1
fi

echo
echo "done. restart Claude Code and 'cockpit watch' to apply."
