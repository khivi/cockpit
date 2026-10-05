---
description: "Load the first-turn task cockpit stored for this workspace. Typed by cockpit itself at spawn."
argument-hint: "<id>"
allowed-tools: Bash(cockpit seed:*)
disable-model-invocation: true
---
!`cockpit seed $ARGUMENTS`
