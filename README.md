# Cockpit

Cockpit is a terminal UI for juggling several PRs at once from Claude Code. Each task gets its own git worktree, a `cmux`/`limux` terminal running `claude`, and a GitHub PR — and cockpit shows them all in one live table (CI, reviews, comments, dirty state) that you drive by keystroke: focus, close, or nudge any row. Start a task with `/cockpit:new`; when its PR merges, cockpit removes the worktree and closes the terminal.

![cockpit watch — every worktree, workspace, and PR in one table](docs/cockpit-tui.png)

## Requirements

- `uv`, `git ≥ 2.30`, Python ≥ 3.12
- [`gh`](https://cli.github.com/), authenticated
- Claude Code
- A workspace backend on `PATH` — `cmux` (macOS) or `limux`
- Optional: [`cship`](https://github.com/khivi/cship) + [`starship`](https://starship.rs/) for the statusline (set `use_cship: true`; wired automatically on `bin/update.sh`)

## Install

1. Add the plugin inside Claude Code:

   ```text
   /plugin marketplace add https://github.com/khivi/cockpit
   /plugin install cockpit@khivi-cockpit
   ```

2. Run the bundled installer once — it installs the `cockpit` command (bootstrapping `uv` if missing) and wires the statusline:

   ```bash
   bash ~/.claude/plugins/cache/khivi-cockpit/cockpit/*/bin/update.sh
   ```

To update later, re-run that script or press `u` in the TUI when it shows "⬆ update available".

## Use

The daemon *is* the TUI — run it yourself (no auto-start):

```bash
cockpit watch          # requires a TTY; run under tmux/cmux/screen to persist
```

Start a task — run `/cockpit:new` inside Claude Code, from a session in any git repo:

```text
/cockpit:new <branch | PR | url> | --pr N | --branch X | --cwd P | --skill S [--repo R] [--name X] [--context] [-- <text...>]
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
