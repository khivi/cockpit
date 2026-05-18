# cockpit

cmux workspaces backed by git worktrees, aligned to GitHub PRs. One PR ↔ one worktree (sibling of your main repo) ↔ one cmux workspace, with status surfaced in a footer file and (optionally) cmux pills.

## What it does

For every active PR you keep open, cockpit enforces a **1:1:1 invariant**:

- **Worktree** at `<dirname(main-repo)>/<short>` — physically isolated on disk
- **cmux workspace** named `<short>` with `claude` pre-running as the single tab
- **GitHub PR** polled by a background cockpit, status cached at `~/.config/cockpit/cache/`

State is **derived**, not stored separately: `git worktree list` + `cmux tree --all --json` are the source of truth. There is no `state.json` to drift.

## Install

```bash
claude /plugin install khivi/cockpit
# start the cockpit in the foreground so you can see what it's doing
# (first run auto-creates ~/.config/cockpit/, seeds config.json,
#  and prompts once to wire Claude Code's statusLine to the cockpit footer):
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
/cockpit:new hotfix --base release-1.2
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

### `/cockpit:close <branch> [--force]`

Removes worktree + workspace + cache. Refuses on uncommitted changes / open PR unless `--force`.

### `/cockpit:focus <pr|branch|slug>`

Switches cmux focus to the matching workspace. Read-only on git/disk.

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

`cockpit.py --footer` doubles as a Claude Code statusline command. The first run of `cockpit.py` offers to wire it for you; to do it by hand, add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "${CLAUDE_PLUGIN_ROOT}/scripts/cockpit.py --footer"
  }
}
```

Output per render (current branch, current cwd):

```text
#27933 khivi/PE-4081-fix-login "Fix login regression after token refresh" · PE-4081 · ✓ · review-required · ✏️ 3 · 🤖 Opus 4.7 · 🧠 7%/1M · ⌛ 5h 42% · ⏱ 1h 23m
```

Reads cockpit's cache only — never blocks on `gh`. Falls back to `<branch> · no PR` when there's no cache hit, and to an empty line outside a git repo. The Linear ticket ID (e.g. `PE-4081`) is regex-extracted from the branch name — no API call. The `🤖` (model), `🧠` (context window), `⌛` (5h usage), and `⏱` (elapsed wall-clock since the first transcript entry) parts all come from the JSON Claude Code pipes on stdin.

## Current defaults & how to change them

| Knob | Default | Where to change |
|---|---|---|
| Polling interval | 300 s | `config.json` → `poll_interval_seconds` |
| Auto-cleanup on merge | **on** | `config.json` → `auto_cleanup_on_merge: false`. When on, the cockpit removes the worktree **and** closes the cmux workspace on any cycle where the PR is MERGED, the worktree is clean, and there are no unpushed commits. cmux has no single-workspace destroy verb — workspace teardown closes every surface and logs a warning if the workspace persists. |
| Branch prefix | `<gh user>/` | `config.json` → per-repo `branch_prefix` |
| Default base branch | repo's `defaultBranchRef` | `config.json` → per-repo `default_base` |
| Tabs per workspace | 1 (`claude`) | edit `scripts/spawn.py` (single `cmux new-workspace --command`) |

## Requirements

- macOS or Linux (cockpit is portable; no LaunchAgent or systemd unit is installed)
- `cmux` (workspace primitives: `new-workspace`, `list-workspaces`, `tree --all --json`, `select-workspace`)
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
