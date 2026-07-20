# FUTURE — cockpit UI on Tauri/Electron (parking notes)

Scratch pad for a future direction: replacing cmux + cockpit's Textual TUI with
a single desktop app, keeping cockpit's reconcile engine as a headless sidecar.
Nothing here is decided. "We'll think later."

## The decision so far

- **Tauri (or Electron) replaces cmux too** — one unified desktop app is BOTH the
  terminal host (panes/splits/sessions, replacing cmux) AND the worktree/PR
  dashboard (replacing cockpit's Textual TUI).
- **cockpit daemon runs as a sidecar** — the reconcile brain stays, goes headless,
  and the desktop app supervises it as a sidecar process.
- Terminal-fidelity is the whole point (Claude Code's TUI must render perfectly),
  so the rendering layer is the main risk to de-risk.

## Layering (the mental model)

cockpit today is two things fused in one process:

- **Brain** (~90% of code): reconcile cycle, `gh`/`git`/`cmux`/ticket-provider
  leaves, spawn/teardown orchestrators, cache writers, nudge logic, the
  `cockpit new/close/nudge` CLI subcommands. **Keep all of it.**
- **Face** (`cockpit/tui/`): Textual table, footer, modals, row-action keys.
  **Throw away**, replace with native desktop UI.

The face is already a thin renderer: the table is read-only, reads only
daemon-written flat cells, and every mutating key routes through a CLI
subcommand + daemon kick. So the swap is a UI replacement, not a rewrite.

```text
┌─ Desktop app (Tauri/Electron — replaces cmux) ─────────┐
│  sidebar   │  ┌─ pane ────────────────────────────┐    │
│  ● repo-a  │  │  xterm.js  →  Claude Code session  │    │
│  ● pr-224  │  └───────────────────────────────────┘    │
│  dashboard (native UI, replaces Textual TUI)            │
│    reads cockpit cache JSON + `git worktree list`       │
│    buttons shell out to `cockpit new/close/nudge`       │
└────────────────────────────────────────────────────────┘
        ▲ reads cells / drives host        │ spawns
        │                                   ▼
   cockpit daemon (Python, headless) — SIDECAR
   reconcile cycle, cache writers, spawn/teardown
```

## Why this is a UI swap, not a rewrite

- Daemon already writes everything to `~/.config/cockpit/cache/*.json` as the
  single source of truth; inventory is derived each cycle from `git worktree
  list` (never stored). The desktop app reads the **same** cells the Textual
  table reads today. → **IPC is nearly free. YAGNI on a socket/HTTP API.**
- One addition worth it: a `cockpit inventory --json` subcommand so the app gets
  the joined view (worktrees × cells) in one call instead of re-joining in JS/Rust.
- Every mutation is already a CLI subcommand (`cockpit close` enqueues a
  `TeardownRequest`; `n`→`cockpit new`; `N`→nudge). The app just invokes them.

## The one hard part: un-fuse the daemon from the TUI

Reconcile **bodies** are already Textual-free and lock-free
(`cockpit.py::_once_with`, `cockpit.py::_fast_tick`, `cycle.py::cycle_all`).
The **scheduling** lives in the Textual `App` (`cockpit/tui/app.py`) and must move
to a headless runner:

- pidfile claim — `cockpit.py:199` (`daemon.claim_pidfile`)
- tick scheduling — the two `@work(thread=True)` workers `_run_slow`
  (`app.py:359`) / `_run_fast` (`app.py:398`) + `_tick_lock` (`app.py:199`) +
  `_start_fast` (`app.py:300`)
- signal handlers — `app.py:325-327` (`loop.add_signal_handler`; SIGUSR1 slow
  kick, SIGTERM/SIGHUP exit)

Invariant to deliberately **reverse**: "no headless mode — non-TTY `watch` exits
2" (`cockpit.py:188`). Extract a `cockpit daemon` headless runner (plain
asyncio/thread loop calling the same bodies); the desktop app spawns it as a
sidecar. Bodies don't move.

## cmux CLI contract the daemon depends on (handoff checklist)

The desktop app must honor this — it's cockpit's coupling to the host, above the
pixel layer. From `cockpit/lib/cmux.py`. `lib/tool.py` already abstracts the
backend (cmux/limux); add a third value. limux's branches are a working example
of "a second backend honoring the contract" — use as the compat spec.

### Tier 1 — subtle semantic contracts (break silently)

1. **`claude_code=` is a 3-state signal; `Needs input` is poison.** `list-status`
   emits `claude_code=Running|Idle|Needs input`. Nudge gate trusts `Running`
   (block) and `Idle` (safe) but MUST NOT trust `Needs input` (ambiguous:
   aged-idle AND pending y/n permission). Most fragile assumption.
2. **Status-line format `KEY=VALUE icon=… color=…`** — parsed by prefix, then
   split on the `icon=` and `color=` separators (each preceded by a space).
   Values may contain spaces.
3. **Persistent keyed pills with prepend ordering** — `set-status KEY VAL
   --color` / `clear-status KEY`, addressable by key, survive across the session.
4. **`idle=` pill writable by an external hook** — `cockpit/hooks/cmux-idle-pill.sh`
   sets it on Claude's Stop event. Third-party `set-status` must work from outside.

### Tier 2 — CLI verbs + flags (break loudly)

| Verb | Contract |
|---|---|
| `rpc workspace.list "{}"` | JSON `{workspaces:[{id, ref, current_directory}]}` |
| `list-workspaces` | text `[*] workspace:<ref>  <name>  [flags…]`; name may have spaces, trailing `[flags]` stripped |
| `list-status --workspace <ref>` | the `KEY=…` dump |
| `set-status KEY VAL --workspace <ref> --color <c>` / `clear-status KEY --workspace <ref>` | keyed pills |
| `new-workspace --name --cwd --command --focus false` | does NOT echo the new ref — cockpit polls `list-workspaces` for the diff |
| `select-workspace --workspace <ref>` | focus (NOT `focus` — that verb exits nonzero) |
| `close-workspace --workspace <ref>` | teardown |
| `rename-workspace --workspace <ref> <name>` | name reconcile |
| `workspace-action --action set-color --color <name> --workspace <ref>` | sidebar tint; **named** color, not hex |
| `send --workspace <ref> <text>` + `send-key --workspace <ref> enter` | two-step submission (type, then Enter) |

### Tier 3 — env

- **`CMUX_WORKSPACE_ID`** = caller's own workspace `id` (the JSON `id`, not
  `ref`). Used to exclude self from focus/match targets. App must set it per pane.

