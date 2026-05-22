#!/usr/bin/env bash
set -euo pipefail

if [ "$(git rev-parse --abbrev-ref HEAD)" = "main" ]; then
  exit 0
fi

plugin=".claude-plugin/plugin.json"
current=$(jq -r .version "$plugin")
parent_version=$(git show HEAD~1:"$plugin" 2>/dev/null | jq -r .version 2>/dev/null || echo "0.0.0")

last_bump=$(git log --format=%H --grep='^chore: bump version' -1 || true)
if [ -n "$last_bump" ]; then
  range="${last_bump}..HEAD"
else
  range="HEAD"
fi

bump=patch
while IFS= read -r msg; do
  case "$msg" in
    break*|BREAK*)
      bump=major
      break
      ;;
    feat*)
      [ "$bump" = "patch" ] && bump=minor
      ;;
  esac
done < <(git log "$range" --format=%s)

IFS='.' read -r maj min pat <<< "$parent_version"
case "$bump" in
  major) maj=$((maj+1)); min=0; pat=0 ;;
  minor) min=$((min+1)); pat=0 ;;
  patch) pat=$((pat+1)) ;;
esac
expected="$maj.$min.$pat"

semver_gte() {
  local a1 a2 a3 b1 b2 b3
  IFS='.' read -r a1 a2 a3 <<< "$1"
  IFS='.' read -r b1 b2 b3 <<< "$2"
  if (( a1 != b1 )); then (( a1 > b1 )); return; fi
  if (( a2 != b2 )); then (( a2 > b2 )); return; fi
  (( a3 >= b3 ))
}

if semver_gte "$current" "$expected"; then
  exit 0
fi

sed -i.bak -E "s/(\"version\"[[:space:]]*:[[:space:]]*\")[^\"]+(\")/\1${expected}\2/" "$plugin"
rm "$plugin.bak"

git add "$plugin"
git commit -m "chore: bump version to $expected"

echo "Bumped $current -> $expected ($bump). Re-run \`git push\`." >&2
exit 1
