# cockpit — build plan (Tauri rewrite)

Handoff spec for a Claude Code session. Everything here was decided; don't relitigate the rejected options.

## Thesis

A single self-contained **Tauri v2** desktop app that spawns Claude Code agents on ptys, renders live ones in tiled xterm panes and all of them as state in an instrument-panel sidebar. **Everything ephemeral:** quit the app → all agent ptys die → clean slate next launch. No tmux, no separate daemon, no launchd.

Differentiators (the reason this exists, all shell-agnostic): **hook-driven status** (not pane-scraping), **PR as a first-class object** (1:1:1 = one worktree : one PR : one agent), and **intent→keystroke indirection** (surfaces send intent, backend owns the keys).

## Architecture

```text
Tauri app (single process — owns everything)
  Rust backend
    ├── portable-pty → spawns `claude` per agent (CommandBuilder, cwd = worktree)
    ├── in-memory session registry
    ├── per-agent output ring buffer (VecDeque<u8>, ~10k lines) — always draining
    ├── git worktree + gh PR calls
    ├── localhost hook receiver (Stop / UserPromptSubmit / Notification) → status
    └── KEYMAP: intent → bytes → pty master.write()
  React frontend
    ├── sidebar: session state, status lights, PR/CI badges, contextual actions
    └── allotment tiling → xterm.js (WebGL addon) tiles ← Channel<PtyEvent>

  quit app → every pty dies → clean slate
```

Lifecycle: **agents = app-lifetime.** Closing a *tile* hides it but keeps the pty + ring buffer alive (reopen replays from buffer). Closing the *app* kills everything. This is the explicit choice — no persistence across app restarts, no background work when closed.

## Key decisions (with the why, so future-me remembers)

- **Tauri, not Go+libghostty.** Multiple live interactive pty panes with splits = embedding a terminal emulator. Rent it (xterm.js) rather than write one. Go+libghostty would mean writing an emulator *and* a tiling WM; libghostty-vt is parser-only and the full render surface isn't shipped cross-platform.
- **No tmux.** tmux's only irreplaceable property is *persistence across app restarts* (agent keeps running while app is closed). Decided that's not wanted → tmux drops out entirely. Bonus: portable-pty is cross-platform, so **Windows is back on the table** (tmux was the only thing forcing mac+Linux).
- **No separate daemon / no launchd.** Daemon was only needed to outlive the app. Decided it shouldn't. So its logic (registry, worktree, PR, hooks) folds into the Rust backend. Removes: socket protocol, launchd/systemd, fork-as-child, single-instance guard, reconciliation-on-boot.
- **`Channel<PtyEvent>` for pty output, never `emit`.** Events serialize through the global bus and drop under high throughput.
- **Ring buffer is the one thing tmux gave for free that must be rebuilt.** ~30 lines. Bounded `VecDeque<u8>` per session, drains even with no tile open. Powers sidebar previews + tile-reopen replay.
- **KEYMAP indirection.** Sidebar sends semantic intent (`approve`); backend resolves to bytes (`"1\r"`) and writes to the pty master directly — clears blocked *background* agents without attaching. When Claude Code reorders its permission menu, fix one KEYMAP entry, not every surface. Same discipline as hook-driven status, applied to input.
- **Native app install, no brew.** `cargo tauri build` → `.app`, drag to Applications. Add Tauri auto-updater (GitHub Releases + minisign) so "just an app" stays current without a package manager. No signing/notarization needed at n=1 (Gatekeeper right-click-open once).

## Rejected (do not reconsider)

- **Go + libghostty** — writing a terminal emulator + tiling WM; libghostty-vt is parser-only.
- **Bubble Tea** — a Bubble Tea program owns the alt-screen; can't host interactive pty panes inside a panel.
- **Swift + GhosttyKit** — macOS-only = rebuilding cmux, the thing being replaced.
- **Extending Tabby / Hyper / Wave (Electron terminals)** — terminal-first architecture fights the sidebar-first inversion; Electron ~300MB idle RAM; Tabby is Angular (throws away the React/Tauri reuse) with a quiet plugin ecosystem; Hyper is stalled.

## Build order (test-first — each step self-verifying)

**Step 0 — FakeAgent + test scaffolding.** A scripted stand-in for `claude` (emits known bytes, reads stdin) so the pty path is deterministic. Build this first; it unblocks every later test. Canned scripts: emits-then-waits, emits-a-permission-prompt, exits-nonzero.

