# Cockpit

[![CI](https://github.com/khivi/cockpit/actions/workflows/ci.yml/badge.svg)](https://github.com/khivi/cockpit/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A terminal UI for running several Claude Code agents at once. Each task gets its own git worktree, a `cmux`/`limux` terminal running `claude`, and a GitHub PR — cockpit shows them all in one live table (CI, reviews, comments, dirty state) you drive by keystroke.

![cockpit watch — every worktree, workspace, and PR in one table](docs/cockpit-tui.png)

One worktree per task scales the *work*. It doesn't scale the *tracking* — run a few in parallel and you have N terminals, N PRs, and N tickets with nothing tying them together.

Cockpit re-derives the whole fleet every cycle from git + cmux + GitHub. Nothing is stored, so nothing drifts. And it closes the loop the other way: `cockpit new` spawns the worktree, the session, and the PR-tracking row from a branch, PR, ticket, or Slack thread — then tears them down when the PR merges.

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

`cockpit setup` wires the idle hooks and the `/cockpit-new` / `/cockpit-close` commands into `~/.claude/`, and on a TTY offers the optional statusline. It's idempotent — re-run it any time. Update with `brew upgrade cockpit`.

Coming from the old Claude Code plugin? See [`MIGRATION.md`](MIGRATION.md), and remove the plugin *before* installing — otherwise both sets of hooks fire.

## Use

Start a task (auto-registers the repo), then open the dashboard:

```bash
cockpit new <branch | PR | url>   # or press `n` in the TUI; full flags: cockpit new --help
cockpit watch                     # needs a TTY; run under tmux/cmux to persist
```

The argument is auto-detected: a branch name, `#N` for a PR, `i#N` for an issue, a ticket key like `PE-1234`, or a URL — GitHub PR / issue / Actions run, Linear, Jira, Trello card, or a Slack permalink. Anything unrecognised becomes a new branch.

Drive the table by keystroke — footer hints adapt to the highlighted row's state, its workspace, and your backend:

| Key | Action |
|---|---|
| `f` | Focus the row's workspace (spawns one first if it has none) |
| `p` | Open the PR in a browser |
| `t` | Open the linked ticket (Linear/GitHub/Jira/Trello) |
| `c` / `C` | Close the worktree + workspace. Refuses uncommitted work, unpushed commits, and an open PR — `C` overrides the open-PR refusal only, never the two that would lose work |
| `m` | Mute / unmute the row's nudge — indefinite |
| `z` | Snooze / wake — silences the row until the PR's reviews change or new work lands |
| `N` | Nudge the row now (honours the idle gate) |
| `n` | New workspace (branch / PR / URL / ticket / Slack thread) |
| `h` | Park the row's repo — stops polling and closes its idle cmux workspaces. On the `▸ N hidden` row it expands the parked repos (click works too); on one of those it un-parks |
| `s` / `o` / `q` | Sync · show logs · quit |

## Configuration

`~/.config/cockpit/config.json` holds managed repos + tunables; `cockpit new` auto-registers repos. Minimal:

```json
{"repos": [{"name": "myrepo", "path": "/abs/path", "branch_prefix": "you/", "default_base": "main"}]}
```

Everything else has a sane default. Full field reference: [`docs/config.md`](docs/config.md) (and [`cockpit/config.example.json`](cockpit/config.example.json)). Three worth knowing:

- **Tickets** — link each PR to Linear / Jira / GitHub Issues / Trello via a body footer, and transition the ticket on merge (per-repo `tickets`).
- **Auto-review** — `review_prs: true` spawns a review agent per coworker PR (collaborators only; `review_external` opts in fork PRs — untrusted content reaching a Bash-capable agent, so enable deliberately).
- **Skills** — `skills.{session,review,plan,actions}` seed a slash command as the first turn of each spawn (session runs in *every* spawn; review/plan/actions per scenario). Unset fields fall back to cockpit's built-in prose. Point them at your own commands, e.g. `"skills": {"review": "/pr-review"}`.

Only the statusline is prompted for at `cockpit setup` (it installs binaries); every other setting is a plain `config.json` edit, validated at startup.

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
