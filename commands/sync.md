---
description: "Force the cockpit to run one reconciliation cycle now."
model: haiku
allowed-tools: Bash
---

# /cockpit:sync

Triggers an immediate cockpit cycle. Two paths:

1. If `~/.config/cockpit/cockpit.pid` exists and the process is alive → `kill -USR1 <pid>` (cheap, no double-poll). Always returns 0.
2. Otherwise → fork `cockpit.py --once` (blocks until the cycle finishes). Exits 0 on GitHub API errors; may exit non-zero on hard failures (config missing, etc.).

Either way: refresh the PR cache, update cmux pills, emit warnings to stderr.

## Implementation

```bash
exec ${CLAUDE_PLUGIN_ROOT}/scripts/sync.py
```

`sync.py` USR1-kicks the watcher when one is running, else shells out to
`cockpit.py --once` for an inline cycle.
