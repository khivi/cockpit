# Cockpit

A Claude Code plugin that makes `cmux`/`limux` PR-aware: each terminal workspace is bound 1:1 to a git worktree and a GitHub PR, with live CI/review status in a TUI table and (optionally) your Claude Code statusline. Merge the PR and cockpit reaps the worktree + workspace.

It's a layer on top of `cmux`/`limux` — cockpit owns the worktree ↔ workspace ↔ PR mapping; the backend owns the terminals.

## What it does

- `/cockpit:new <branch | PR | linear-id>` → a sibling git worktree + a `cmux`/`limux` workspace with `claude` already running.
- `cockpit watch` (a TUI daemon) polls GitHub and shows every workspace's PR / approval / CI / comment / dirty state in a navigable table.
- Nudges an idle workspace about actionable PR signals (CI red, unresolved threads, conflicts).
- Auto-removes a worktree + workspace once its PR merges and the tree is clean.

Inventory is derived each cycle from `git worktree list` + the backend's workspace tree — no state file to drift.

## Requirements

`uv`, `git ≥ 2.30`, Python ≥ 3.12, [`gh`](https://cli.github.com/) (authenticated), Claude Code, and a workspace backend on `PATH` — `cmux` (macOS) or `limux` (Linux; no nudge pills). Optional: [`cship`](https://github.com/khivi/cship) for the statusline. Missing `gh`/`git` refuses to start; a missing backend drops to cache-only mode.

## Install

```bash
uv tool install git+https://github.com/khivi/cockpit          # the `cockpit` command
```

Then, inside Claude Code:

```text
/plugin marketplace add https://github.com/khivi/cockpit
/plugin install cockpit@khivi-cockpit
```

The slash command and statusline hook call the `cockpit` binary, so it must stay on `PATH`. Update with `bin/update.sh` (or press `u` in the TUI when it shows "⬆ update available").

## Use

The daemon *is* the TUI — run it yourself (no auto-start):

```bash
cockpit watch          # requires a TTY; run under tmux/cmux/screen to persist
```

The table lists each workspace's PR, author, approval/CI, unaddressed comments (💬), dirty state, and title. Row keys: `f` focus · `p`/`l` open PR/Linear · `c`/`C` close/force-close · `m` mute nudges · `N` nudge · `n` new · `s` sync · `u` update · `q` quit.

Create work from any git repo:

```text
/cockpit:new fix-login        # new/existing branch
/cockpit:new 123              # PR number (or full URL)
/cockpit:new PE-1234          # Linear ticket id
```

`/cockpit:new` is the only slash command; list/focus/close/nudge all live in the TUI. `cockpit nudge {mute,unmute,status,list,forget}` remains as a shell CLI.

## Configuration

`~/.config/cockpit/config.json` holds managed repos + tunables; `/cockpit:new` auto-registers the current repo:

```json
{
  "repos": [
    { "name": "myrepo", "path": "/abs/path", "branch_prefix": "you/", "default_base": "main", "linear_keys": ["TEAM"], "sidebar_color": "Blue" }
  ],
  "slow_poll_interval_seconds": 300,
  "fast_poll_interval_seconds": 30,
  "use_cship": false,
  "use_linear": false,
  "tool": "auto"
}
```

| Knob | Default | Notes |
|---|---|---|
| `slow_poll_interval_seconds` | 300 | full GitHub reconcile |
| `fast_poll_interval_seconds` | 30 | network-free git-state republish |
| `tool` | `auto` | `cmux` \| `limux` \| `none` \| `auto` |
| `autoclose_age_days` | 14 | PR-less worktrees older than this become auto-close eligible |
| `prompt_prefix` | — | prepended to every new workspace's first turn |
| `use_linear` | off | `/cockpit:new PE-1234` fetches the ticket via the Linear MCP, then renames branch + workspace to the title slug |
| per-repo `sidebar_color` | — | `cmux` color tint (Red/Orange/Green/Blue/…); an invalid name refuses to start |
| `check_update` | on | hourly version check → header indicator |

Full schema: [`config.example.json`](config.example.json). Cleanup on merge is unconditional — a MERGED PR with a clean, pushed worktree is always reaped; nothing else is touched.

## Statusline (optional)

With `use_cship: true`, PR/CI/review state renders under the Claude prompt via [`cship`](https://github.com/khivi/cship) + [`starship`](https://starship.rs/) reading cockpit's cache (no network in the prompt path):

```text
🤖 Opus 4.7   🧠 7%/1M   ⌛ 4%/5h   khivi/fix-login   ✓ clean
TICKET-123   APPROVED   #9999   ✓   Add login flow
```

Install `cship` + `starship`, set `use_cship: true`, then run `cockpit setup` once — it wires `~/.claude/settings.json` and seeds the `cship`/`starship` configs (re-running resets them).

## Nudge pills (optional, `cmux` only)

An idle workspace gets pinged about actionable PR signals. Mute per-PR with `cockpit nudge mute --until 7d` (plus `unmute`/`status`/`list`/`forget`); mutes persist across restarts and auto-clear once `until` passes.

## Uninstall

```bash
kill "$(cat ~/.config/cockpit/cockpit.pid 2>/dev/null)" 2>/dev/null || true
rm -rf ~/.config/cockpit          # state only; your worktrees remain
```

Then `/plugin uninstall cockpit` in Claude Code.

## License

MIT — see [LICENSE](LICENSE). Contributing? Read [`AGENTS.md`](./AGENTS.md) first.
