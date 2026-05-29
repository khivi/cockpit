---
description: "Mute / unmute / inspect cockpit nudges for the current PR."
argument-hint: "[mute|unmute|status|list|forget] [--categories ...] [--until 7d] [--reason ...]"
model: haiku
allowed-tools: Bash
---

# /cockpit:nudge

YOU MUST immediately invoke the Bash tool with the exact command below, passing through all skill arguments verbatim. Do not paraphrase, reorder, or skip. After Bash returns, paste its stdout verbatim.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/nudge.py "$@"
```

## When to use

The cockpit daemon types reminders into idle Claude sessions when a tracked PR has actionable state (`comments`, `ci`, `conflicts`). When a reminder is wrong or you've intentionally left a thread open (e.g. a Copilot suggestion you're ignoring), use this skill to suppress it without leaving the session.

## Subcommands

- `mute` — silence nudges for the current branch's PR. Defaults to all categories, forever. Refine with `--categories comments,ci,conflicts` and/or `--until 30m|2h|7d|1w`. `--reason "..."` is optional but shows up in `list` / `status`.
- `unmute` — resume nudges for the current branch's PR.
- `status` — show whether the current branch's PR is muted, what's muted, and when it last got nudged.
- `list` — show every PR that currently has a mute set.
- `forget` — delete the persisted nudge file entirely (resets both mute and rate-limit timer).

You can pass an explicit PR number as the first positional after the subcommand if you don't want it inferred from the current branch.

## Where state lives

`~/.config/cockpit/cache/nudges/<pr-number>.json`. Survives cockpit daemon and workspace-backend restarts. The daemon auto-expires mutes once `until` passes.
