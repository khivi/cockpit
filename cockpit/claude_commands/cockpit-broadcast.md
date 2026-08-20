---
description: "Send a line of text (often a slash command like /compact) to every idle Claude session cockpit knows about."
argument-hint: "[--dry] <message>"
allowed-tools: Bash
---

Invoke the Bash tool with this command, then paste its stdout/stderr verbatim
— it already reports sent vs. skipped, or the unavailable-backend error:

```bash
cockpit broadcast "$ARGUMENTS"
```

The message is a **single positional argument**, so keep it quoted. If
`$ARGUMENTS` starts with `--dry`, move that flag outside the quotes
(`cockpit broadcast --dry "<rest>"`); everything else is the message.

Don't reimplement the fan-out loop or the idle check in bash —
`cockpit broadcast` owns both.

- `--dry` reports which workspaces would receive the message without sending
  it. Prefer it first when the user hasn't already confirmed the exact text.
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

`cockpit broadcast` has no single-workspace target — it always fans out to
every idle workspace at once. So before broadcasting a slash command for the
first time, tell the user to smoke-test it by hand in ONE live workspace
(type it into that workspace's composer and confirm it runs the intended
command, not an autocomplete near-miss) rather than discovering a misfire
only after it already hit every session.
