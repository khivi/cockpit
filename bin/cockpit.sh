#!/usr/bin/env bash
# Launch the cockpit TUI daemon (`cockpit watch`) and supervise it. Prefers the
# installed `cockpit` command; if it isn't on PATH, runs it from this checkout
# via `uv`. Extra args are forwarded to `watch`. Use the `cockpit` command
# directly for other subcommands (close/new/focus/nudge/...).
#
# `cockpit.sh update [--check]` is the one reserved word: it delegates to
# bin/update.sh (sibling of this script) without launching the TUI — handy when
# aliased (`cockpit-watch update`) or for headless/scripted updates. Remaining
# args (e.g. --check) pass through.
#
# Self-update: when the TUI exits with RESTART_EXIT_CODE (the user pressed `u`
# on an available update), this wrapper runs bin/update.sh and relaunches — the
# update can't take effect in-process since it reinstalls the running package.
set -euo pipefail

# Resolve repo_root from the script's real location, following any symlinks
# (e.g. ~/bin/cockpit.sh -> the installed plugin dir). Plain `readlink` in a
# loop is portable (BSD/macOS lacks `readlink -f`); the loop is a no-op for a
# regular file, so this stays correct whether or not a symlink is involved.
_src="${BASH_SOURCE[0]}"
while [ -h "${_src}" ]; do
  _dir="$(cd -P "$(dirname "${_src}")" && pwd)"
  _src="$(readlink "${_src}")"
  case "${_src}" in /*) ;; *) _src="${_dir}/${_src}" ;; esac
done
repo_root="$(cd -P "$(dirname "${_src}")/.." && pwd)"

# `update` is the one non-watch verb: delegate to the sibling updater and stop
# (exec, so the TUI supervisor loop never starts). Forwards trailing args.
if [ "${1:-}" = "update" ]; then
  shift
  exec "${repo_root}/bin/update.sh" "$@"
fi

# Must match cockpit.tui.app.RESTART_EXIT_CODE.
RESTART_EXIT_CODE=42

run_watch() {
  if command -v cockpit >/dev/null 2>&1; then
    cockpit watch "$@"
  elif command -v uv >/dev/null 2>&1; then
    uv run --project "${repo_root}" cockpit watch "$@"
  else
    echo "cockpit: not installed and uv is unavailable." >&2
    echo "run ${repo_root}/bin/update.sh first." >&2
    return 127
  fi
}

while :; do
  code=0
  run_watch "$@" || code=$?
  if [ "${code}" -eq "${RESTART_EXIT_CODE}" ]; then
    echo "cockpit: update requested — running bin/update.sh, then restarting..."
    "${repo_root}/bin/update.sh" \
      || echo "cockpit: update.sh failed; relaunching on the current version." >&2
    continue
  fi
  exit "${code}"
done
