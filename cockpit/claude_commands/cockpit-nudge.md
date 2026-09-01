---
description: "Inspect or change cockpit's nudge mutes for a PR (mute, unmute, list, status, forget)."
argument-hint: "mute|unmute|list|status|forget [pr] [--until 2h] [--reason ...]"
allowed-tools: Bash
---

Invoke the Bash tool with this exact command, then paste its stdout verbatim:

```bash
cockpit nudge $ARGUMENTS
```

Prefs persist as one JSON file per PR under
`~/.config/cockpit/cache/nudges/<repo>__<number>.json`. They are keyed **per
repo**, so run this inside the repo's checkout: the PR number can be passed
explicitly but the repo never can, and an unresolvable repo exits 2 rather
than guessing.

Reference (see `cockpit nudge --help` for the full list):

- `mute [pr] [--until 30m|2h|7d|1w] [--reason ...]` — silence all nudges for a
  PR. Without `--until` the mute is indefinite.
- `unmute [pr]` — resume nudges.
- `list` — show currently muted PRs.
- `status [pr]` — show mute / last-nudge state for one PR.
- `forget [pr]` — delete the PR's nudge file, clearing the rate-limit timer
  along with the mute.

`pr` defaults to the current branch's PR; when it can't be inferred, pass the
number. The TUI's `m` (mute) and `z` (snooze) cover the same prefs on the
cursor row — this is the shell route, and the only one offering `list`,
`status` and `forget`.
