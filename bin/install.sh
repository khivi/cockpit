#!/usr/bin/env bash
# Bootstrap installer: ensure `uv` is present, then install the `cockpit`
# command on PATH via `uv tool install`. Run once; afterwards use `cockpit`
# directly (e.g. `cockpit watch`). Safe to re-run to upgrade.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found — installing it..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv installed but 'uv' is not on PATH — open a new shell and re-run." >&2
  exit 1
fi

echo "installing cockpit from ${repo_root} ..."
uv tool install --force "${repo_root}"

echo
echo "done — 'cockpit' is on PATH (via ~/.local/bin)."
echo "start the daemon with:  cockpit watch"