1. **Scaffold** — `create-tauri-app` (React/TS/Tailwind/shadcn — reuse launchd-ui muscle). Add `tauri-plugin-pty` or raw `portable-pty`. Read `crynta/terax-ai` TERAX.md first.
2. **Backend pty spawn** — spawn one `claude` on a pty, reader thread → `Channel<PtyEvent>`, keystrokes via `invoke` → `master.write()`. **Prove one live xterm tile end to end.** ← first real milestone.
3. **Ring buffer** — bounded `VecDeque<u8>` per session, drains with no tile open.
4. **Multi-session + allotment** — registry of N agents, allotment tiling, open/close a tile without killing the pty (hide + keep buffer; reopen replays).
5. **Sidebar state** — hook receiver updates status; sidebar renders lights/badges (see mockups).
6. **KEYMAP action layer** — Approve / Reject / Interrupt → intent → bytes to that agent's pty. Wire the v2 mockup's buttons to real writes.

## Tests (by layer, most coverage in Rust)

```rust
// Layer 1 — Rust unit (cargo test): most value here
keymap_resolves_intent_to_bytes()   // approve → b"1\r", interrupt → [0x03]
ring_buffer_bounds_and_replays()    // never unbounded; snapshot ends with most recent
hook_event_maps_to_status()         // Notification → NeedsInput, Stop → Idle

// Layer 2 — pty integration (tokio test) against FakeAgent, NOT real claude
pty_roundtrip_streams_and_writes()  // spawn → collect marker → write "1\r" → assert echo
```

```ts
// Layer 3 — frontend (Vitest + mockIPC): no Tauri binary
actionsFor("needs_input") == ["approve","reject","interrupt"]
actionsFor("idle") == []            // no dead buttons
```

