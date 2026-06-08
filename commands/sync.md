---
description: "Force the cockpit to run one reconciliation cycle now."
argument-hint: ""
model: haiku
allowed-tools: Bash
---

# /cockpit:sync

YOU MUST immediately invoke the Bash tool with the exact command below. Do not paraphrase or skip. After Bash returns, paste its stdout verbatim.

```bash
cockpit sync
```

## Behaviour

Triggers an immediate cockpit cycle. Two paths:

1. If `~/.config/cockpit/cockpit.pid` exists and the process is alive → `kill -USR1 <pid>` (cheap, no double-poll). Always returns 0.
2. Otherwise → fork `cockpit once` (blocks until the cycle finishes). Exits 0 on GitHub API errors; may exit non-zero on hard failures (config missing, etc.).

Either way: refresh the PR cache, update cmux pills, emit warnings to stderr.