### Simplification this direction unlocks

Once the app owns BOTH the sidebar and the dashboard and both read cockpit's
cache cells, the daemon's **decorative-pill push** dies — DELETE `apply_pills`,
`status_pills`, `_CMUX_RENDERERS`, `_clear_pr_pill_keys`, `apply_wip_pill`,
`apply_stale_pill`, `apply_devdone_pill`, `set_workspace_color`,
`_tint_repo_workspaces`, and the color/pill reconcile in the fast tick. The app
renders `ci`/`comments`/`wip`/`owner`/`devdone`/`muted` rows straight from the
JSON cells. Only the **functional** trio stays as host state the nudge gate
reads: `claude_code=`, `idle=`, `parked=`.

## Bucket framing (why the host swap doesn't cost cockpit anything)

Sort every cmux feature into three buckets:

1. **From tmux** — session persistence, splits, copy mode, scrollback,
   send-keys. Free on any path (Tauri/Electron/native).
2. **From xterm.js** — GPU/fast render, TUI fidelity, ligatures, true color,
   hyperlinks, search. Free on any web path.
3. **From cmux being native macOS + libghostty** — native chrome, notification
   center, libghostty glyph nuance, Kitty graphics. The only bucket that argues
   against a web path.

cockpit relies on **none** of bucket 3 — only on the cmux **CLI/workspace
contract** above (bucket "app-level," orthogonal to the rendering backend). So
swapping the host's rendering leaves cockpit untouched as long as the contract
holds.

## Build vs reuse — layer 1 (terminal host)

Do NOT write a VTE parser or pty layer. Reusable pieces:

