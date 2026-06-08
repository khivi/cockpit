# Cockpit

A Claude Code plugin that keeps **one git worktree, one workspace (cmux/limux), and one GitHub PR** in lockstep. Open a worktree, get a workspace with `claude` already running, and watch its PR status surface in your statusline. Merge the PR, the worktree and workspace go with it.

## Why

If you juggle several PRs at once, you end up babysitting three parallel things per task: a git worktree on disk, a terminal/agent workspace, and a PR on GitHub. Cockpit collapses them into a single object. You ask for `/cockpit:new fix-login`; you get the worktree, the workspace, and (once you push) the PR — all addressable by **PR number, branch, or slug** (the short workspace name, e.g. `fix-login`).

A background daemon polls GitHub every few minutes and caches each PR's CI / review state to `~/.config/cockpit/cache/` — the statusline reads from cache, so it never blocks on the network.

## Requirements

- Python **3.12+**
- [`uv`](https://docs.astral.sh/uv/) (to install the `cockpit` command)
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

Two pieces: the **`cockpit` command** (the daemon, and the binary the slash commands call) and the Claude Code **plugin** (slash commands + hooks).

1. Install the `cockpit` command so it's on your `PATH`:

```bash
uv tool install git+https://github.com/khivi/cockpit
# or, from a checkout / the installed plugin dir, a one-shot bootstrap that
# installs uv too if missing:  bin/update.sh
# or run ad-hoc without installing:
#   uvx --from git+https://github.com/khivi/cockpit cockpit --help
```

`bin/cockpit.sh` launches the TUI daemon (`cockpit watch`), preferring the installed `cockpit` and otherwise running it from the checkout via `uv` — handy before a global install. It also supervises self-update: the TUI checks hourly for a newer version and shows an "⬆ update available" indicator in the header; press `u` and cockpit.sh runs `bin/update.sh`, then relaunches the daemon on the new version. `bin/update.sh` updates everything in one shot: it bootstraps `uv` if missing, refreshes the Claude Code marketplace + plugin via the `claude` CLI, then reinstalls the `cockpit` command via `uv` (restart Claude Code afterwards for the plugin's slash commands/hooks). `bin/update.sh --check` reports whether an update is available without applying it (exit 10 = available, 0 = current).

**Where to launch it.** `bin/cockpit.sh` lives in a checkout's `bin/`, and in each installed plugin version dir (`~/.claude/plugins/cache/khivi-cockpit/cockpit/<version>/bin/cockpit.sh`). Run it from a terminal or cmux tab (see "no auto-start" below). An alias keeps it one keystroke away:

```bash
# Have a checkout? Alias the stable path (stays current via git pull):
alias cockpit-watch='~/code/cockpit/bin/cockpit.sh'

# Plugin-only (no checkout)? Always launch the newest installed copy:
alias cockpit-watch='bash "$(ls -d ~/.claude/plugins/cache/khivi-cockpit/cockpit/*/bin/cockpit.sh | sort -V | tail -1)"'
```

Either alias works with self-update: pressing `u` runs `bin/update.sh` and relaunches on the new version. The same launcher also takes an `update` verb — `cockpit-watch update` (or `cockpit-watch update --check`) delegates to `bin/update.sh` without opening the TUI, for headless or scripted updates. The plugin-only alias also picks up a newer `cockpit.sh` itself on the next launch (the checkout alias gets that via `git pull`). The alias resolves through symlinks too, so `~/bin/cockpit-watch -> …/cockpit.sh` is fine.

1. Inside Claude Code, add the plugin:

```text
/plugin marketplace add https://github.com/khivi/cockpit
/plugin install cockpit@khivi-cockpit
```

The `/cockpit:new` slash command and the statusline hook invoke the `cockpit` command from step 1. If it isn't on `PATH`, the daemon warns at startup and the commands fail — re-run step 1.

For live PR/CI status to flow into the `cockpit watch` table and the statusline, start the daemon. There is no auto-start (no LaunchAgent, no systemd unit) — run it yourself in a terminal or cmux tab so failures are visible. `cockpit watch` opens a **terminal UI**: slow/fast tick countdowns + an update indicator in the header, and a navigable worktree table (arrow keys move the row cursor) showing each workspace's PR, author (the coworker login on a review PR, blank for your own), approval/CI state, unaddressed-comment count (💬), dirty-tree state, and PR title:

```bash
cockpit watch    # long-running daemon, terminal UI (requires a TTY)
```

The daemon is a foreground process — close the terminal, it dies; run it in `tmux`/`cmux`/`screen` for persistence.

First run auto-creates `~/.config/cockpit/` and seeds `config.json`. (Wiring Claude Code's statusLine is a separate one-time step — run `cockpit setup`, below.)

## Quick start

Inside any git repo:

```text
/cockpit:new fix-login
```

Cockpit auto-registers the GitHub repo, creates a worktree at `<parent>/fix-login` (sibling to your repo — e.g. `/code/myrepo` → `/code/fix-login`), spawns a workspace named `fix-login` (via cmux on macOS, limux on Linux), and starts `claude` in it. Re-running the same command attaches to the existing setup; it's safe to re-run.

A blank spawn like this — a new branch with no PR, ticket, or extra context — starts ready for you to state the task; no plan-only prompt is seeded. Cockpit seeds a plan-only first turn only when there's something to study first: a PR, a Linear ticket, inherited `--context`, or an explicit `-- <text>` task. Either way, a configured [`prompt_prefix`](#defaults) still runs.

Open the PR however you normally do. Once it exists, cockpit picks it up on the next daemon cycle (default 5 minutes; force it by pressing `s` in `cockpit watch`).

When the PR merges and the worktree is clean, cockpit tears both down automatically.

### Is it working?

| Check | Expected |
|---|---|
| `cat ~/.config/cockpit/cockpit.pid` | a running PID; absent or stale → daemon not running |
| `cockpit watch` | your managed worktrees show in the table with PR / Approval / CI columns populated |

If the table is empty, the daemon hasn't completed a cycle yet — wait for the polling interval or press `s` to force one. If the pidfile exists but the table is stale, check `~/.config/cockpit/watch.log` for the cycle error.

## Linear positional input

`/cockpit:new` accepts a Linear ticket id in the same positional slot that takes a branch name or PR number:

```text
/cockpit:new PE-1234
```

Matches `[A-Z]{2,6}-\d+` (case-insensitive). Creates a worktree on `<branch_prefix><id-lower>` (e.g. `khivi/pe-1234`) and a workspace named `pe-1234`.

With `use_linear: true` AND the Linear MCP detected via `claude mcp list`, Claude's first turn reads the ticket via the Linear MCP, derives a `<slug>` from the title, then renames both the branch (`khivi/pe-1234-add-login-flow`) and the workspace (`add-login-flow` — no id prefix). Cockpit's next reconcile cycle picks both up automatically.

With `use_linear: false` (default) or no Linear MCP detected, the workspace just starts on `khivi/pe-1234` with the generic plan prompt — a positional Linear key still seeds plan-only because it names a ticket to look at, but the branch/workspace stay plain (no MCP fetch or rename). Auth lives in your Claude MCP config, never in cockpit.

Out of scope here: rendering the Linear ticket title in the cship statusline pill. That requires cship-side support (see [TODO.md](TODO.md)).

## Commands

| Command | What it does |
|---|---|
| `/cockpit:new <branch-or-pr>` | Create or attach to a worktree+workspace. Numeric arg = PR mode. |

Listing worktrees, forcing a poll, focusing, closing a worktree, and nudging now live in the `cockpit watch` TUI — the table is the live worktree list, `f` (or Enter) focuses the cursor row's workspace, `p` opens its PR in your browser, `l` opens its linked Linear ticket, `s` forces a cycle, `c` tears down the cursor row (refuses on dirty/unpushed/open-PR), `C` force-closes it (overrides the open-PR refusal but never dirty/unpushed work), `m` mutes/unmutes its PR's nudges, `N` sends a nudge to the cursor row's workspace now (overrides mute + the slow-tick throttle, still gated on idle so it never types into a permission prompt), and `n` opens the new-workspace modal (type a branch name / `#N` / Linear id; with more than one repo configured a picker chooses which repo, defaulting to the cursor row's). The `cockpit nudge status|list|forget` CLI remains for shell use; closing is TUI-only now (`c`/`C`), with no `/cockpit:list`, `/cockpit:sync`, `/cockpit:close`, `/cockpit:focus`, `/cockpit:repos`, or `/cockpit:nudge` commands (`/cockpit:new` is the only slash command).

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
      "linear_keys": ["TEAM"],
      "sidebar_color": "Blue"
    }
  ],
  "slow_poll_interval_seconds": 300,
  "fast_poll_interval_seconds": 30,
  "use_cship": false,
  "use_linear": false,
  "check_update": true,
  "tool": "auto"
}
```

Full schema with every optional key in [`config.example.json`](config.example.json).

Each cycle's output is written to a bounded log file at `~/.config/cockpit/watch.log` (the last 200 lines), so it's greppable even though the TUI doesn't show a log pane right now.

### Defaults

| Knob | Default | Where to change |
|---|---|---|
| Slow poll interval | 300 s | `config.json` → `slow_poll_interval_seconds` |
| Fast poll interval | 30 s | `config.json` → `fast_poll_interval_seconds` |
| Workspace backend | `auto` (cmux, fall back to limux) | `config.json` → `tool` (`cmux` \| `limux` \| `none` \| `auto`) |
| Auto-close age | 14 days | `config.json` → `autoclose_age_days`. Worktrees older than this threshold with no open PR are eligible for auto-close. |
| Prompt prefix | _(empty)_ | `config.json` → `prompt_prefix`. Prepended to the first-turn prompt of every new workspace — and is the entire first turn for a blank spawn that seeds no plan prompt. |
| Theme | `dark` | `config.json` → `theme` (`dark` \| `light`). Themes the neutral-grey statusline text; saturated hues stay background-agnostic. |
| Update check | **on** | `config.json` → `check_update`. When on, the slow tick reads `plugin.json` on the install repo's default branch (via `gh api`, at most hourly) and logs a one-line notice to the `cockpit watch` log (and the header update indicator) when a newer version is published. Set `false` to skip the check. |
| Branch prefix | `<gh user>/` | `config.json` → per-repo `branch_prefix` |
| Default base branch | repo's `defaultBranchRef` | `config.json` → per-repo `default_base` |
| Sidebar color | _(unset)_ | `config.json` → per-repo `sidebar_color`. A cmux color name that tints that repo's workspace entries in the cmux sidebar (and its name in cockpit's `--watch` log). Valid names: `Red`, `Crimson`, `Orange`, `Amber`, `Olive`, `Green`, `Teal`, `Aqua`, `Blue`, `Navy`, `Indigo`, `Purple`, `Magenta`, `Rose`, `Brown`, `Charcoal`. Unset = no tint. No effect on limux. An invalid name causes cockpit to refuse to start. |
| Smart Linear flow | **off** (opt-in) | `config.json` → `use_linear`. When on, `/cockpit:new PE-1234` pre-flights `claude mcp list` for a Linear connector and (if found) seeds Claude's first turn to fetch the ticket via the Linear MCP and rename branch + workspace to include the title slug. Off → plain branch `khivi/pe-1234` + generic plan prompt (the positional Linear key still counts as context, so plan-only is seeded; only the MCP fetch + rename are skipped). |
| Linear key → repo routing | per-repo, opt-in | Per-repo `linear_keys: ["PE", ...]` paired with `use_linear: true`. `/cockpit:new PE-1234` (no `--repo`) routes the spawn to the repo whose `linear_keys` contains `PE`, regardless of cwd. Unique match wins; zero matches falls back to cwd discovery; multiple matches print a note on stderr and also fall back. `--repo <name>` always overrides. |

Worktree cleanup on merge is unconditional (no knob): every slow tick, cockpit removes the worktree and closes its workspace for any branch whose PR is MERGED, the worktree is clean, and nothing is unpushed. A worktree with no merged PR is never touched.

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
3. Run `cockpit setup` once to wire `~/.claude/settings.json`.

`cockpit setup` also seeds `~/.config/cship.toml` and `~/.config/starship.toml` with the bundled defaults. Re-running it clobbers any local edits to those files — that's intentional, it's the reset switch.

## Nudge pills (optional)

When the agent is idle, cockpit can ping the workspace about actionable PR signals (CI failed, unresolved threads, merge conflict). It's automatic and no-ops outside cmux. In the TUI, `N` sends a nudge to the cursor row now (overrides mute + the throttle) and `m` mutes/unmutes the row's PR.

Mute per-PR when a nudge is wrong, via the `cockpit nudge` CLI:

```text
cockpit nudge mute --until 7d --reason "copilot intentional"
cockpit nudge unmute
cockpit nudge status
cockpit nudge list
cockpit nudge forget    # wipe the nudge file (resets mute + rate-limit)
```

A mute is all-or-nothing — it silences every category (CI, comments, conflicts) for that PR. Mutes persist across daemon and cmux restarts and auto-clear once `until` passes.

## Uninstall

```bash
kill "$(cat ~/.config/cockpit/cockpit.pid 2>/dev/null)" 2>/dev/null || true
rm -rf ~/.config/cockpit    # nuke state (your worktrees remain)
```

Then inside Claude Code: `/plugin uninstall cockpit`.

## License

MIT. See [LICENSE](LICENSE). Contributing? Read [`AGENTS.md`](./AGENTS.md) first.
