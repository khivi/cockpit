#!/usr/bin/env bash
# Update cockpit end-to-end:
#   1. refresh the Claude Code marketplace + plugin via the `claude` CLI
#   2. reinstall the `cockpit` command from the refreshed source via `uv`
# Restart Claude Code and `cockpit watch` afterwards to apply (plugin updates
# require a restart).
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Plugin + marketplace names come from the manifests, so this never drifts.
read_name() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$1"
}
plugin="$(read_name "${repo_root}/.claude-plugin/plugin.json")"
marketplace="$(read_name "${repo_root}/.claude-plugin/marketplace.json")"

if command -v claude >/dev/null 2>&1; then
  echo "refreshing marketplace ${marketplace}..."
  claude plugin marketplace update "${marketplace}"
  echo "updating plugin ${plugin}..."
  claude plugin update "${plugin}"
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
