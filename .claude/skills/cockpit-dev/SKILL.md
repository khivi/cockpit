---
name: cockpit-dev
description: Run this worktree's cockpit build against a throwaway sandbox, so a dev build can't fight the installed daemon or touch real worktrees. TRIGGER when you want to see a cockpit change actually running, or the user says "run cockpit", "try it in the TUI", "does this render right". DO NOT TRIGGER for running the test suite (use pytest) or for `cockpit setup` (never run that from a worktree).
---

# cockpit-dev

Run `./dev.sh` from the repo root. It seeds `.cockpit-dev/` and execs this
worktree's build against it.

```bash
./dev.sh                 # your real repos + a copy of your real PR cache, read-only
./dev.sh --empty         # no repos — layout and keybinding work
./dev.sh -- nudge list   # any other subcommand, same sandbox
```

`watch` is a Textual TUI and needs a terminal. If you're in a session without
one, tell the user to run it themselves — suggest they type `! ./dev.sh` so the
output lands in the conversation, or run it in their own terminal.

## What the sandbox does and does not cover

`./dev.sh` isolates four things. Read `dev.sh`'s header comment for why each one
is load-bearing; the short version is that missing any single one reaches real
state:

| Isolated | Covers |
|---|---|
| `COCKPIT_HOME` | config, PR cache, `cockpit.pid`, close-request queue |
| `TMPDIR` | statusline/starship flat cells (they live in `$TMPDIR/cockpit-cache`, not under `COCKPIT_HOME`) |
| `tool: none` | every cmux write — spawn, close, rename, set-color, workspace-group, `send` |
| `--dry` | teardown, autoclose, `git branch -D`, tracker writes, spawn, nudge |

**Two things it does not cover, and you should say so rather than imply full
coverage:**

1. **Every cmux-facing feature is inert.** Sidebar folds, `f`/focus, `a`/ask,
   `d`/diff, colours, workspace groups — all no-ops under `tool: none`. Testing
   a change to any of those needs a real cmux, and therefore real risk. The
   sandbox is right for the table, cells, config, prompts, and the reconcile
   cycle's decisions.
2. **`gh` reads still happen** on the slow tick and spend rate limit.

## Never run `cockpit setup` from a worktree

`dev.sh` refuses it, exit 2. Setup bakes `sys.executable` — here
`.venv/bin/python` — into `~/.claude/settings.json` and `~/.config/starship.toml`,
both *outside* the sandbox. When the worktree is removed, the interpreter goes
with it and the user's statusline dies. That's the "footer disappeared" bug in
AGENTS.md's `{python}` pin invariant. The fix is re-running the brew-installed
`cockpit setup`, so don't work around the refusal.

## Reading the result

Under `--dry` the daemon prints its decisions with a `[dry]` prefix instead of
acting. That output is the point: it's how you check that a change decides
correctly without letting it act. The table renders from the seeded cache, since
`--dry` suppresses cache writes.
