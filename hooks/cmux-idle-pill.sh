#!/bin/bash
# cmux idle + loop pills — owns two related cmux pills for the same workspace:
#
#   idle=        — agent parked at the prompt (Stop with no live loop).
#                  Value is intentionally empty: cmux already renders its own
#                  `Idle` workspace badge, so the pill is a marker only — read
#                  by `nudge_if_idle` to decide whether the workspace is safe
#                  to ping with an actionable PR signal.
#   loop=🔄      — agent is mid-/loop (dynamic ScheduleWakeup or cron). Visual
#                  only; suppresses idle gating so broadcasters skip the
#                  workspace while a wakeup is queued.
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
# turn; if it called ScheduleWakeup or CronCreate, we leave `idle=` cleared and
# set `loop=` on. Otherwise we clear `loop=` (the loop terminated — the model
# stopped arming wakeups) and set `idle=`. This gives accurate "currently
# looping" state for dynamic /loop, which a pure PreToolUse-only hook cannot
# (it has no event for "model decided not to schedule another wakeup").
#
# Cron-mode /loop arms a cron once at setup and fires on a fixed schedule —
# the Stop-time transcript scan would not see ScheduleWakeup on every iteration
# for that mode, so it relies on the PreToolUse(CronCreate|CronDelete) wiring
# to drive the `loop=` pill explicitly.
#
# Hook wiring (Claude Code event → arg):
#   Stop                                                       → stop
#   UserPromptSubmit                                           → prompt
#   PreToolUse(ScheduleWakeup|CronCreate|CronUpdate)           → loop-set
#   PreToolUse(CronDelete)                                     → loop-clear
#   SessionEnd                                                 → loop-clear

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
      # /loop iteration just scheduled another wakeup — keep `idle=` cleared
      # (we are *not* at rest) and reflect the live loop in `loop=`.
      cmux clear-status idle
      cmux set-status loop "🔄" --color "#a78bfa"
      exit 0
    fi
    # No wakeup armed by the last turn — any prior dynamic /loop has ended.
    # Clear `loop=` so the visual matches reality, then mark idle.
    cmux clear-status loop
    cmux set-status idle "" --color "#6b7280"
    ;;
  prompt) cmux clear-status idle ;;
  loop-set) cmux set-status loop "🔄" --color "#a78bfa" ;;
  loop-clear) cmux clear-status loop ;;
esac

exit 0
