---
description: "Send a line of text (often a slash command like /compact) to every idle Claude session cockpit knows about."
argument-hint: "[--dry] [--repo NAME | --worktree PATH] <message>"
allowed-tools: Bash
---

Invoke the Bash tool with this command, then paste its stdout/stderr verbatim
— it already reports sent vs. skipped, or the unavailable-backend error:

```bash
cockpit broadcast "$ARGUMENTS"
```

The message is a **single positional argument**, so keep it quoted. If
`$ARGUMENTS` starts with `--dry`, `--repo NAME` or `--worktree PATH`, move
those outside the quotes (`cockpit broadcast --dry --repo svc-auth "<rest>"`);
everything else is the message.

Don't reimplement the fan-out loop or the idle check in bash —
`cockpit broadcast` owns both.

- `--dry` reports which workspaces would receive the message without sending
  it. Prefer it first when the user hasn't already confirmed the exact text.
- `--repo NAME` scopes the fan-out to one registered repo, named as the
  dashboard names it (case-insensitive). Use it whenever the message only makes
  sense in one repo — the default really is every idle session, including repos
  the user isn't thinking about. An unknown name exits 2 and lists the
  configured ones; read that list rather than guessing a second spelling.
- `--worktree PATH` is the narrowest scope: the session(s) rooted at exactly
  that directory. Use it when the message is for one session — never a `--repo`
  that happens to hold a single open workspace, which stops being one target
  the moment a second worktree opens. A path that isn't a directory exits 2.
  It and `--repo` are mutually exclusive.
- The command skips any workspace that's mid-turn, awaiting a permission
  prompt, or parked — that's the whole safety story, and it's built in. It
  also never sends to the caller's own session. Skipped workspaces are
  printed, not queued; re-run to retry them.

## The one hazard: slash commands and autocomplete

`cmux send` types the message into the workspace's composer exactly like a
keystroke. If the message starts with `/`, that opens Claude Code's
slash-command autocomplete, and the trailing `enter` submits whatever is
**highlighted** in that menu — not necessarily the exact command that was
typed. An exact-prefix match usually sorts first, but that's an assumption
about someone else's UI, not a guarantee.

So before broadcasting a slash command for the first time, smoke-test it
against ONE workspace with `--worktree <path>` and confirm it ran the intended
command rather than an autocomplete near-miss — cheaper than discovering the
misfire after it already hit every session.
