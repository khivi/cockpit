#!/usr/bin/env bash
# One-shot dev setup: wire pre-commit hooks for both commit + push stages.
# pre-push runs version-bump and pytest via pre-commit (see .pre-commit-config.yaml).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

if ! command -v pre-commit >/dev/null; then
  echo "pre-commit not installed. Install with: brew install pre-commit" >&2
  exit 1
fi

pre-commit install
pre-commit install --hook-type pre-push --overwrite

echo "Installed: pre-commit hooks (commit + push stages)"