- **Layer 4 — E2E (WDIO Tauri service):** keep thin, ~3 smoke tests. WebKitGTK (Playwright doesn't work); macOS needs the embedded-server route. Lowest ROI — don't build until app is stable.
- **Terminal correctness gap:** WebDriver can't see inside the xterm WebGL canvas. Assert on **bytes** (layer 2), not pixels. If you must assert screen *state*, run bytes through a headless VT parser (`vte` crate) and check the grid.

Test-to-step map: step 2 → layer-2 roundtrip; step 3 → ring buffer; step 5 → hook→status; step 6 → KEYMAP resolve + action-set.

## Watch items / gotchas

- Resize must round-trip: xterm `FitAddon.fit()` → `master.resize(rows, cols)`, or Claude's TUI renders at the wrong width.
- `@xterm/addon-webgl` for the agent panes (fast-scrolling output).
- Cost scales with *visible* panes, not total agents — attach/render on open, keep buffer draining in background.
- Ring buffer is the load-bearing new code; everything else tmux did was deleted, not replaced.

## Install / distribution

```bash
cargo tauri build   # → cockpit.app + .dmg
# drag .app to /Applications
```

- Auto-updater in `tauri.conf.json`: endpoint → GitHub Releases `latest.json`, minisign pubkey.
- `tauri-action` GitHub workflow on tag push → builds bundle + `latest.json` (mac/Linux/Windows in one job). Add from first commit even if unused.
- **Skip** (solo): brew cask, homebrew tap, Apple notarization, Windows/Linux matrix — add only if a second person wants it.

## Repo strategy (two phases — prototype first, migrate only if it proves out)

**Phase 1 — prototype in `cockpit-app`. Do NOT touch the old repo yet.**
The new repo is decided: **`cockpit-app`** (private). Start fresh there so the rewrite is de-risked before retiring anything; old `cockpit` stays live and untouched as a fallback/reference. This build plan (`FUTURE.md`) is the seed doc — carry it into `cockpit-app` as its handoff spec on the first commit.

```bash
# new repo; first commit = the Tauri scaffold (step 1), not an empty README
gh repo create khivi/cockpit-app --private --source . --remote origin
```

- `.gitignore`: `target/`, `src-tauri/target/`, `node_modules/`, `dist/`. Private to start.

**Phase 2 — only after the prototype works: adopt the `cockpit` name + retire old.**
Deferred on purpose — don't pay the migration cost until the architecture is proven. Order matters (archive locks writes):

```bash
# 1. tombstone commit on old repo (before archiving)
git commit -am "Retired: superseded by the Tauri rewrite (see cockpit-app)" && git push
# 2. rename old → frees the name
gh repo rename cockpit-legacy --repo khivi/cockpit
# 3. archive old (read-only; keeps history/issues/stars)
gh api -X PATCH repos/khivi/cockpit-legacy -f archived=true
# 4. rename the proven prototype to take the name
gh repo rename cockpit --repo khivi/cockpit-app
```

- Do NOT delete the old repo (destructive, buys nothing).
- Carry the name **cockpit** eventually — the idea (1:1:1, instrument-panel sidebar) continues; the code restarts.

## References

- **crynta/terax-ai** (Terax) — Tauri2 + portable-pty + xterm.js WebGL, `Channel<PtyEvent>`, PtyState `RwLock<HashMap>`, ~7–10MB. Closest reference impl — read TERAX.md first. **Best reference for the terminal mechanics specifically.**
- **Jan** — local-AI desktop app (runs OSS models locally / connects to OpenAI/Anthropic/Google) on Tauri. Best reference for streaming + Rust-backend patterns. Terax + Jan are the two best code references overall.
- **marc2332/tauri-terminal** — minimal xterm ↔ portable-pty pattern.
- **Tnze/tauri-plugin-pty** + `tauri-pty` (frontend) — drop-in if not hand-rolling the Rust side.
- Also on Tauri v2 in-domain (context, not code refs): Spacedrive (file manager), Nous Research desktop agent (agent orchestration + sandboxing), Yaak (API client), GeoLibre.
- Mockups (visual/interaction target): `cockpit-tauri-mockup.html` (shell layout), `cockpit-tauri-mockup-v2.html` (interactive sidebar actions + KEYMAP toast).

**Maturity note:** Tauri v2 is production-proven (stable since late 2024, 2.9.x line, real apps at hundreds-of-thousands of installs). The only real knock is a shorter track record than Electron — irrelevant at this scale. Multi-webview sidebars/split-views are an officially supported pattern; an r/tauri multi-window IDE shipped at ~5MB in a week, which is directly this scope.

## Reuse & licensing

- **Tauri framework** — dual MIT / Apache-2.0. Building on it (crates, plugins) has no obligations beyond standard permissive terms.
- **Terax (crynta/terax-ai) — REFERENCE ONLY, do not copy its code.** Decision: read TERAX.md and its pty module to learn the portable-pty ↔ `Channel<PtyEvent>` ↔ xterm wiring, then write cockpit's pty layer fresh from the portable-pty / `tauri-plugin-pty` docs. Reading open code to learn a pattern is not a license event → zero attribution burden, repo stays clean, no derivative worry (cockpit diverges 90% anyway — Terax is an IDE, cockpit isn't). Writing the glue fresh costs ~a day over copying; worth it for the clean repo.
- *(For the record: Terax is Apache-2.0, NOT copyleft, so copying WOULD be legally fine with a THIRD_PARTY attribution line. Choosing not to, for a clean repo. Unlike Claude Squad = AGPL, which was avoided outright.)*
- **What to write fresh (all of it):** pty spawn/stream layer (referencing Terax's approach), plus everything above it — sidebar, orchestration, PR/1:1:1 model, KEYMAP action layer.

## Timeline calibration (from Terax's git history)

- Terax's **first commit was "rust pty & xterm in react prototype"** — i.e. this plan's step 2. Prototype → public release (macOS+Linux) was **~11 days**, but at ~302 commits/month = near-full-time, AI-assisted pace.
- Terax's later months (~230 commits) went into an IDE surface cockpit is NOT building (editor, file explorer, git panel, AI chat, 75+ providers). Comparable slice = Terax v0.0.2 → v0.5.8 ≈ first ~150–200 commits.
- **Calibration:** the "one agent, one live tile" milestone is a day-one thing (same portable-pty + xterm + Channel pattern). A usable cockpit shell is a ~2-week-of-focused-work artifact, not multi-month — the scoped-out IDE features are what made Terax a 3-month project. Calendar time scales with available hours, not commit count.

## Open questions for the session

- Target Windows in v1, or mac+Linux first? (portable-pty makes Windows viable; decide before scaffolding CI.)
- Nudge input: inline text field on the row vs command-palette prompt.
- Background-agent Interrupt: confirm step, or fire blind?

## Roadmap (post-v1 — do NOT let this inflate v1 scope)

The v1 is the shell (pty + sidebar + PR core). These make cockpit more than "another Claude Squad" but only have somewhere to live once the shell exists. Fence them off until then.

The theme: cockpit's model grows from *PR-first* to the full **ticket → agent → hooks → PR → CI → merge** loop — i.e. more first-class objects in the daemon, same architecture. The 1:1:1 invariant extends toward ticket:worktree:agent:PR.

- **Native Linear (etc.) read — cockpit's job.** Daemon reads Linear via API to populate the Ticket column + spawn-an-agent-from-a-ticket, writes status back. Same shape as the existing `gh` PR calls. Belongs in the daemon/backend.
- **Pre-commit hooks as a status signal — cockpit's job.** Hook results (lint/test/format) become another per-agent gate surfaced in the sidebar before the PR forms. Extends the hook-driven-status model.
- **Browser-to-see-code — the AGENT's job, NOT cockpit's.** Claude Code can already drive a browser (Chrome MCP / Playwright) to view rendered output or a PR web UI. Do NOT build browser integration into cockpit — host agents that use it and track the *outcome*. Pulling an agent capability up into the orchestrator bloats cockpit and couples it to one agent's toolset.

Design rule this surfaces: **orchestration-layer concerns (ticket read, hook results, PR/CI state) go in the daemon; agent capabilities (browsing, editing, tool use) stay in the agent.** cockpit tracks outcomes, doesn't absorb tools.
