# Cockpit

A Claude Code plugin that keeps **one git worktree, one cmux workspace, and one GitHub PR** in lockstep. Open a worktree, get a workspace with `claude` already running, and watch its PR status surface in your statusline. Merge the PR, the worktree and workspace go with it.

## Why

If you juggle several PRs at once, you end up babysitting three parallel things per task: a git worktree on disk, a terminal/agent workspace, and a PR on GitHub. Cockpit collapses them into a single object. You ask for `/cockpit:new fix-login`; you get the worktree, the workspace, and (once you push) the PR — all addressable by **PR number, branch, or slug** (the short workspace name, e.g. `fix-login`).

A background daemon polls GitHub every few minutes and caches each PR's CI / review state to `~/.config/cockpit/cache/` — the statusline reads from cache, so it never blocks on the network.

## Requirements

- Python **3.11+**
- `git` **2.30+**
- Workspace backend on `PATH`:
  - macOS → `cmux`
  - Linux → `limux` (cmux fork; nudge pills are disabled and cockpit warns at startup)
- [`gh` CLI](https://cli.github.com/), authenticated (`gh auth status` must pass)
- Claude Code with plugin support
- Optional: [`cship`](https://github.com/khivi/cship) for the statusline integration

### What happens if a dependency is missing

| Missing | Behavior |
|---|---|
| `gh` or `git` | refuses to start |
| `cship` or `starship` (with `use_cship: true`) | refuses to start |
| both `cmux` and `limux` | warns once; runs in cache-only mode |

## Install

Inside Claude Code:

```text
/plugin marketplace add https://github.com/khivi/cockpit
/plugin install cockpit@khivi-cockpit
```

That installs the plugin. To actually run the daemon, start it once by hand from inside a Claude Code session (where `$CLAUDE_PLUGIN_ROOT` is set):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/cockpit.py --watch    # long-running
${CLAUDE_PLUGIN_ROOT}/scripts/cockpit.py --once     # single cycle
```

From a plain shell, look the path up once (`echo $CLAUDE_PLUGIN_ROOT` inside Claude Code, then save it as an alias).

First run auto-creates `~/.config/cockpit/`, seeds `config.json`, and prompts once to wire Claude Code's statusLine to the cockpit footer.

> **No daemon auto-start.** Cockpit does not install a LaunchAgent or systemd unit. Run it yourself in a terminal or cmux tab — silent failures are worse than a visible log.

## Quick start

Inside any git repo:

```text
/cockpit:new fix-login
```

Cockpit auto-registers the GitHub repo, creates a worktree at `<parent>/fix-login` (sibling to your repo — e.g. `/code/myrepo` → `/code/fix-login`), spawns a cmux workspace named `fix-login`, and starts `claude` in it. Re-running the same command attaches to the existing setup; it's safe to re-run.

Open the PR however you normally do. Once it exists, cockpit picks it up on the next daemon cycle (default 5 minutes; force it with `/cockpit:sync`).

When the PR merges and the worktree is clean, cockpit tears both down automatically (configurable — see [Defaults](#defaults)).

## Commands

| Command | What it does |
|---|---|
| `/cockpit:new <branch-or-pr>` | Create or attach to a worktree+workspace. Numeric arg = PR mode. |
| `/cockpit:list` | Table of all managed worktrees: branch, PR, CI, review, last update. |
| `/cockpit:focus <pr\|branch\|slug>` | Switch cmux focus to the matching workspace. Read-only on disk. |
| `/cockpit:close <pr\|branch\|slug> [--force]` | Tear down worktree + workspace + PR cache. Refuses on dirty state, unpushed commits, or open PR without `--force`. |
| `/cockpit:sync` | Force an immediate poll cycle without waiting for the next interval. |
| `/cockpit:repos` | List configured repos from `~/.config/cockpit/config.json`. |
| `/cockpit:nudge` | Mute/unmute per-PR nudges. See [Nudge pills](#nudge-pills-optional). |

Example `/cockpit:list` output:

```text
REPO          BRANCH                          PR      CI        REVIEW                UPDATED
myrepo        feature/foo                     #123    pass      approved              2025-05-17T14:23:01
myrepo        fix/bar                         #124    fail      changes-req 💬3       2025-05-17T13:10:44
otherrepo     experiment/baz                  —       —         —                     — (no workspace)
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
| Workspace backend | `auto` (cmux, fall back to limux) | `config.json` → `tool` (`cmux` \| `limux` \| `none` \| `auto`) |
| Auto-cleanup on merge | **on** | `config.json` → `auto_cleanup_on_merge`. When on, cockpit removes the worktree and closes the cmux workspace on any cycle where the PR is MERGED, the worktree is clean, and there are no unpushed commits. |
| Branch prefix | `<gh user>/` | `config.json` → per-repo `branch_prefix` |
| Default base branch | repo's `defaultBranchRef` | `config.json` → per-repo `default_base` |

## Claude Code statusline (optional)

Cockpit can render PR/CI/review state into the Claude Code statusline via [`cship`](https://github.com/khivi/cship) and [`starship`](https://starship.rs/). To opt in:

1. Install `cship` and `starship` on `PATH`.
2. Set `use_cship: true` in `~/.config/cockpit/config.json`.
3. Run `cockpit.py --footer` once to wire `~/.claude/settings.json`.

`--footer` also seeds `~/.config/cship.toml` and `~/.config/starship.toml` with the bundled defaults. Re-running it clobbers any local edits to those files — that's intentional, it's the reset switch.

## Nudge pills (optional)

When the agent is idle, cockpit can ping the workspace about actionable PR signals (CI failed, unresolved threads, merge conflict). It's automatic and no-ops outside cmux.

Mute per-PR when a nudge is wrong:

```text
/cockpit:nudge mute --categories comments --until 7d --reason "copilot intentional"
/cockpit:nudge unmute
/cockpit:nudge status
/cockpit:nudge list
/cockpit:nudge forget    # wipe the nudge file (resets mute + rate-limit)
```

Categories: `comments`, `ci`, `conflicts` (omit `--categories` to mute all). Mutes persist across daemon and cmux restarts and auto-clear once `until` passes.

## Uninstall

```bash
kill "$(cat ~/.config/cockpit/cockpit.pid 2>/dev/null)" 2>/dev/null || true
rm -rf ~/.config/cockpit    # nuke state (your worktrees remain)
```

Then inside Claude Code: `/plugin uninstall cockpit`.

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for setup. Public repo — read [`AGENTS.md`](./AGENTS.md) before opening a PR.

## License

MIT. See [LICENSE](LICENSE).
