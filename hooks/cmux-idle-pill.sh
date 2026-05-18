#!/bin/bash
# cmux idle pill — emits `idle=☕ rest` when the agent parks at the prompt
# (Stop event) and clears it on UserPromptSubmit. Sole producer of the `idle=`
# pill the cockpit reads in `nudge_if_idle` to decide whether a workspace is
# at rest. Without this hook the cockpit's nudge logic never fires.
#
# Orthogonal to PR state: a workspace can rest with CI failing. cmux's own
# `claude_code=Needs input` fires for any idle prompt; y/n permission prompts
# happen mid-turn via PreToolUse, not at Stop, so we never mask a real
# confirmation by emitting the pill here.
#
# /loop suppression: dynamic /loop iterations end with a ScheduleWakeup call,
# and the session is *not* truly at rest during the wait window — broadcasters
# that read this pill (e.g. `cmux send`) would happily target a session waiting
# for its own next wakeup. So on Stop we scan the transcript's last assistant
# turn; if it called ScheduleWakeup or CronCreate, we leave the pill cleared.
# The next wakeup-triggered turn fires UserPromptSubmit, which clears it anyway
# — so in steady state the pill stays cleared through the entire loop lifecycle.
# Cron-mode /loop (CronCreate done once at setup, not re-armed per iteration)
# is not covered by this heuristic; park such workspaces manually.
#
# Hook wiring (Claude Code event → arg):
#   Stop             → stop
#   UserPromptSubmit → prompt

set -eu

[ -z "${CMUX_WORKSPACE_ID:-}" ] && exit 0

cmux() {
  # Fire-and-forget: the cmux daemon occasionally stalls under contention
  # (cockpit watcher + every claude session's hook all hitting the socket).
  # Claude Code's hook timeout then kills the script and surfaces a
  # "non-blocking status code" error on every prompt. Detach via subshell +
  # background + stdio redirection so the hook returns in <1ms regardless of
  # daemon health. Pill update is best-effort by design.
  ( command cmux "$@" --workspace "$CMUX_WORKSPACE_ID" </dev/null >/dev/null 2>&1 & )
}

loop_active_in_transcript() {
  # Exits 0 iff the most recent assistant turn in the transcript referenced by
  # the Stop-hook JSON payload (passed as $1) contains a ScheduleWakeup or
  # CronCreate tool_use. Heredoc feeds the python script via stdin, so we pass
  # the JSON payload as argv[1] rather than stdin to avoid the collision.
  python3 - "$1" 2>/dev/null <<'PY'
import json, sys, os
try:
    payload = json.loads(sys.argv[1])
except Exception:
    sys.exit(1)
transcript = payload.get("transcript_path")
if not transcript or not os.path.isfile(transcript):
    sys.exit(1)
LOOP_TOOLS = {"ScheduleWakeup", "CronCreate"}
last_tools = None
with open(transcript) as f:
    for line in f:
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "assistant":
            continue
        msg = d.get("message") or {}
        content = msg.get("content") or []
        last_tools = [c.get("name") for c in content
                      if isinstance(c, dict) and c.get("type") == "tool_use"]
sys.exit(0 if last_tools and any(t in LOOP_TOOLS for t in last_tools) else 1)
PY
}

case "${1:-}" in
  stop)
    hook_input="$(cat)"
    if [ -n "$hook_input" ] && loop_active_in_transcript "$hook_input"; then
      # /loop active — keep the pill cleared so consumers of the idle pill
      # don't see false rest on a session waiting for its own next wakeup.
      cmux clear-status idle
      exit 0
    fi
    cmux set-status idle "☕ rest" --color "#6b7280"
    ;;
  prompt) cmux clear-status idle ;;
esac

exit 0
