#!/usr/bin/env bash
# Launcher for the cockpit CLI. Prefers the installed `cockpit` command; if it
# isn't on PATH, runs it from this checkout via `uv run`. Handy for the daemon
# (`bin/cockpit.sh watch`) without a global install.
#
# NOTE: this adds uv's startup overhead on the fallback path, so it is for
# interactive/daemon use only. The per-render statusline + starship fields use
# the installed `cockpit` directly (see lib/config.STARSHIP_CMD) — never route
# that hot path through this wrapper.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v cockpit >/dev/null 2>&1; then
  exec cockpit "$@"
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run --project "${repo_root}" cockpit "$@"
fi

echo "cockpit: not installed and uv is unavailable." >&2
echo "run ${repo_root}/bin/install.sh first." >&2
exit 127
