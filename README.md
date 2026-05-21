[github.com/khivi/cockpit](https://github.com/khivi/cockpit)

cmux workspaces backed by git worktrees, aligned to GitHub PRs. One PR ↔ one worktree (sibling of your main repo) ↔ one cmux workspace, with status surfaced in a footer file and (optionally) cmux pills.

## What it does

For every active PR you keep open, cockpit enforces a **1:1:1 invariant**:

- **Worktree** at `<dirname(main-repo)>/<short>` — physically isolated on disk
- **cmux workspace** named `<short>` with `claude` pre-running as the single tab
- **GitHub PR** polled by a background cockpit, status cached at `~/.config/cockpit/cache/`

State is **derived**, not stored separately: `git worktree list` + `cmux tree --all --json` are the source of truth. There is no `state.json` to drift.

## Install

Inside Claude Code:

```text
/plugin marketplace add https://github.com/khivi/cockpit
/plugin install cockpit@khivi-cockpit
```

Then start the cockpit in the foreground so you can see what it's doing.
First run auto-creates `~/.config/cockpit/`, seeds `config.json`, and prompts
once to wire Claude Code's statusLine to the cockpit footer:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/cockpit.py --watch
# or run one cycle and exit:
${CLAUDE_PLUGIN_ROOT}/scripts/cockpit.py --once
```

Then edit `~/.config/cockpit/config.json` to register your managed repos, or just run `/cockpit:new` inside any git repo — it auto-adds the repo via `gh api user` + `gh repo view`.

> **No daemon auto-start.** cockpit does not install a LaunchAgent. Run the cockpit by hand in a terminal/cmux tab so you can see its log output.

## Usage

### `/cockpit:new <branch-or-pr>`

```text
/cockpit:new fix-login                  # new branch off default base
/cockpit:new 12345                      # PR mode (numeric arg)
/cockpit:new fix-login --pr 12345       # explicit PR mode with custom local branch
```

Idempotent — re-running for the same branch attaches to the existing worktree+workspace.

### `/cockpit:list`

```text
REPO          BRANCH              PR     CI       REVIEW          UPDATED
myrepo        feature/foo         #123   pass     approved        2m ago
myrepo        fix/bar             #124   fail     changes-req     1h ago
otherrepo     experiment/baz      —      —        —               3d ago  (no PR)
```

### `/cockpit:sync`

Kicks the cockpit immediately (SIGUSR1) if `--watch` is running, otherwise forks `cockpit.py --once`. Refreshes the PR cache and footer.

### `/cockpit:repos`

Lists configured repos (name, path, branch prefix, default base) from `~/.config/cockpit/config.json`. Referenced by `/cockpit:new`'s error when `--repo <name>` doesn't match any configured repo.

### `/cockpit:focus <pr|branch|slug>`

Switches cmux focus to the matching workspace. Resolves via `lib.cmux.resolve_workspace` (PR → branch → slug). Read-only on git/disk.

### `/cockpit:close <pr|branch|slug> [--force]`

Removes worktree + workspace + PR cache. Refuses on uncommitted changes, unpushed commits, or an open PR unless `--force`. Shares its resolver with `/cockpit:focus`.

## State directory

```text
~/.config/cockpit/
├── config.json          # managed repos + tunables
├── cache/
│   └── <repo>__pr-<N>.json
└── cockpit.pid
```

The cockpit logs to stderr — visible in the `--watch` terminal. No log file is written.

## Claude Code statusline

Cockpit delegates the Claude Code statusline to [`cship`](https://github.com/khivi/cship). `scripts/claude.py` is a thin shim that pipes Claude Code's stdin JSON through to `cship` and forwards its output — keeping it in the path lets cockpit shape input or fail soft when cship isn't installed.

Opt in by setting `use_cship: true` in `~/.config/cockpit/config.json`, then run `cockpit.py --footer` once to wire everything up. That command (and only that command) verifies `cship` is on `PATH`, writes `~/.claude/settings.json` so Claude Code invokes the shim each render (any existing file is backed up), and copies both `scripts/defaults/cship.toml` to `~/.config/cship.toml` and `scripts/defaults/starship.toml` to `~/.config/starship.toml`. If `use_cship: true` but `cship` is missing, `--footer` hard-errors — install cship first, or leave the flag off.

Two toml files because the chain has two halves: cship's line renderer handles `[directory]`, `[time]`, and any `$cship.*` modules; everything starship-flavored (every `[custom.*]` — Linear ticket, PR state, CI checks, etc.) is rendered by spawning the starship binary, which cship does when its `format` expands `$starship_prompt`. Cockpit ships both files so the chain works end-to-end without dotfiles plumbing.

If `~/.config/cship.toml` or `~/.config/starship.toml` is a symlink (e.g. into a dotfiles repo), `--footer` backs up the target file and replaces the symlink with a real file rather than writing through to it.

`--once` and `--watch` reconcile cycles never touch any of these files. So local edits to `~/.config/cship.toml` or `~/.config/starship.toml` stick around indefinitely; re-run `cockpit.py --footer` to deliberately clobber them back to the bundled defaults. When `use_cship` is unset (the default), `--footer` is a no-op on the statusLine.

To wire by hand:

```json
{
  "statusLine": {
    "type": "command",
    "command": "${CLAUDE_PLUGIN_ROOT}/scripts/claude.py"
  }
}
```

## Nudge wiring (idle + loop pills)

`hooks/cmux-idle-pill.sh` is wired automatically via the plugin's `hooks.json` and owns two cmux pills on every Claude session:

- **`idle=☕ rest`** — set on `Stop` when the agent has parked at the prompt with no live `/loop`; cleared on `UserPromptSubmit`. The cockpit reconciler reads this pill in `nudge_if_idle` to decide whether to ping a workspace about an actionable PR signal (CI failed, unresolved threads, merge conflict). Without it the cockpit is a passive dashboard.
- **`loop=🔄`** — set on `PreToolUse(ScheduleWakeup|CronCreate|CronUpdate)` and refreshed on every `Stop` whose last assistant turn armed another wakeup; cleared on `PreToolUse(CronDelete)`, on `SessionEnd`, and on any `Stop` whose last turn did *not* arm a wakeup. Visual-only — at-a-glance signal that the session is iterating on its own schedule.

Two non-obvious behaviors worth knowing:

- **`/loop` suppression of `idle=`.** A dynamic `/loop` ends each turn with `ScheduleWakeup`, and the session is *not* truly at rest during the wait window — broadcasters that read `idle=` would happily target a session waiting for its own next wakeup. So on `Stop` the hook scans the transcript's last assistant turn; if it called `ScheduleWakeup` or `CronCreate`, `idle=` is left cleared and `loop=` is set.
- **Fire-and-forget detach.** Every `cmux` call is backgrounded so the hook returns in <1 ms regardless of daemon state. The cmux socket occasionally stalls under contention (cockpit watcher + every session's hooks), and without the detach Claude Code's hook timeout surfaces a "non-blocking status code" banner on every prompt. Pill updates are best-effort by design.

Outside cmux, the hook no-ops (early-exits on missing `CMUX_WORKSPACE_ID`).

### Muting nudges per PR

When a nudge is wrong (e.g. a Copilot thread you've intentionally left open), mute it from inside the Claude session with `/cockpit:nudge`:

```text
/cockpit:nudge mute --categories comments --until 7d --reason "copilot intentional"
/cockpit:nudge unmute
/cockpit:nudge status
/cockpit:nudge list
```

The skill infers the current branch's PR via `gh pr view`. Mutes are persisted to `~/.config/cockpit/cache/nudges/<pr-number>.json` and survive both daemon and cmux restarts. The daemon auto-clears the mute once `until` passes. Categories: `comments`, `ci`, `conflicts` (omit `--categories` to mute all). Same surface is available as a regular CLI via `scripts/nudge.py` if you prefer the shell.

## Current defaults & how to change them

| Knob | Default | Where to change |
|---|---|---|
| Polling interval | 300 s | `config.json` → `poll_interval_seconds` |
| Auto-cleanup on merge | **on** | `config.json` → `auto_cleanup_on_merge: false`. When on, the cockpit removes the worktree **and** closes the cmux workspace on any cycle where the PR is MERGED, the worktree is clean, and there are no unpushed commits. cmux has no single-workspace destroy verb — workspace teardown closes every surface and logs a warning if the workspace persists. |
| Branch prefix | `<gh user>/` | `config.json` → per-repo `branch_prefix` |
| Default base branch | repo's `defaultBranchRef` | `config.json` → per-repo `default_base` |

## Requirements

- macOS or Linux
- `cmux`
- Claude Code with plugin support
- `gh` CLI, authenticated (`gh auth status` must pass)
- Python 3.11+
- `git` 2.30+ (for `worktree --porcelain`)

## How it differs from other cmux plugins

Existing cmux tools (`hashangit/cmux-skill`, `hummer98/using-cmux`, `jbasdf/setup-cmux`, `cmux-terminal-manager`) wrap the cmux CLI, generate workspace configs, or bridge devcontainers. cockpit is the only one that binds the cmux workspace **physically** to a git worktree and **logically** to a GitHub PR, with a long-running cockpit reconciling all three.

## Uninstall

```bash
# stop the cockpit if it's running:
kill "$(cat ~/.config/cockpit/cockpit.pid 2>/dev/null)" 2>/dev/null || true
rm -rf ~/.config/cockpit                      # nuke state (your worktrees remain)
claude /plugin uninstall cockpit
```

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for setup. Public repo — read [`AGENTS.md`](./AGENTS.md) before opening a PR.

## License

MIT. See [LICENSE](LICENSE).
