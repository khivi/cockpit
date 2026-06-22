# Cockpit

Cockpit is a terminal UI for juggling several PRs at once from Claude Code. Each task gets its own git worktree, a `cmux`/`limux` terminal running `claude`, and a GitHub PR — and cockpit shows them all in one live table (CI, reviews, comments, dirty state) that you drive by keystroke: focus, close, or nudge any row.

![cockpit watch — every worktree, workspace, and PR in one table](docs/cockpit-tui.png)

## Requirements

- `uv`, `git ≥ 2.30`, Python ≥ 3.12
- [`gh`](https://cli.github.com/), authenticated
- Claude Code
- A workspace backend on `PATH` — `cmux` (macOS) or `limux`
- Optional: [`cship`](https://github.com/khivi/cship) + [`starship`](https://starship.rs/) for the statusline (set `use_cship: true`; wired automatically by the installer / `cockpit update`)

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

To update later, run `cockpit update` or press `u` in the TUI when it shows "⬆ update available". Both run the same in-wheel updater — no shell needed once installed. (`cockpit update --check` reports availability without installing.)

## Use

The daemon *is* the TUI — run it yourself (no auto-start):

```bash
cockpit watch          # requires a TTY; run under tmux/cmux/screen to persist
```

Drive the table by keystroke — most keys act on the highlighted row, and the footer hints adapt to that row's state and your backend (e.g. `p`/`m` hide on a row with no PR):

| Key | Action |
|---|---|
| `f` | Focus the row's workspace (cmux) |
| `w` | Open/spawn the row's workspace (works on limux too) |
| `p` | Open the PR in a browser |
| `t` | Open the linked ticket (Linear/GitHub) |
| `c` / `C` | Close (refuses dirty/unpushed/open-PR) / force-close (overrides the open-PR block only) |
| `m` | Mute / unmute the row's nudge |
| `N` | Nudge the row now (honours the idle gate) |
| `n` | New workspace (branch / PR / URL / Linear id / Slack thread) |
| `s` | Sync (full reconcile now) |
| `o` | Show tick output / logs |
| `u` | Self-update (shown when "⬆ update available") |
| `q` | Quit |

Start a task — run `/cockpit:new` inside Claude Code, from a session in any git repo (or press `n` in the TUI):

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
      "default_base": "main"
    }
  ],
  "tool": "auto"
}
```

That minimal block is enough to start; every other knob has a sane default. The most common ones:

| Knob | Default | Notes |
|---|---|---|
| `tool` | `auto` | workspace backend: `cmux` \| `limux` \| `none` \| `auto` |
| per-repo `sidebar_color` | — | `cmux` color tint (Red/Orange/Green/Blue/…); an invalid name refuses to start |
| per-repo `tickets` | `none` | ticket integration: `{provider: linear\|github, …}` (see below) |
| `slow_poll_interval_seconds` | 300 | full GitHub reconcile |
| `fast_poll_interval_seconds` | 30 | network-free git-state republish |

**Full annotated schema — every knob, with defaults — lives in [`cockpit/config.example.json`](cockpit/config.example.json)**, which is also the file copied as your config on first run and is validated in CI, so it can't drift from what the daemon accepts. That file is the source of truth; this table is just the day-one subset.

Cleanup on merge is unconditional — a MERGED PR with a clean, pushed worktree is always reaped; nothing else is touched.

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
