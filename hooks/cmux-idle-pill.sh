#!/bin/bash
# cmux idle + loop pills — owns two related cmux pills for the same workspace:
#
#   idle=idle    — agent parked at the prompt (Stop with no live loop).
#                  Value is the literal string `idle`: cmux requires a non-empty
#                  `<value>` argument, but cmux already renders its own `Idle`
#                  workspace badge so the pill is a key-presence marker only.
#                  `nudge_if_idle` reads it to decide whether the workspace is
#                  safe to ping with an actionable PR signal — the value is
#                  ignored, only the `idle=` key prefix matters.
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

# Skip silently if the workspace was closed/recreated. Without this, every
# Stop/UserPromptSubmit writes to a dead socket and the err log fills with
# Broken Pipe forever. If `list-workspaces` itself fails we fall through
# (fail-open: same as pre-fix behavior, no regression).
if live=$(command cmux list-workspaces 2>/dev/null | tr '\n' ' '); then
  case " $live " in
    *" $CMUX_WORKSPACE_ID "*) ;;
    *) exit 0 ;;
  esac
fi

# Operator-debug log for cmux stderr. Silent failure of `cmux set-status`
# (e.g. the empty-value rejection that masked this pill being broken for weeks)
# lands here, prefixed per-line with ISO timestamp + workspace id so multi-
# session output is attributable. Path mirrors cockpit/lib/config.py's
# COCKPIT_HOME default; respects env override for tests.
LOG="${COCKPIT_HOME:-$HOME/.config/cockpit}/cmux-idle-pill.err"
LOCKDIR="$LOG.lock.d"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || exit 0

# Bounded-size rotate. Hook may fire from multiple concurrent Claude sessions
# in the same worktree; mkdir(2) is a POSIX-atomic CAS lock that serializes the
# rotate without needing flock (non-portable on macOS). Stale-lock reclaim
# handles a sibling that crashed mid-rotate.
if [ -d "$LOCKDIR" ] && [ -n "$(find "$LOCKDIR" -maxdepth 0 -mmin +5 2>/dev/null)" ]; then
  rmdir "$LOCKDIR" 2>/dev/null
fi
if mkdir "$LOCKDIR" 2>/dev/null; then
  if [ -f "$LOG" ] && [ "$(wc -c <"$LOG" 2>/dev/null || echo 0)" -gt 65536 ]; then
    tmp="$LOG.tmp.$$"
    if tail -c 16384 "$LOG" >"$tmp" 2>/dev/null; then
      mv "$tmp" "$LOG"
    else
      rm -f "$tmp"
    fi
  fi
  rmdir "$LOCKDIR" 2>/dev/null
fi

cmux() {
  # Fire-and-forget: the cmux daemon occasionally stalls under contention
  # (cockpit watcher + every claude session's hook all hitting the socket).
  # Claude Code's hook timeout then kills the script and surfaces a
  # "non-blocking status code" error on every prompt. Detach via subshell +
  # background + stdio redirection so the hook returns in <1ms regardless of
  # daemon health. Pill update is best-effort by design — stderr is captured
  # into $LOG (prefixed with timestamp + workspace id) so silent CLI changes
  # don't go unnoticed for weeks; stdout is discarded.
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  ( { command cmux "$@" --workspace "$CMUX_WORKSPACE_ID" </dev/null >/dev/null; } 2>&1 \
    | sed "s|^|${ts} [${CMUX_WORKSPACE_ID}] |" >>"$LOG" & )
}

# How hard to retry the `idle=` write before giving up. Overridable so tests
# don't sleep. Five tries × 1s self-heals a transient daemon stall (the
# "Broken pipe" drops seen in $LOG) within a few seconds.
CMUX_VERIFY_TRIES="${CMUX_VERIFY_TRIES:-5}"
CMUX_VERIFY_SLEEP="${CMUX_VERIFY_SLEEP:-1}"

cmux_set_verify() {
  # Reliable, still-detached pill SET: set `<key>=<value>` then read it back via
  # list-status, retrying until present or tries exhausted. The plain fire-and-
  # forget cmux() silently dropped this write under daemon contention, leaving a
  # genuinely-parked workspace with no `idle=` pill — so nudge_if_idle could not
  # tell it was safe to ping and the actionable nudge never fired. The whole
  # loop runs in a backgrounded subshell, so the hook still returns in <1ms.
  key="$1"; value="$2"; color="$3"
  (
    i=0
    while [ "$i" -lt "$CMUX_VERIFY_TRIES" ]; do
      command cmux set-status "$key" "$value" --workspace "$CMUX_WORKSPACE_ID" \
        --color "$color" </dev/null >/dev/null 2>>"$LOG" || true
      if command cmux list-status --workspace "$CMUX_WORKSPACE_ID" 2>/dev/null \
           | grep -qE "^[[:space:]]*${key}="; then
        exit 0
      fi
      i=$((i + 1))
      [ "$i" -lt "$CMUX_VERIFY_TRIES" ] && sleep "$CMUX_VERIFY_SLEEP"
    done
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [${CMUX_WORKSPACE_ID}] WARN: ${key}= not confirmed after ${CMUX_VERIFY_TRIES} tries" >>"$LOG"
  ) &
}

cmux_clear_verify() {
  # Reliable, still-detached pill CLEAR: clear `<key>` then confirm it is absent,
  # retrying as above. Mirrors cmux_set_verify so a dropped UserPromptSubmit
  # clear can't leave a stale `idle=` on a now-running session (nudge_if_idle's
  # native-Running guard is the second line of defense for that case).
  key="$1"
  (
    i=0
    while [ "$i" -lt "$CMUX_VERIFY_TRIES" ]; do
      command cmux clear-status "$key" --workspace "$CMUX_WORKSPACE_ID" \
        </dev/null >/dev/null 2>>"$LOG" || true
      if ! command cmux list-status --workspace "$CMUX_WORKSPACE_ID" 2>/dev/null \
             | grep -qE "^[[:space:]]*${key}="; then
        exit 0
      fi
      i=$((i + 1))
      [ "$i" -lt "$CMUX_VERIFY_TRIES" ] && sleep "$CMUX_VERIFY_SLEEP"
    done
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [${CMUX_WORKSPACE_ID}] WARN: ${key}= still present after ${CMUX_VERIFY_TRIES} tries" >>"$LOG"
  ) &
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
      cmux_clear_verify idle
      cmux set-status loop "🔄" --color "#a78bfa"
      exit 0
    fi
    # No wakeup armed by the last turn — any prior dynamic /loop has ended.
    # Clear `loop=` so the visual matches reality, then mark idle. The idle
    # write is verified+retried because its silent loss is the bug this hook
    # exists to prevent (a parked workspace that never gets nudged).
    cmux clear-status loop
    cmux_set_verify idle idle "#6b7280"
    ;;
  prompt) cmux_clear_verify idle ;;
  loop-set) cmux set-status loop "🔄" --color "#a78bfa" ;;
  loop-clear) cmux clear-status loop ;;
esac

exit 0
