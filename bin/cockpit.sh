#!/usr/bin/env bash
# Launch the cockpit TUI daemon (`cockpit watch`). Prefers the installed
# `cockpit` command; if it isn't on PATH, runs it from this checkout via `uv`.
# This wrapper only starts the TUI — any extra args are forwarded as flags to
# `watch` (e.g. --dry-run). Use the `cockpit` command directly for other
# subcommands (sync/close/new/list/...).
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v cockpit >/dev/null 2>&1; then
  exec cockpit watch "$@"
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run --project "${repo_root}" cockpit watch "$@"
fi

echo "cockpit: not installed and uv is unavailable." >&2
echo "run ${repo_root}/bin/install.sh first." >&2
exit 127
