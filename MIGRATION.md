# Migrating from the plugin to Homebrew

Cockpit used to ship as a Claude Code plugin + uv-tool that self-updated in place. It's now a Homebrew formula updated with `brew upgrade`. Its Claude Code footprint is whatever `cockpit setup` writes: the statusLine + idle/stop hooks in `~/.claude/settings.json`, and `/cockpit-new` + `/cockpit-close` in `~/.claude/commands/` (hyphenated — colon-namespacing like `/cockpit:new` is plugin-only). `/cockpit:review` is replaced by the built-in `/review`.

Do this once. **Remove the old plugin *before* installing the new one** — otherwise the plugin's hooks and the new `settings.json` hooks both fire (doubled statusline / idle-pill).

> **Linux prerequisite:** Homebrew runs on Linux (Linuxbrew), but it isn't there by default. If `brew` isn't already on your PATH, [install Homebrew](https://brew.sh) first, then add it to your PATH by running the `brew shellenv` line the installer prints under "Next steps" (append it to your `~/.zshrc` or `~/.bashrc`) and open a new shell — before step 2.

## 1. Remove the old install

First **stop the running daemon** so it releases its pidfile: `uv tool uninstall` deletes the binary, but a live `cockpit watch` keeps running on the now-deleted interpreter and still holds the pidfile. Press `q` in its TUI, or:

```bash
kill "$(cat ~/.config/cockpit/cockpit.pid)"   # clean SIGTERM exit
```

Then, in a Claude Code session (or the `claude` CLI):

```text
/plugin                                   # uninstall "cockpit"
claude plugin marketplace remove <name>   # if you added one just for cockpit
```

Then the uv-tool:

```bash
uv tool uninstall cockpit
```

## 2. Install via Homebrew

```bash
brew tap khivi/cockpit
brew trust khivi/cockpit   # recent Homebrew refuses to load a formula from an untrusted third-party tap
brew install cockpit
cockpit setup              # statusLine + hooks + /cockpit-new/-close (interactive for the statusline)
```

Restart your Claude Code sessions so the new hooks load, then `cockpit watch`. Confirm you're on the Homebrew build and not a leftover uv shim:

```bash
which cockpit      # → …/linuxbrew/bin/cockpit  (or /opt/homebrew/bin on macOS)
cockpit --version
```

`cockpit setup` is idempotent and backs up `settings.json` before writing. **It also re-seeds `~/.config/starship.toml` and `~/.config/cship.toml` from the bundled defaults (when `use_cship` is set) — and, unlike `settings.json`, it does *not* back up a plain file it overwrites (only a symlink target is backed up).** If you've hand-edited either, copy it aside first. Make theme changes via cockpit's `theme` config, not by editing `starship.toml`, so they survive a re-setup.

## What's preserved

Your `~/.config/cockpit/config.json`, worktrees, branches, and cmux/limux workspaces are untouched — this swaps how cockpit is *installed*, not the state it manages. The only removed config key is `check_update` (a leftover value is ignored).

## Updating

`brew upgrade cockpit`. No `u` key, no `cockpit update` — new tagged releases land in the tap (`khivi/homebrew-cockpit`) automatically.

## Uninstalling

Run `cockpit teardown` **before** `brew uninstall cockpit`. Brew removes only the Cellar binary, so without teardown the `~/.claude` statusLine, hooks, and commands are left dangling at a now-missing `cockpit`.
