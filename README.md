# Cockpit

[![CI](https://github.com/khivi/cockpit/actions/workflows/ci.yml/badge.svg)](https://github.com/khivi/cockpit/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Cockpit is a terminal UI for juggling several PRs at once from Claude Code. Each task gets its own git worktree, a `cmux`/`limux` terminal running `claude`, and a GitHub PR — and cockpit shows them all in one live table (CI, reviews, comments, dirty state) that you drive by keystroke: focus, close, or nudge any row.

![cockpit watch — every worktree, workspace, and PR in one table](docs/cockpit-tui.png)

## Why I built this

Claude Code made it cheap to run several coding agents at once. The clean way to do that is one git worktree per task, each with its own [`cmux`](https://github.com/manaflow-ai/cmux) terminal running `claude`, each ending in its own PR. That scales the *work* — but not the *tracking*. After a few parallel tasks you have N terminals, N PRs on GitHub, and N tickets in Linear/Jira/Trello, with nothing tying a worktree to its PR to its ticket. Which agent is idle and waiting on you? Which PR just went red on CI? Which one has a review comment nobody's answered? You find out by tabbing through terminals and refreshing browser tabs.

Cockpit is the missing glue between cmux, git worktrees, GitHub PRs, and your ticket tracker. It renders one live table — always computed fresh from the real state of git, cmux, and GitHub, so it never drifts out of sync and there's nothing to refresh by hand. From that table you focus a session, open its PR or ticket, nudge an idle agent, or close a finished worktree — without leaving the terminal.

It also closes the loop the other way: cockpit can spawn a worktree + cmux session + PR-tracking row straight from a PR number, a Slack thread, a ticket, or a bare branch (`cockpit new`, or `n` in the TUI), and tear the whole thing down when the PR merges. Worktrees, PRs, and tickets stop being three separate things you manage by hand and become rows in one board.

## Requirements

- `git ≥ 2.30`
- [`gh`](https://cli.github.com/), authenticated
- Claude Code
- A workspace backend on `PATH` — a "workspace backend" is the terminal app that gives each worktree its own tab/session cockpit can spawn, focus, and close. Without one, cockpit runs in cache-only mode: the footer/statusline still work, but the side panel and workspace spawning are disabled.
  - [`cmux`](https://github.com/manaflow-ai/cmux) ([cmux.dev](https://cmux.dev)) — an open-source, Ghostty-based macOS terminal with vertical-tab workspaces built for AI coding agents. `brew install --cask cmux`.
  - [`limux`](https://github.com/am-will/limux) — a GPU-accelerated Linux port of cmux (GTK4 over libghostty, tracks cmux parity). AppImage/`.deb` from [releases](https://github.com/am-will/limux/releases), or AUR `limux-bin`. Cockpit's Linux backend: it can spawn/close workspaces but lacks cmux's focus/pill/sidebar-color verbs, which degrade gracefully.
- Optional: [`cship`](https://github.com/stephenleo/cship) + [`starship`](https://starship.rs/) for the statusline — install cship with `curl -fsSL https://cship.dev/install.sh | bash` (macOS + Linux, arm64/x86_64), then set `use_cship: true` (wired by `cockpit setup`)

## Install

```bash
brew tap khivi/cockpit    # maps to github.com/khivi/homebrew-cockpit
brew install cockpit
cockpit setup             # wires the statusLine + Claude Code hooks into ~/.claude/settings.json
```

Not on Homebrew? Any platform with Python 3.12+ works via [pipx](https://pipx.pypa.io/) or [uv](https://docs.astral.sh/uv/) — the PyPI distribution is `cmux-cockpit` (the bare name `cockpit` is taken), and it still gives you the `cockpit` command:

```bash
pipx install cmux-cockpit   # or: uv tool install cmux-cockpit
cockpit setup
```

`cockpit setup` is idempotent and preserves any hooks you've already configured. It also installs the optional `/cockpit-new` and `/cockpit-close` in-session commands into `~/.claude/commands/` — thin wrappers around `cockpit new`/`cockpit close`, so you don't have to leave the Claude Code session to spawn or tear down a worktree. To update later, `brew upgrade cockpit`. Coming from the old Claude Code plugin install? See [`MIGRATION.md`](MIGRATION.md).

## Use

**1. Start your first task.** Run `cockpit new` from a shell in any git repo — it auto-registers that repo and spawns a worktree + workspace (or press `n` inside the TUI later):

```text
cockpit new <branch | PR | url> | --pr N | --branch X | --cwd P | --skill S [--repo R] [--name X] [--context] [-- <text...>]
```

`url` is auto-detected: a GitHub PR URL, a **GitHub Actions run URL**, or a Slack thread permalink. These (like `--pr`) are *spawn sources* you pass at creation time — not configuration, so they have no entry in `config.json`.

**2. Open the dashboard.** The daemon *is* the TUI, so run it yourself (no auto-start):

```bash
cockpit watch          # requires a TTY; run under tmux/cmux/screen to persist
```

Drive the table by keystroke — most keys act on the highlighted row, and the footer hints adapt to that row's state, its live workspace, and your backend. `f` (Focus) is the single "take me there" verb — it focuses the row's session, spawning one first if it doesn't have one; `N` (Nudge) shows only when the row *has* a workspace; `p`/`m` hide on a row with no PR:

| Key | Action | Hint shown when |
|---|---|---|
| `f` | Focus the row's workspace, spawning one first if it has none | any backend (hidden only on `tool=none`) |
| `p` | Open the PR in a browser | the row has a PR |
| `t` | Open the linked ticket (Linear/GitHub/Jira/Trello) | the row has a delivered ticket |
| `c` / `C` | Close / force-close. Feature worktree: close workspace + remove worktree (refuses dirty/unpushed/open-PR; `C` overrides the open-PR block only). Primary checkout (a `use_worktree: false` `master`): **workspace-only close** — closes the session, keeps the checkout, gated on dirty ("all committed") | feature row: always · primary checkout: only when it has a workspace |
| `m` | Mute / unmute the row's nudge | the row has a PR |
| `N` | Nudge the row now (honours the idle gate) | cmux **and** the row has a workspace |
| `n` | New workspace (branch / PR / URL / Linear id / Slack thread) | always (global — not row-scoped) |
| `s` | Sync (full reconcile now) | always |
| `o` | Show tick output / logs | always |
| `q` | Quit | always |

## Configuration

`~/.config/cockpit/config.json` holds managed repos + tunables; `cockpit new` auto-registers the current repo:

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

**Every knob, with defaults, lives in [`cockpit/config.example.json`](cockpit/config.example.json)** (this README table documents the common ones). A fresh install starts empty (`{"repos": []}`) — you register repos by running `cockpit new` in them, or by adding a `repos` entry to `~/.config/cockpit/config.json` by hand.

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
cockpit teardown                  # removes the settings.json statusLine/hooks entries
                                   # and ~/.claude/commands/{cockpit-new,cockpit-close}.md
rm -rf ~/.config/cockpit          # state only; your worktrees remain
brew uninstall cockpit
```

`cockpit teardown` is the inverse of `cockpit setup` — run it *before* `brew uninstall`, or the `statusLine`/hooks left in `~/.claude/settings.json` (and the two command files under `~/.claude/commands/`) point at a now-missing `cockpit` binary.

## License

MIT — see [LICENSE](LICENSE). Contributing? Read [`CONTRIBUTING.md`](./CONTRIBUTING.md) first.
