#!/usr/bin/env bash
set -euo pipefail

current_branch=$(git rev-parse --abbrev-ref HEAD)
if [ "$current_branch" = "main" ]; then
  exit 0
fi

# Ensure we have the latest origin/main
git fetch origin main --quiet 2>/dev/null || true

plugin_file="./.claude-plugin/plugin.json"
old_version=$(git show origin/main:"$plugin_file" 2>/dev/null | jq -r .version)
current_version=$(jq -r .version "$plugin_file")

if [ "$old_version" != "$current_version" ]; then
  exit 0
fi

# Determine bump type from commits since origin/main
bump=patch
while IFS= read -r msg; do
  case "$msg" in
    break*|BREAK*) bump=major; break ;;
    feat*)
      if [ "$bump" = "patch" ]; then
        bump=minor
      fi
      ;;
  esac
done < <(git log origin/main..HEAD --format=%s)

IFS='.' read -r maj min pat <<< "$old_version"
case "$bump" in
  major) maj=$((maj+1)); min=0; pat=0 ;;
  minor) min=$((min+1)); pat=0 ;;
  patch) pat=$((pat+1)) ;;
esac
new_version="$maj.$min.$pat"

tmp=$(mktemp)
jq --arg v "$new_version" '.version = $v' "$plugin_file" > "$tmp"
mv "$tmp" "$plugin_file"

git add "$plugin_file"
git commit -m "chore: bump version to $new_version"

echo "Version bumped: $old_version → $new_version ($bump). Re-run \`git push\`." >&2
exit 1
