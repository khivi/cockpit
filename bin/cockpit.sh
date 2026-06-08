#!/usr/bin/env bash
# Launch the cockpit TUI daemon (`cockpit watch`) and supervise it. Prefers the
# installed `cockpit` command; if it isn't on PATH, runs it from this checkout
# via `uv`. Extra args are forwarded to `watch`. Use the `cockpit` command
# directly for other subcommands (close/new/focus/nudge/...).
#
# Self-update: when the TUI exits with RESTART_EXIT_CODE (the user pressed `u`
# on an available update), this wrapper runs bin/update.sh and relaunches — the
# update can't take effect in-process since it reinstalls the running package.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Must match cockpit.tui.app.RESTART_EXIT_CODE.
RESTART_EXIT_CODE=42

run_watch() {
  if command -v cockpit >/dev/null 2>&1; then
    cockpit watch "$@"
  elif command -v uv >/dev/null 2>&1; then
    uv run --project "${repo_root}" cockpit watch "$@"
  else
    echo "cockpit: not installed and uv is unavailable." >&2
    echo "run ${repo_root}/bin/install.sh first." >&2
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
