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
| `cmux` only (Linux falls back to `limux`) | worktrees, workspaces, and `/cockpit:new` (including Linear positional input) all work; the nudge pills feature is disabled (limux lacks the persistent-pill API) and cockpit warns at startup |
| `cship` (with `use_cship: false` or unset) | the statusline pills don't render; everything else, including `/cockpit:new <linear-id>`, still creates the workspace and seeds the plan prompt |
| Linear MCP connector (for `/cockpit:new <linear-id>`, only when `use_linear: true`) | worktree + workspace still get created and named after the id (e.g. `khivi/pe-1234`). If `claude mcp list` reports no Linear entry, cockpit warns once and seeds the generic plan prompt instead of the MCP-instructing one. If detection is inconclusive and Claude can't actually reach the MCP, the spawned Claude reports that on its first turn and exits without writing a plan. With `use_linear: false` (default) the MCP is never consulted. |

## Install

Inside Claude Code:

```text
/plugin marketplace add https://github.com/khivi/cockpit
/plugin install cockpit@khivi-cockpit
```

That installs the slash commands. You can use `/cockpit:new` and the rest immediately — see [Quick start](#quick-start) below.

For live PR/CI status to flow into `/cockpit:list` and the statusline, you also need to start the polling daemon. There is no auto-start (no LaunchAgent, no systemd unit) — run it yourself in a terminal or cmux tab so failures are visible:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/cockpit.py --watch    # long-running poller
${CLAUDE_PLUGIN_ROOT}/scripts/cockpit.py --once     # single cycle, then exit
```

`$CLAUDE_PLUGIN_ROOT` is set inside Claude Code sessions. From a plain shell, `echo $CLAUDE_PLUGIN_ROOT` from a Claude session once and save it as an alias. The daemon is a foreground process — close the terminal, it dies; run it in `tmux`/`cmux`/`screen` for persistence.

First run auto-creates `~/.config/cockpit/`, seeds `config.json`, and prompts once to wire Claude Code's statusLine to the cockpit footer.

## Quick start

Inside any git repo:

```text
/cockpit:new fix-login
```

Cockpit auto-registers the GitHub repo, creates a worktree at `<parent>/fix-login` (sibling to your repo — e.g. `/code/myrepo` → `/code/fix-login`), spawns a cmux workspace named `fix-login`, and starts `claude` in it. Re-running the same command attaches to the existing setup; it's safe to re-run.

Open the PR however you normally do. Once it exists, cockpit picks it up on the next daemon cycle (default 5 minutes; force it with `/cockpit:sync`).

When the PR merges and the worktree is clean, cockpit tears both down automatically (configurable — see [Defaults](#defaults)).

### Is it working?

| Check | Expected |
|---|---|
| `cat ~/.config/cockpit/cockpit.pid` | a running PID; absent or stale → daemon not running |
| `/cockpit:list` | your managed worktrees show with PR / CI / review columns populated |
| `/cockpit:sync` | forces a poll; PR data should refresh within a few seconds |

If `/cockpit:list` shows `—` everywhere, the daemon hasn't completed a cycle yet — wait for the polling interval or run `/cockpit:sync`. If pidfile exists but `/cockpit:list` is stale, tail the `--watch` terminal for the cycle error.

## Linear positional input

`/cockpit:new` accepts a Linear ticket id in the same positional slot that takes a branch name or PR number:

```text
/cockpit:new PE-1234
```

Matches `[A-Z]{2,6}-\d+` (case-insensitive). Creates a worktree on `<branch_prefix><id-lower>` (e.g. `khivi/pe-1234`) and a cmux workspace named `pe-1234`.

With `use_linear: true` AND the Linear MCP detected via `claude mcp list`, Claude's first turn reads the ticket via the Linear MCP, derives a `<slug>` from the title, then renames both the branch (`khivi/pe-1234-add-login-flow`) and the workspace (`add-login-flow` — no id prefix). Cockpit's next reconcile cycle picks both up automatically.

With `use_linear: false` (default) or no Linear MCP detected, the workspace just starts on `khivi/pe-1234` with the generic plan prompt — same as `/cockpit:new --branch pe-1234`. Auth lives in your Claude MCP config, never in cockpit.

Out of scope here: rendering the Linear ticket title in the cship statusline pill. That requires cship-side support (see [TODO.md](TODO.md)).

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

Edit `config.json` to register repos manually, or just run `/cockpit:new` and let cockpit add the current repo for you. Minimal shape:

```json
{
  "repos": [
    {
      "name": "myrepo",
      "path": "/absolute/path/to/main/repo",
      "branch_prefix": "yourusername/",
      "default_base": "main",
      "linear_keys": ["TEAM"]
    }
  ],
  "poll_interval_seconds": 300,
  "auto_cleanup_on_merge": true,
  "use_cship": false,
  "use_linear": false,
  "tool": "auto"
}
```

Full schema with every optional key in [`config.example.json`](config.example.json).

The cockpit logs to stderr — visible in the `--watch` terminal. No log file is written.

### Defaults

| Knob | Default | Where to change |
|---|---|---|
| Polling interval | 300 s | `config.json` → `poll_interval_seconds` |
| Workspace backend | `auto` (cmux, fall back to limux) | `config.json` → `tool` (`cmux` \| `limux` \| `none` \| `auto`) |
| Auto-cleanup on merge | **on** | `config.json` → `auto_cleanup_on_merge`. When on, cockpit removes the worktree and closes the cmux workspace on any cycle where the PR is MERGED, the worktree is clean, and there are no unpushed commits. |
| Branch prefix | `<gh user>/` | `config.json` → per-repo `branch_prefix` |
| Default base branch | repo's `defaultBranchRef` | `config.json` → per-repo `default_base` |
| Smart Linear flow | **off** (opt-in) | `config.json` → `use_linear`. When on, `/cockpit:new PE-1234` pre-flights `claude mcp list` for a Linear connector and (if found) seeds Claude's first turn to fetch the ticket via the Linear MCP and rename branch + workspace to include the title slug. Off → behaves like `/cockpit:new --branch pe-1234`: plain branch + generic plan prompt. |
| Linear key → repo routing | per-repo, opt-in | Per-repo `linear_keys: ["PE", ...]` paired with `use_linear: true`. `/cockpit:new PE-1234` (no `--repo`) routes the spawn to the repo whose `linear_keys` contains `PE`, regardless of cwd. Unique match wins; zero matches falls back to cwd discovery; multiple matches print a note on stderr and also fall back. `--repo <name>` always overrides. |

## Claude Code statusline (optional)

PR/CI/review state surfaces under the Claude Code prompt — model + context + rate-limit on line 1, branch identity + PR status on line 2:

```text
🤖 Opus 4.7   🧠 7%/1M   ⌛ 4%/5h   khivi/fix-login   ✓ clean   14:32
TICKET-123   APPROVED   #9999   ✓   Add login flow
```

Both lines collapse cleanly: line 2 disappears when there's no Linear ticket and no PR for the current branch; individual pills hide when their data is missing. Rendered by [`cship`](https://github.com/khivi/cship) + [`starship`](https://starship.rs/) reading cockpit's PR cache — no network calls in the prompt path.

To opt in:

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

## License

MIT. See [LICENSE](LICENSE). Contributing? Read [`AGENTS.md`](./AGENTS.md) first.
