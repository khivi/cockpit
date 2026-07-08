# Cockpit

Cockpit is a terminal UI for juggling several PRs at once from Claude Code. Each task gets its own git worktree, a `cmux`/`limux` terminal running `claude`, and a GitHub PR — and cockpit shows them all in one live table (CI, reviews, comments, dirty state) that you drive by keystroke: focus, close, or nudge any row.

![cockpit watch — every worktree, workspace, and PR in one table](docs/cockpit-tui.png)

## Requirements

- `uv`, `git ≥ 2.30`, Python ≥ 3.12
- [`gh`](https://cli.github.com/), authenticated
- Claude Code
- A workspace backend on `PATH` — a "workspace backend" is the terminal app that gives each worktree its own tab/session cockpit can spawn, focus, and close. Without one, cockpit runs in cache-only mode: the footer/statusline still work, but the side panel and slash-command spawning are disabled.
  - [`cmux`](https://github.com/manaflow-ai/cmux) ([cmux.dev](https://cmux.dev)) — an open-source, Ghostty-based macOS terminal with vertical-tab workspaces built for AI coding agents. `brew install --cask cmux`.
  - [`limux`](https://github.com/am-will/limux) — a GPU-accelerated Linux port of cmux (GTK4 over libghostty, tracks cmux parity). AppImage/`.deb` from [releases](https://github.com/am-will/limux/releases), or AUR `limux-bin`. Cockpit's Linux backend: it can spawn/close workspaces but lacks cmux's focus/pill/sidebar-color verbs, which degrade gracefully.
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

Drive the table by keystroke — most keys act on the highlighted row, and the footer hints adapt to that row's state, its live workspace, and your backend. `f` (Focus) is the single "take me there" verb — it focuses the row's session, spawning one first if it doesn't have one; `N` (Nudge) shows only when the row *has* a workspace; `p`/`m` hide on a row with no PR:

| Key | Action | Hint shown when |
|---|---|---|
| `f` | Focus the row's workspace, spawning one first if it has none | any backend (hidden only on `tool=none`) |
| `p` | Open the PR in a browser | the row has a PR |
| `t` | Open the linked ticket (Linear/GitHub/Jira/Trello) | the row has a delivered ticket |
| `c` / `C` | Close / force-close. Feature worktree: close workspace + remove worktree (refuses dirty/unpushed/open-PR; `C` overrides the open-PR block only). Primary checkout (an `in_place` `master`): **workspace-only close** — closes the session, keeps the checkout, gated on dirty ("all committed") | feature row: always · primary checkout: only when it has a workspace |
| `m` | Mute / unmute the row's nudge | the row has a PR |
| `N` | Nudge the row now (honours the idle gate) | cmux **and** the row has a workspace |
| `n` | New workspace (branch / PR / URL / Linear id / Slack thread) | always (global — not row-scoped) |
| `s` | Sync (full reconcile now) | always |
| `o` | Show tick output / logs | always |
| `u` | Self-update | when "⬆ update available" |
| `q` | Quit | always |

Start a task — run `/cockpit:new` inside Claude Code, from a session in any git repo (or press `n` in the TUI):

```text
/cockpit:new <branch | PR | url> | --pr N | --branch X | --cwd P | --skill S [--repo R] [--name X] [--context] [-- <text...>]
```

`url` is auto-detected: a GitHub PR URL, a **GitHub Actions run URL**, or a Slack thread permalink. These (like `--pr`) are *CLI spawn sources* you pass at workspace-creation time — they are not configuration, so they have no entry in `config.json` / `config.example.json`. The only GitHub *config* surface is the `tickets` provider (`{provider: "github", …}`); see below.

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
| per-repo `tickets` | `none` | ticket integration: `{provider: linear\|github\|jira\|trello, …}` (see below) |
| per-repo `review_prs` | `false` | auto-spawn a review worktree for coworkers' open PRs (see below) |
| `slow_poll_interval_seconds` | 300 | full GitHub reconcile |
| `fast_poll_interval_seconds` | 30 | network-free git-state republish |

**Every knob, with defaults, lives in [`cockpit/config.example.json`](cockpit/config.example.json)** — a plain-JSON example config (not annotated; this README table is the field documentation), validated in CI so it can't drift from what the daemon accepts. It is *not* copied as your config on first run: a fresh install seeds an empty `{"repos": []}` instead, and repos are registered by running `/cockpit:new` (or `cockpit new`) in a repo, or by adding a `repos` entry to `~/.config/cockpit/config.json` by hand.

### Ticket providers

`tickets` links a PR to an external ticket via a footer in the PR body (`Linear: [...]`, `Closes #123`, `Jira: [...]`, `Trello: [...]`) and can transition that ticket on merge (`close_on_merge`, opt-in, off by default). Four providers:

| Provider | Config fields | Env |
|---|---|---|
| `linear` | `keys` (team prefixes), `dev_done_state`, `merge_done_state` (default `"Done"`) | `LINEAR_API_KEY` |
| `github` | `dev_done_label` (default `"ready for review"`), `start_label` | none (uses `gh`) |
| `jira` | `site_url`, `email`, `dev_done_status` (default `"Dev Done"`), `merge_done_status` (default `"Done"`) | `JIRA_API_TOKEN` |
| `trello` | `dev_done_list`, `merge_done_list` (no defaults — Trello list names are board-specific) | `TRELLO_API_KEY`, `TRELLO_API_TOKEN` |

All providers also accept the shared `close_on_merge: bool`. See `cockpit/config.example.json` for full examples of each block.

### Auto-review security posture

`review_prs: true` (per-repo) auto-spawns a review worktree + agent for coworkers' open PRs. By default this only fires for PRs from repo collaborators/members. Set `"review_external": true` (per-repo) to opt in to auto-reviewing external contributors' PRs too — external PR content (title, description, diff) reaches an auto-spawned agent, so only enable this if you trust that exposure for the repo.

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