- **maiTerm** (`Flexmark-Intl/maiterm`) — closest match: Tauri + Svelte 5 +
  xterm.js, workspace organization, split panes, editor tabs. Architecture to
  copy regardless: `alacritty_terminal` (Rust) does VTE parsing + scrollback +
  buffering; xterm.js is a thin DOM renderer painting only the visible screen.
  Candidate **fork base**.
- **marc2332/tauri-terminal** — minimal reference (xterm.js + `portable-pty`).
- **Terax** (`emee-dev/terax-ai-tauri-terminal`) — Tauri 2 + portable-pty + React
  19 + xterm.js, 7MB.
- Search surfaced (no repo name captured) an "xterm.js + Tauri macOS terminal
  multiplexer for switching between AI agent sessions like Claude Code and Codex
  in one window" — almost exactly the target. **TODO: find the repo.**

Reusable blocks ranked: xterm.js (renderer) → `alacritty_terminal` OR
`portable-pty` (Rust VTE/pty) → maiTerm as fork base. tmux stays optional as the
session-persistence backend.

**Layer 2 (dashboard + orchestration) has nothing off-the-shelf** — adjacent AI
tools (Parallel Code, RunPane, Crystal, Conductor) are session launchers, not
PR-state trackers with cockpit's ticket-provider/dev-done/autoclose logic. That
IS cockpit. Keep it.

## Tauri vs Electron

Axes that actually differ for a terminal app:

- **Rendering fidelity (biggest):** Electron bundles Chromium; Tauri uses OS
  webview (WKWebView/Safari on macOS). xterm.js is Chromium-tuned; VS Code /
  Hyper / Tabby are Electron for this reason. WKWebView is where xterm.js
  perf/correctness bugs live. Strongest arg for Electron — terminal-specific.
- **BUT the maiTerm architecture blunts it:** VTE/scrollback in Rust, xterm.js as
  thin renderer → webview does less, engine gap shrinks. Tauri risk is real only
  if xterm.js does the full terminal.
- **PTY maturity:** edge Electron (`node-pty`, VS Code's) vs Tauri
  `portable-pty` (solid, less proven at scale).
- **Ecosystem prior art:** edge Electron (VS Code stack to borrow; existing
  AI-agent-worktree tools are Electron).
- **Footprint/memory:** Tauri decisively (~5–10MB vs ~150MB).
- **Language:** Tauri core Rust (forking maiTerm = Rust work); Electron Node/JS.
  cockpit brain is Python sidecar either way, unaffected.
- Everything in buckets 1 & 2 is identical on both — Electron is also the "web
  path," just heavier and more proven.

**Deciding question:** trust WKWebView + newer Rust terminal crates vs value
footprint. Risk-minimizing default for a terminal = **Electron** (Chromium +
node-pty is the proven stack), UNLESS committed to the Rust-backend/thin-renderer
architecture, where Tauri's footprint win returns and the rendering risk is
mostly designed away.

**Don't decide from reasoning — spike it:** put Claude Code's actual TUI in
xterm.js inside WKWebView (Tauri) for an hour, watch for repaint lag, cursor
artifacts, resize glitches. Clean → Tauri. Stutters → Electron. Settles it faster
than more analysis.

## Open decisions

- [ ] Tauri vs Electron — run the WKWebView + Claude Code TUI spike first.
- [ ] Fork maiTerm vs greenfield layer 1 — eval maiTerm's pane/workspace model
      for forkability.
- [ ] Find the unnamed macOS AI-agent Tauri multiplexer from search.
- [ ] Session backend: tmux vs `alacritty_terminal`'s own scrollback/persistence.
- [ ] Sidecar lifecycle confirmed (vs standalone service) — sidecar chosen.

## Sources

- [maiTerm](https://github.com/Flexmark-Intl/maiterm)
- [marc2332/tauri-terminal](https://github.com/marc2332/tauri-terminal)
- [Terax](https://github.com/emee-dev/terax-ai-tauri-terminal)
- [xterm.js](https://github.com/xtermjs/xterm.js/)
- [Claude Code native worktrees](https://code.claude.com/docs/en/worktrees)
- [johannesjo/parallel-code](https://github.com/johannesjo/parallel-code)
