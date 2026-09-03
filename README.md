# Cockpit

[![CI](https://github.com/khivi/cockpit/actions/workflows/ci.yml/badge.svg)](https://github.com/khivi/cockpit/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A change lives in four places at once — a git worktree, a GitHub PR, a ticket, and usually a Slack thread — and nothing joins them but you. Cockpit is that join: a terminal UI with one row per change, showing all four live (CI, reviews, comments, dirty state, ticket status) and driven by keystroke.

![cockpit watch — every worktree, workspace, and PR in one table](docs/cockpit-tui.png)

It orchestrates *context*, not agents. Cockpit doesn't plan work, split it up, or decide anything — you do that. What it takes off you is the clerical half of working on several things at once: which worktree that PR was cut from, which one is dirty, which review is waiting on you, which ticket nobody moved. The terminal in each worktree is running `claude`, but that's one more thing the row carries, not the thing being managed.

It works in both directions. Point `cockpit new` at any one of the four — a branch name, `#42`, `PE-1234`, a Slack permalink — and it materialises the rest: worktree cut, terminal opened in it, PR picked up when it appears, ticket linked. The row then keeps itself true, re-derived every cycle from git + GitHub + your terminal backend — nothing is stored, so nothing drifts — until the PR merges and it tears the row down.

In short — the full tour is [`FEATURES.md`](FEATURES.md):

- [**One row per change**](FEATURES.md#the-dashboard), every repo, sorted by whose turn it is.
- [**One argument starts anything**](FEATURES.md#starting-work-one-argument-any-source) — branch, PR, issue, ticket, Slack link, failed CI run.
- [**It nudges you back**](FEATURES.md#the-nudge) when your PR goes red — only into a session genuinely parked at its prompt.
- [**Tickets stay in sync**](FEATURES.md#tickets) across Linear, Jira, GitHub Issues, and Trello, and move on merge.
- [**Reviews are waiting for you**](FEATURES.md#reviewing-your-teams-prs) on each coworker PR — dry-run, never posted on your behalf.
- [**Closing refuses to lose work**](FEATURES.md#closing-up), and merged PRs clean themselves up.

## Requirements

- `git ≥ 2.30`, [`gh`](https://cli.github.com/) (authenticated), Claude Code
- A workspace backend on `PATH` — this is what opens and focuses a terminal per worktree. Without one the table and statusline still work; nothing can be spawned:
  - [`cmux`](https://github.com/manaflow-ai/cmux) — macOS: `brew install --cask cmux`
  - [`limux`](https://github.com/am-will/limux) — Linux port ([releases](https://github.com/am-will/limux/releases) / AUR `limux-bin`); spawns/closes but lacks cmux's focus/pill/color verbs
- Optional statusline: [`cship`](https://github.com/stephenleo/cship) + [`starship`](https://starship.rs/) — `curl -fsSL https://cship.dev/install.sh | bash`, then `use_cship: true`

## Install

```bash
brew tap khivi/cockpit
brew trust khivi/cockpit   # recent Homebrew won't load a formula from an untrusted third-party tap
brew install cockpit
cockpit setup
```

Or, any platform with Python 3.12+ (PyPI dist is `cmux-cockpit`; the command stays `cockpit`):

```bash
pipx install cmux-cockpit   # or: uv tool install cmux-cockpit
cockpit setup
```

`cockpit setup` wires the idle hooks and the `/cockpit-new` / `/cockpit-close` / `/cockpit-broadcast` / `/cockpit-nudge` commands into `~/.claude/`, and on a TTY offers the optional statusline. It's idempotent — re-run it any time. Update with `brew upgrade cockpit`.

Coming from the old Claude Code plugin? See [`MIGRATION.md`](MIGRATION.md), and remove the plugin *before* installing — otherwise both sets of hooks fire.

## Use

Start a task (auto-registers the repo), then open the dashboard:

```bash
cockpit new <branch | PR | url>   # or press `n` in the TUI; full flags: cockpit new --help
cockpit watch                     # needs a TTY; run under tmux/cmux to persist
```

The argument is auto-detected: a branch name, `#N` for a PR, `i#N` for an issue, a ticket key like `PE-1234`, or a URL — GitHub PR / issue / Actions run, Linear, Jira, Trello card, or a Slack permalink. Anything unrecognised becomes a new branch. ([What each source seeds →](FEATURES.md#starting-work-one-argument-any-source))

Drive the table by keystroke — footer hints adapt to the highlighted row's state, its workspace, and your backend:

| Key | Action |
|---|---|
| `f` | Focus the row's workspace — spawns one if it has none |
| `p` · `t` | Open the PR · the linked ticket |
| `d` · `a` | Open the PR diff · send a line to the row's session (on a repo header, to every session in it) |
| `c` · `C` | Close the worktree + workspace — [never discards work](FEATURES.md#closing-up) |
| `m` · `z` | Mute indefinitely · [snooze until the PR changes](FEATURES.md#the-nudge) |
| `n` · `h` | Start something new · park the row's repo, or reveal / un-park a parked one |
| `s` · `q` | Reconcile every repo now · quit |

Hover any footer key for a sentence on what it does. **☰ Menu**, top right, holds logs, config, theme, the [feature guide](FEATURES.md), and the [release notes](https://github.com/khivi/cockpit/releases) — click it, or press `ctrl+p`.

## Configuration

`~/.config/cockpit/config.json` holds managed repos + tunables; `cockpit new` auto-registers repos. Minimal:

```json
{"repos": [{"name": "myrepo", "path": "/abs/path", "branch_prefix": "you/", "default_base": "main"}]}
```

Everything else has a sane default. Three worth turning on:

- **[Tickets](FEATURES.md#tickets)** — `tickets` links each PR to Linear / Jira / GitHub Issues / Trello, and transitions it on merge.
- **[Auto-review](FEATURES.md#reviewing-your-teams-prs)** — `review_prs: true` spawns a review agent per coworker PR. Collaborators only; `review_external` opts in fork PRs — untrusted content reaching a Bash-capable agent, so enable deliberately.
- **Skills** — `skills.{session,review,plan,actions}` seed your own slash command as a spawn's first turn, e.g. `"skills": {"review": "/pr-review"}`. Unset fields fall back to cockpit's built-in prose.

Full field reference: [`docs/config.md`](docs/config.md) (and [`cockpit/config.example.json`](cockpit/config.example.json)). Only the statusline is prompted for at `cockpit setup` (it installs binaries); every other setting is a plain `config.json` edit, validated at startup.

## Statusline (optional)

Cockpit can also drive Claude Code's own statusLine, so a session shows where it stands without switching to the dashboard — budget on the first line, PR on the second:

```text
🤖 Opus 4.7   🧠 7%/1M   ⌛ 4%/5h   khivi/fix-login   ✓ clean
TICKET-123   APPROVED   #9999   ✓   Add login flow
```

Set `use_cship: true` (or accept the prompt at `cockpit setup`); drop fields you don't want with `statusline_hide`.

## Uninstall

```bash
cockpit teardown          # remove the ~/.claude statusLine/hooks/commands (do this before uninstall)
rm -rf ~/.config/cockpit  # state only; your worktrees remain
brew uninstall cockpit
```

## License

MIT — see [LICENSE](LICENSE). Contributing? Read [`CONTRIBUTING.md`](./CONTRIBUTING.md).
