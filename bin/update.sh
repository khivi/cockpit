#!/usr/bin/env bash
# Update cockpit end-to-end:
#   1. refresh the Claude Code marketplace + plugin via the `claude` CLI
#   2. reinstall the `cockpit` command from the refreshed source via `uv`
# Restart Claude Code and `cockpit watch` afterwards to apply (plugin updates
# require a restart).
#
# `--check`: compare the running version against the latest on the install repo
# and report, WITHOUT updating. Exits 0 when up to date, 10 when an update is
# available, 1 when the check can't run. This is the same comparison the TUI's
# header indicator uses, exposed for scripts.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "${1:-}" = "--check" ]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found — can't run the version check. Install via bin/install.sh." >&2
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
  # so a plugin-refresh failure must not (under `set -e`) abort before it.
  claude plugin update "${plugin}@${marketplace}" \
    || echo "plugin refresh failed; continuing to the uv reinstall." >&2
else
  echo "claude CLI not found — update the plugin from inside Claude Code with /plugin." >&2
fi

if command -v uv >/dev/null 2>&1; then
  echo "reinstalling the cockpit command..."
  uv tool install --force "${repo_root}"
else
  echo "uv not found — run ${repo_root}/bin/install.sh to (re)install the cockpit command." >&2
fi

echo
echo "done. restart Claude Code and 'cockpit watch' to apply."
