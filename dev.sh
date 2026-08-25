#!/usr/bin/env bash
# Run THIS worktree's cockpit against a throwaway sandbox, so a dev build can
# never fight the installed daemon or act on your real worktrees.
#
#   ./dev.sh                    # snapshot mode: your real repos + a copy of
#                               # your real PR cache, read-only
#   ./dev.sh --empty            # no repos at all — layout/keybinding work
#   ./dev.sh -- nudge list      # any other subcommand, same sandbox
#
# Five things have to be isolated, and missing any one of them reaches your
# real state:
#
#   COCKPIT_HOME  config.json and cache/*__pr-*.json.
#   COCKPIT_RUNTIME_DIR
#                 cockpit.pid and close-requests/. A separate variable because
#                 these are machine-local and deliberately do NOT follow
#                 COCKPIT_HOME (which is often synced). Share it and
#                 `claim_pidfile` either exits 1 on the installed daemon's live
#                 PID or steals the pidfile, after which every `cockpit close`
#                 in every worktree routes into the dev build.
#   TMPDIR        the statusline/starship flat cells live in
#                 `$TMPDIR/cockpit-cache` (cache.py::FLAT_CACHE_DIR), NOT under
#                 COCKPIT_HOME. Leave it shared and a dev build repaints your
#                 live footer.
#   tool: none    no workspace backend, so every cmux write — spawn, close,
#                 rename, set-color, workspace-group, and `send` into a live
#                 Claude session — becomes a no-op.
#   --dry         `tool: none` does NOT cover autoclose: `_maybe_autoclose`
#                 removes merged worktrees and runs `git branch -D` through
#                 git, not through the backend. --dry is the gate that stops
#                 it, along with the tracker writes (`gh issue close`, Linear
#                 issueUpdate, Jira transitions, Trello move_card).
#
# Not covered: `gh` reads still happen on the slow tick under --dry. They are
# reads, but they spend rate limit.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

mode=snapshot
sandbox=".cockpit-dev"

while [ $# -gt 0 ]; do
  case "$1" in
    --snapshot) mode=snapshot; shift ;;
    --empty) mode=empty; shift ;;
    --) shift; break ;;
    -h | --help)
      sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) break ;;
  esac
done

# Default to the TUI. `--dry` is a real cockpit flag, not a dev.sh one.
if [ $# -eq 0 ]; then
  set -- watch
fi

# Force --dry on every `watch`, not only the no-args default: `./dev.sh -- watch`
# is a form the help advertises, and without the flag the daemon acts for real.
# `tool: none` does not cover autoclose, which removes worktrees and deletes
# branches through git rather than through the workspace backend.
if [ "$1" = watch ]; then
  case " $* " in
    *" --dry "*) ;;
    *) set -- "$@" --dry ;;
  esac
fi

# Announced before the guards below so the resolved argv is visible even when
# one of them refuses — `watch` silently losing its forced --dry is the failure
# worth seeing.
echo "dev.sh: cockpit $*"

# `cockpit setup` writes ~/.claude/settings.json and ~/.config/starship.toml
# with `sys.executable` baked in. From this worktree that is .venv/bin/python,
# which dies with the worktree and takes the statusline down with it — the
# "footer disappeared" bug in AGENTS.md's `{python}` pin invariant. The sandbox
# COCKPIT_HOME does not protect you: setup writes outside it.
for arg in "$@"; do
  if [ "$arg" = "setup" ] || [ "$arg" = "--setup" ]; then
    echo "dev.sh: refusing to run \`cockpit setup\` from a worktree." >&2
    echo "It bakes this worktree's .venv/bin/python into ~/.claude/settings.json" >&2
    echo "and ~/.config/starship.toml, which breaks your statusline when the" >&2
    echo "worktree is removed. Run the brew-installed \`cockpit setup\` instead." >&2
    exit 2
  fi
done

if [ "$1" = "watch" ] && [ ! -t 1 ]; then
  echo "dev.sh: \`watch\` is a Textual TUI and needs a terminal (it exits 2 without one)." >&2
  exit 2
fi

real_home="${COCKPIT_HOME:-$HOME/.config/cockpit}"

# Re-seed from scratch every run: a snapshot drifts the moment the real daemon
# ticks, and a half-stale sandbox is harder to reason about than a slow copy.
rm -rf "$sandbox"
mkdir -p "$sandbox/cache" "$sandbox/tmp" "$sandbox/runtime"
touch "$sandbox/.sandbox"

if [ "$mode" = snapshot ] && [ -d "$real_home/cache" ]; then
  # PR snapshots only. Nudge prefs are deliberately left behind: a mute or
  # snooze is a real decision about a real PR, and a dev build has no business
  # reading or rewriting one.
  find "$real_home/cache" -maxdepth 1 -name '*__pr-*.json' -exec cp {} "$sandbox/cache/" \;
fi

python3 - "$mode" "$real_home/config.json" "$sandbox/config.json" <<'PY'
import json
import sys

mode, src, dest = sys.argv[1], sys.argv[2], sys.argv[3]

cfg = {"repos": []}
if mode == "snapshot":
    try:
        with open(src) as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(f"dev.sh: no usable config at {src} ({e}) — starting empty", file=sys.stderr)
        cfg = {"repos": []}

# Belt and braces. --dry already gates every one of these, but they are the
# irreversible class (a closed ticket, a labelled issue, a spawned review agent
# on someone's PR), and this also covers `./dev.sh -- <subcommand>` runs that
# never go near the daemon's dry flag. Write-enabling keys only: `dev_done`
# stays, being the value a fetched ticket state is *compared* against rather
# than one anything moves a ticket to.
# `fast_skills` shells out to `claude -p` inline and `slow_skills` spawns a
# workspace running `claude` (cycle.py::_run_repo_skills) — a sandbox must start
# no agents. NOT the `skills` block, which holds only slash-command *names*
# interpolated into seed prompts and writes nothing.
DROP = {
    "close_on_merge",
    "start_label",
    "merge_done",
    "fast_skills",
    "slow_skills",
}
FALSE = {"review_prs", "dependabot", "review_external"}


def scrub(node):
    if isinstance(node, dict):
        return {
            k: (False if k in FALSE else scrub(v))
            for k, v in node.items()
            if k not in DROP
        }
    if isinstance(node, list):
        return [scrub(v) for v in node]
    return node


cfg = scrub(cfg)
cfg["tool"] = "none"

with open(dest, "w") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")

print(f"dev.sh: {mode} sandbox, {len(cfg.get('repos', []))} repo(s), tool=none")
PY

export COCKPIT_HOME="$PWD/$sandbox"
export TMPDIR="$PWD/$sandbox/tmp"
# The pidfile + close-request queue do NOT follow COCKPIT_HOME (they are
# machine-local, so they must not ride a synced directory), so isolating them
# takes its own variable. Without this the dev build claims the installed
# daemon's pidfile and drains its real teardown queue.
export COCKPIT_RUNTIME_DIR="$PWD/$sandbox/runtime"

echo "dev.sh: COCKPIT_HOME=$COCKPIT_HOME"
echo "dev.sh: COCKPIT_RUNTIME_DIR=$COCKPIT_RUNTIME_DIR"
echo "dev.sh: cmux-facing features (folds, focus, a/ask, d/diff) are INERT under tool=none."

exec uv run cockpit "$@"
