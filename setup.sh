#!/usr/bin/env bash
# One-shot dev setup: wire pre-commit + pre-push version bumper into .git/hooks.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

if ! command -v pre-commit >/dev/null; then
  echo "pre-commit not installed. Install with: brew install pre-commit" >&2
  exit 1
fi

pre-commit install

hooks_dir="$(git rev-parse --git-common-dir)/hooks"
src="$(pwd)/.githooks/version-bump.sh"
chmod +x "$src"
ln -sf "$src" "$hooks_dir/pre-push"

echo "Installed: pre-commit + pre-push version bumper in $hooks_dir"
