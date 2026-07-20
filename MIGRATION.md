# Migrating to the Homebrew install

Cockpit used to ship as a **Claude Code plugin** (installed from a marketplace)
paired with a **uv-tool** daemon, and updated itself in-place (the `u` key /
`cockpit update`). It now ships as a **Homebrew formula** and is updated with
`brew upgrade`. Its only Claude Code footprint is two `~/.claude/settings.json`
entries — the statusLine command and the idle/stop hooks — written by
`cockpit setup`.

If you installed cockpit the old way, do this once. **Order matters: remove the
old plugin *before* installing the new one**, or the old plugin-managed hooks
and the new `settings.json` hooks both fire (doubled statusline / idle-pill).

## 1. Stop the daemon

Quit the TUI (`q` in `cockpit watch`).

## 2. Remove the old plugin

The plugin owned the Claude Code hooks (the `SessionStart` self-update hook, the
`Stop`/`UserPromptSubmit` idle-pill + statusline hooks). Uninstalling it removes
them automatically.

In a Claude Code session (or the `claude` CLI):

```text
/plugin                                   # open the plugin manager → uninstall "cockpit"
# or, non-interactively:
claude plugin uninstall cockpit@<marketplace>
```

Find `<marketplace>` and remove it too if you added one just for cockpit:

```bash
claude plugin marketplace list
claude plugin marketplace remove <marketplace>
```

## 3. Uninstall the old uv-tool daemon

```bash
uv tool uninstall cockpit
```

(That also retires the old self-update machinery — there is no separate cleanup
for `bin/update.sh`; it lived inside the uv-tool install.)

## 4. Install via Homebrew

```bash
brew tap khivi/cockpit    # maps to github.com/khivi/homebrew-cockpit
brew install cockpit
cockpit setup             # writes statusLine + hooks into ~/.claude/settings.json
```

`cockpit setup` is idempotent, backs up `settings.json` before writing, and
preserves any non-cockpit hooks you already have. It rewrites the statusLine
entry the old install left in `settings.json` (backing it up first), so there's
nothing to clean by hand there.

## 5. Restart + verify

Restart your Claude Code sessions so the new `settings.json` hooks load, then:

```bash
cockpit --version     # confirms the brew install is on PATH
cockpit watch         # daemon/TUI; the footer statusline + idle pill should work as before
```

## What carries over untouched

- **Your config** (`~/.config/cockpit/config.json`) is fully compatible — no
  changes needed. The only removed key is `check_update` (it gated the deleted
  update check); a leftover value is simply ignored.
- **Your worktrees, branches, and cmux/limux workspaces** are untouched — this
  swaps how cockpit is *installed*, not any of the state it manages.

## Updating from now on

```bash
brew upgrade cockpit
```

No in-TUI `u` key, no `cockpit update`. New releases land in the tap
(`khivi/homebrew-cockpit`) automatically on each tagged version.
