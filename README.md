# Cockpit

[github.com/khivi/cockpit](https://github.com/khivi/cockpit)

A Claude Code plugin that keeps **one git worktree, one [cmux](https://github.com/cmux/cmux) workspace, and one GitHub PR** in lockstep. Open a worktree, get a workspace with `claude` already running, and watch its PR status surface in your statusline. Close the PR, the worktree and workspace go with it.

## Why

If you juggle several PRs at once, you end up babysitting three parallel things per task: a git worktree on disk, a terminal/agent workspace, and a PR on GitHub. Cockpit collapses them into a single object. You ask for `/cockpit:new fix-login`; you get the worktree, the workspace, and (eventually) the PR — all addressable by branch, slug, or PR number. A background reconciler polls GitHub and writes the result somewhere your statusline can see.

State is **derived**, not stored: `git worktree list` and `cmux tree --all --json` are the source of truth. There's no `state.json` to drift out of sync.

## Requirements

- Python **3.11+**
- `git` **2.30+** (needs `worktree --porcelain`)
- Workspace backend on `PATH`:
  - macOS → `cmux`
  - Linux → `limux` (cmux fork; lacks the side-panel pill API, so cockpit auto-disables pills and prints a one-line warning at startup)
- [`gh` CLI](https://cli.github.com/), authenticated (`gh auth status` must pass)
- Claude Code with plugin support
- Optional: [`cship`](https://github.com/khivi/cship) for the statusline integration

Every `cockpit.py` invocation (`--watch`, `--once`, `--footer`) runs the same dependency preflight:

| Binary | Severity | Triggered when |
|---|---|---|
| `gh`, `git` | hard-fail (`exit 2`) | always |
| `cship`, `starship` | hard-fail (`exit 2`) | `use_cship: true` |
| `cmux` / `limux` | warning; cache-only mode | `tool: auto` and neither on PATH |

## Install

Inside Claude Code:

```text
/plugin marketplace add https://github.com/khivi/cockpit
/plugin install cockpit@khivi-cockpit
```

That installs the plugin. To actually run the reconciler, start it once by hand so you can see its log output:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/cockpit.py --watch    # long-running
# or
${CLAUDE_PLUGIN_ROOT}/scripts/cockpit.py --once     # single reconcile cycle
```

First run auto-creates `~/.config/cockpit/`, seeds `config.json`, and prompts once to wire Claude Code's statusLine to the cockpit footer.

> **No daemon auto-start.** Cockpit does not install a LaunchAgent or systemd unit. Run it yourself in a terminal or cmux tab — silent failures are worse than a visible log.

## Quick start

Inside any git repo:

```text
/cockpit:new fix-login
```

Cockpit auto-registers the repo (via `gh api user` + `gh repo view`), creates a sibling worktree at `<dirname(main-repo)>/fix-login`, spawns a cmux workspace named `fix-login`, and starts `claude` in it. Re-running the same command attaches to the existing setup — it's idempotent.

Open the PR however you normally do. Once it exists, cockpit picks it up on the next reconcile cycle (default 5 minutes; force it with `/cockpit:sync`).

When the PR merges and the worktree is clean, cockpit tears both down automatically (configurable — see [Defaults](#defaults)).

## Commands

| Command | What it does |
|---|---|
| `/cockpit:new <branch-or-pr>` | Create or attach to a worktree+workspace. Numeric arg = PR mode. |
| `/cockpit:list` | Table of all managed worktrees: branch, PR, CI, review, last update. |
| `/cockpit:focus <pr\|branch\|slug>` | Switch cmux focus to the matching workspace. Read-only on disk. |
| `/cockpit:close <pr\|branch\|slug> [--force]` | Tear down worktree + workspace + PR cache. Refuses on dirty state, unpushed commits, or open PR without `--force`. |
| `/cockpit:sync` | Force an immediate reconcile (SIGUSR1 to `--watch`, else forks `--once`). |
| `/cockpit:repos` | List configured repos from `~/.config/cockpit/config.json`. |
| `/cockpit:nudge` | Mute/unmute per-PR nudges. See [Nudge pills](#nudge-pills-optional). |

Example `/cockpit:list` output:

```text
REPO          BRANCH                          PR      CI        REVIEW                UPDATED
myrepo        feature/foo                     #123    pass      approved              2025-05-17T14:23:01
myrepo        fix/bar                         #124    fail      changes-req 💬3       2025-05-17T13:10:44
otherrepo     experiment/baz                  —       —         —                     —                    (no PR)
```

## Configuration

State and config live under `~/.config/cockpit/`:

```text
~/.config/cockpit/
├── config.json          # managed repos + tunables
├── cache/
│   └── <repo>__pr-<N>.json
└── cockpit.pid
```

Edit `config.json` to register repos manually, or just run `/cockpit:new` and let cockpit add the current repo for you. See [`config.example.json`](config.example.json) for the schema.

The cockpit logs to stderr — visible in the `--watch` terminal. No log file is written.

### Defaults

| Knob | Default | Where to change |
|---|---|---|
| Polling interval | 300 s | `config.json` → `poll_interval_seconds` |
| Auto-cleanup on merge | **on** | `config.json` → `auto_cleanup_on_merge`. When on, the cockpit removes the worktree and closes the cmux workspace on any cycle where the PR is MERGED, the worktree is clean, and there are no unpushed commits. |
| Branch prefix | `<gh user>/` | `config.json` → per-repo `branch_prefix` |
| Default base branch | repo's `defaultBranchRef` | `config.json` → per-repo `default_base` |

cmux has no single-workspace destroy verb — workspace teardown closes every surface and logs a warning if the workspace persists.

## Claude Code statusline (optional)

Cockpit can render PR/CI/review state into the Claude Code statusline via [`cship`](https://github.com/khivi/cship). To opt in:

1. Install `cship` and `starship` on `PATH`.
2. Set `use_cship: true` in `~/.config/cockpit/config.json`.
3. Run `cockpit.py --footer` once to wire it up.

To wire by hand instead, point Claude Code's statusLine at the shim:

```json
{
  "statusLine": {
    "type": "command",
    "command": "${CLAUDE_PLUGIN_ROOT}/scripts/footer.py"
  }
}
```

## Nudge pills (optional)

When the agent is idle, cockpit can ping the workspace about actionable PR signals (CI failed, unresolved threads, merge conflict). It's wired automatically via the plugin's `hooks.json` and no-ops outside cmux.

Mute per-PR when a nudge is wrong:

```text
/cockpit:nudge mute --categories comments --until 7d --reason "copilot intentional"
/cockpit:nudge unmute
/cockpit:nudge status
/cockpit:nudge list
```

Categories: `comments`, `ci`, `conflicts` (omit `--categories` to mute all). Mutes persist across daemon and cmux restarts and auto-clear once `until` passes.

## How it differs from other cmux plugins

Existing cmux tools (`hashangit/cmux-skill`, `hummer98/using-cmux`, `jbasdf/setup-cmux`, `cmux-terminal-manager`) wrap the cmux CLI, generate workspace configs, or bridge devcontainers. Cockpit is the only one that binds the cmux workspace **physically** to a git worktree and **logically** to a GitHub PR, with a long-running reconciler keeping all three aligned.

## Uninstall

```bash
kill "$(cat ~/.config/cockpit/cockpit.pid 2>/dev/null)" 2>/dev/null || true
rm -rf ~/.config/cockpit                      # nuke state (your worktrees remain)
claude /plugin uninstall cockpit
```

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for setup. Public repo — read [`AGENTS.md`](./AGENTS.md) before opening a PR.

## License

MIT. See [LICENSE](LICENSE).
