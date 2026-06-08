# Cockpit

A Claude Code plugin for running several PRs at once. Each task gets its own git worktree, a `cmux`/`limux` terminal with `claude` already running, and a GitHub PR — and cockpit shows all of them, with live CI and review status, in one TUI table. Start a task with `/cockpit:new`; when its PR merges, cockpit deletes the worktree and closes the terminal for you.

## What it does

- **`cockpit watch`** — a TUI showing every workspace's PR, approval, CI, comments, and dirty state in one navigable table; row keystrokes focus, close, mute, and nudge.
- **`/cockpit:new <branch | PR | linear-id>`** — spawns a sibling worktree + a `cmux`/`limux` workspace with `claude` already running.
- Nudges an idle workspace about actionable PR signals, and auto-removes a worktree + workspace once its PR merges clean.

## Requirements

- `uv`, `git ≥ 2.30`, Python ≥ 3.12
- [`gh`](https://cli.github.com/), authenticated
- Claude Code
- A workspace backend on `PATH` — `cmux` (macOS) or `limux` (Linux; no nudge pills)
- Optional: [`cship`](https://github.com/khivi/cship) + [`starship`](https://starship.rs/) for the statusline (set `use_cship: true`; wired automatically on `bin/update.sh`)

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

The table lists each workspace's PR, author, approval/CI, unaddressed comments (💬), dirty state, and title (plus Linear ticket + status columns when a repo sets `linear_keys`). Row keys: `f` focus · `p`/`l` open PR/Linear · `c`/`C` close/force-close · `m` mute nudges · `N` nudge · `n` new · `s` sync · `r` repo config · `o` output · `u` update · `q` quit.

Create work from any git repo:

```text
/cockpit:new fix-login        # new/existing branch
/cockpit:new 123              # PR number (or full URL)
/cockpit:new PE-1234          # Linear ticket id
```

## Configuration

`~/.config/cockpit/config.json` holds managed repos + tunables; `/cockpit:new` auto-registers the current repo:

```json
{
  "repos": [
    {
      "name": "myrepo",
      "path": "/abs/path",
      "branch_prefix": "you/",
      "default_base": "main",
      "linear_keys": ["TEAM"],
      "sidebar_color": "Blue"
    }
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

## Claude Code statusline (optional)

```text
🤖 Opus 4.7   🧠 7%/1M   ⌛ 4%/5h   khivi/fix-login   ✓ clean
TICKET-123   APPROVED   #9999   ✓   Add login flow
```

## Uninstall

Stop the TUI (`q`), then:

```bash
rm -rf ~/.config/cockpit          # state only; your worktrees remain
```

Then `/plugin uninstall cockpit` in Claude Code.

## License

MIT — see [LICENSE](LICENSE). Contributing? Read [`AGENTS.md`](./AGENTS.md) first.
