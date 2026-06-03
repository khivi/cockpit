# Cockpit state machine

Cockpit combines **three independent state vocabularies** into per-workspace
decisions. No single source file shows the combination layer — this document
does. Diagrams are [Mermaid](https://mermaid.js.org/) and render on GitHub.

## The three state sources

| Source | Lives in | Values |
|---|---|---|
| **GitHub PR** | `gh` API → PR cache JSON (`cache.py`) | `state` ∈ {`OPEN`,`MERGED`,`CLOSED`} × `ci` × `unaddressed` × `review_decision` × `isDraft` × `mergeable` |
| **Claude session** | cmux native `claude_code=` + statusline stdin (`claude.py`) | `Running` / `Idle` / `Needs input`; context %, rate-limit, model, cost |
| **cmux workspace** | cmux pills + in-memory `pill_state` dict | `idle=` `stuck=` `parked=` `ci=` `comments=` `merge=` `wip=` `draft=` `approved=` `keep=` `stale=` `loop=` + *does a worktree exist?* |

Five decision functions consume these and emit actions. Everything below is a
drill-down of one node in the orientation map.

---

## 1. Orientation map (L0)

How the three state sources feed the five decision functions, and what each emits.

```mermaid
flowchart LR
  subgraph SRC["State sources"]
    GH["GitHub PR state<br/>gh API → PR cache JSON"]
    CL["Claude session<br/>cmux native + statusline"]
    CM["cmux workspace<br/>pills + worktree-exists?"]
  end

  subgraph DEC["Decision functions"]
    MW["match_worktrees<br/>cycle.py:208"]
    SM["_spawn_missing_workspaces<br/>cycle.py:959"]
    NI["nudge_if_idle<br/>cmux.py:314"]
    TS["_track_stale_issue<br/>cycle.py:157"]
    AC["_maybe_autoclose<br/>cycle.py:333"]
  end

  subgraph ACT["Actions"]
    A1["bg spawn (plan-only / review)"]
    A2["nudge (send + enter)"]
    A3["stuck= pill"]
    A4["teardown (worktree+workspace+branch)"]
    A5["refresh pills + colors"]
  end

  GH --> MW & SM & TS & AC
  CM --> MW & NI & TS
  CL --> NI

  MW --> SM
  SM --> A1
  NI --> A2
  TS --> A3
  AC --> A4
  MW --> A5
```

The renderer (`starship.py`) is **not** in this picture by design: it only reads
cache cells and never consults source state. See diagram 5.

---

## 2. Reconcile decision tree (slow tick)

Runs every `slow_poll_interval_seconds` (default 300s) in
`cycle.py::cycle_all`. For each PR crossed with "does a worktree exist?", the
daemon picks exactly one path. Split into two flows: **live PRs** (open work, may
spawn) and **cleanup** (merged/closed/orphaned). `self_user` is the configured
GitHub handle.

### 2a. Live PRs — track & spawn

Leads on "does a worktree exist?" so the two PR×author dimensions don't fan out.

```mermaid
flowchart TD
  P["Open PR"] --> WT{"worktree<br/>exists?"}

  WT -->|yes| TRACK["Track: refresh pills + caches"]
  TRACK --> ACT{"actionable issue?<br/>ci / comments / conflicts"}
  ACT -->|yes| NUDGE["nudge_if_idle → diagram 3"]
  ACT -->|no| CLR["clear stuck timer → diagram 4"]
  NUDGE --> ST["_track_stale_issue → diagram 4"]

  WT -->|no| WHO{"author?"}
  WHO -->|mine| SP["bg spawn --pr N<br/>(plan-only first turn)"]
  WHO -->|coworker| RV{"review_prs<br/>set?"}
  RV -->|yes| SPR["bg spawn --pr N --review<br/>(/review, uncapped)"]
  RV -->|no| IG["ignore (PR invisible)"]
```

### 2b. Cleanup — teardown, orphan, reap

```mermaid
flowchart TD
  C["Worktree / workspace cleanup"] --> K{"state?"}

  K -->|"MERGED / branch gone"| AC{"autoclose<br/>blockers?"}
  AC -->|"dirty · unpushed · open ·<br/>draft · ci≠green · unaddressed · keep"| SK["skip (log reason),<br/>keep worktree"]
  AC -->|"clean & merged"| TD["teardown: workspace →<br/>worktree → branch → PR cache"]

  K -->|"CLOSED · mine"| OR["orphan: pills + nudge<br/>to push or close"]

  K -->|"CLOSED · coworker"| KS{"--keep-stale?"}
  KS -->|yes| KP["keep (log stale)"]
  KS -->|no| CW["close workspace"]

  K -->|"workspace, no worktree"| RP{"idle?"}
  RP -->|"yes & mine-prefix"| EN["enqueue forced teardown"]
  RP -->|"no (mid-turn)"| DF["defer to next cycle"]
```

Key gates (all from `cycle.py`):

- **Autoclose hard blocker** (never overridden): uncommitted files.
- **Autoclose soft blockers** (`forced=True` overrides): unpushed commits, open PR.
- **Autoclose smart-skip**: even when merged & clean, skip if draft, CI not green,
  or unaddressed review threads remain.
- **In-flight spawn guard**: `_bg_spawn_pr` keys `spawn:<owner>/<name>:<branch>`
  in `pill_state` with a `time.monotonic()` stamp; a second spawn within
  `_SPAWN_INFLIGHT_TTL_SECONDS` (600s) is skipped, so a `/cockpit:sync` kick
  can't double-launch mid-creation.
- **Orphan auto-spawn is `<self_user>/`-prefix gated**: review worktrees are
  never orphan-spawned.

---

## 3. Nudge idle-gate (`nudge_if_idle`, `cmux.py:314`)

Five sequential guards decide whether it is safe to `send` a nudge. The subtle
rule: cmux native `Needs input` is **deliberately untrusted** — it is the same
value cmux shows for a pending y/n permission prompt, and nudging there would
type into the confirmation. Do not "simplify" the gate to trust it.

```mermaid
flowchart TD
  IN["nudge_if_idle(ref, msg,<br/>pr_number, category)"] --> G1{"PR-attached &<br/>muted for category?"}
  G1 -->|yes| F1["return False<br/>(user mute, survives restart)"]
  G1 -->|"no / orphan nudge"| G2{"native ==<br/>Running?"}

  G2 -->|yes| F2["return False<br/>(mid-turn; also catches a<br/>stale idle= on a live session)"]
  G2 -->|no| G3{"idle= pill present<br/>OR native == Idle?"}

  G3 -->|no| F3["return False<br/>(Needs input / None = not at rest)"]
  G3 -->|yes| G4{"parked= pill<br/>present?"}

  G4 -->|yes| F4["return False<br/>(user's done-waiting marker)"]
  G4 -->|no| HEAL{"native == Idle<br/>& no idle= pill?"}

  HEAL -->|yes| SELFHEAL["re-assert idle= pill<br/>(self-heal dropped Stop-hook write)"]
  HEAL -->|no| FIRE
  SELFHEAL --> FIRE["send msg + send-key enter<br/>→ record_nudge(pr, category)<br/>→ return True"]
```

There is **no time-based throttle**; the slow-tick cadence is the implicit rate
limit. Each tick re-evaluates and re-fires if the underlying issue persists.

Truth table (native × `idle=` × `parked=` × muted → result):

| native | `idle=` | `parked=` | muted | result |
|---|---|---|---|---|
| `Running` | any | any | any | **no** (guard 2) |
| `Idle` | T | F | F | **NUDGE** |
| `Idle` | F | F | F | **NUDGE** (+ self-heal `idle=`) |
| `Idle` | any | T | — | **no** (guard 4) |
| `Idle`/`None` | any | any | T | **no** (guard 1) |
| `Needs input` | any | any | any | **no** (guard 3, ambiguous) |
| `None` | T | F | F | **NUDGE** |
| `None` | F | any | any | **no** (guard 3) |

---

## 4. Stuck-pill timer (`_track_stale_issue`, `cycle.py:157`)

The `stuck=` pill is the **stale-running escape hatch**: a passive sidebar
visual (never a `send`) for when an actionable issue persists but the workspace
never becomes nudgeable — agent wedged mid-turn, or every `idle=` self-heal
failed. Per-category timing lives in `NudgePref.first_seen_at` (one JSON file
per PR). Threshold = `nudge_stale_seconds`, default `3 × slow_poll_interval`
(900s).

```mermaid
stateDiagram-v2
  [*] --> NoTimer

  NoTimer --> Timing: issue seen, not nudged, not muted (first_seen_at = now)
  Timing --> Timing: still un-nudged, elapsed < threshold
  Timing --> Stuck: elapsed >= threshold (apply stuck=cat Xm, RED)
  Stuck --> Stuck: issue persists, still un-nudged

  Timing --> NoTimer: nudged / resolved / muted / category switched
  Stuck --> NoTimer: nudged / resolved / muted / category switched

  NoTimer --> [*]
```

Reset paths (any of these clears the timer and pill):

- A successful nudge that cycle (`nudged=True`).
- The actionable issue resolves (`category=None`).
- User mutes the category (`should_nudge` returns False).
- The issue switches category (e.g. `ci` → `comments`); the old category's
  timer is dropped so it can't false-escalate.

The pill is managed **directly in the slow tick**, not via `apply_pills`, so it
is intentionally absent from `cmux.ACTIONABLE_KEYS`. No-op in dry runs.

---

## 5. Cell data-flow & ownership

**Only the daemon writes cells; renderers only read.** Field printers in
`starship.py` are strictly read-only — no `gh`, no `git`, no subprocess forks.
The lone exception is **session-scoped cells**, which Claude Code's statusLine
writes directly because the data exists only in the real-time stdin stream.

Read it left-to-right as a pipeline: **sources → ticks → cells → renderer**. The
daemon owns the bottom track; the statusLine is the side-channel that writes
session cells directly. The only feedback edge is the fast tick's republish loop
(it reads the persistent PR JSON and re-derives the ephemeral cells). `cmux`
pills are a separate daemon→cmux output (see diagram 1), not a render cell.

```mermaid
flowchart LR
  GH["gh API"] --> SLOW["Slow tick · 300s"]
  GIT["git worktrees"] --> SLOW
  GIT --> FAST["Fast tick · 30s"]

  SLOW --> DISK[("PR JSON<br/>on disk")]
  DISK -.republish.-> FAST

  SLOW --> CELLS["daemon cells<br/>pr-state · git-state · base-dist"]
  FAST --> CELLS

  STDIN["Claude statusLine"] --> SESS["session cells<br/>context · model · cost · rate-limit"]

  CELLS --> RENDER["starship printers<br/>READ-ONLY"]
  SESS --> RENDER
```

The cell-key detail (per-branch / per-cwd / per-sid suffixes) lives in the
source; this view shows ownership. Everything the renderer reads passes through
a cell — it never touches a source directly.

Why two ticks:

- **Slow tick** owns every decision (spawn, nudge, stuck, teardown, colors) and
  the expensive `gh` fetch + per-PR JSON snapshot.
- **Fast tick** is network-free: it re-derives git-state cells for every
  worktree and republishes PR flat cells from the persistent JSON, so a
  `git checkout` or an OS tmpdir wipe recovers within ~30s instead of ~300s.

Both hold `_tick_lock` (`daemon.py`) so they never collide on the same cells.

**Invariant**: a new cell's writer goes in `cache.py`; the call site goes in the
slow tick (decision + snapshot) and/or the fast tick (republish). Never let a
renderer path consult source state directly — that produces same-render
disagreement between fields, the bug class this design eliminates. Do not extend
the session-scoped exception to any new cell.
