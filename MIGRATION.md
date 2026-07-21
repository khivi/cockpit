# Migrating from the plugin to Homebrew

Cockpit used to ship as a Claude Code plugin + uv-tool that self-updated in place. It's now a Homebrew formula updated with `brew upgrade`. Its Claude Code footprint is whatever `cockpit setup` writes: the statusLine + idle/stop hooks in `~/.claude/settings.json`, and `/cockpit-new` + `/cockpit-close` in `~/.claude/commands/` (hyphenated — colon-namespacing like `/cockpit:new` is plugin-only). `/cockpit:review` is replaced by the built-in `/review`.

Do this once. **Remove the old plugin *before* installing the new one** — otherwise the plugin's hooks and the new `settings.json` hooks both fire (doubled statusline / idle-pill).

## 1. Remove the old install

In a Claude Code session (or the `claude` CLI):

```text
/plugin                                   # uninstall "cockpit"
claude plugin marketplace remove <name>   # if you added one just for cockpit
```

Then the uv-tool daemon:

```bash
uv tool uninstall cockpit
```

## 2. Install via Homebrew

```bash
brew tap khivi/cockpit
brew install cockpit
cockpit setup     # statusLine + hooks + /cockpit-new/-close (interactive for the statusline)
```

Restart your Claude Code sessions so the new hooks load, then `cockpit watch`. `cockpit setup` is idempotent and backs up `settings.json` before writing.

## What's preserved

Your `~/.config/cockpit/config.json`, worktrees, branches, and cmux/limux workspaces are untouched — this swaps how cockpit is *installed*, not the state it manages. The only removed config key is `check_update` (a leftover value is ignored).

## Updating

`brew upgrade cockpit`. No `u` key, no `cockpit update` — new tagged releases land in the tap (`khivi/homebrew-cockpit`) automatically.
