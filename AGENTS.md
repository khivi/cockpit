# Privacy & Internal References

This is a public repository. Never include in commits, PRs, code comments, or documentation:

- Internal ticket IDs (Linear `ENG-123`, Jira `PROJ-456`)
- Internal GitHub PR/issue URLs from private repos
- Real names of teammates (use roles: "the reviewer", "the on-call engineer")
- Internal Slack channels, wiki URLs, tool links, hostnames, service names, infra identifiers
- Customer names or company-specific identifiers

In commit messages and PR descriptions describe *what* changed and *why*, not which ticket tracks it; reference public GitHub issues only. If context needs an internal ticket, summarize the requirement instead of linking. Before committing, scan for what gitleaks can't catch: your team's ticket prefixes, and `@firstname` references that aren't GitHub handles.

## Worktree discipline

Always use a dedicated git worktree for any code change. Never commit directly to `main`/`master` in the primary checkout, and never edit in place on a feature branch without a dedicated sibling worktree — cockpit derives per-branch state from `git worktree list`, so an unisolated branch is misattributed or dropped.

Before any Edit or Write, run `git branch --show-current` and `git worktree list`. If HEAD is `main`/`master`, or the working tree is the primary checkout (first entry), spawn a worktree via `cockpit new` first. If HEAD is already a non-main branch in a sibling worktree, proceed — do **not** spawn another.

## Architecture notes

Each `###` is one invariant: the rule and its enforcing `file::symbol`. Obey the **Never** / **Do not** lines — they encode paid-for regressions. `docs/state-machine.md`'s four Mermaid diagrams are the control-flow half; keep both in sync.

### Keep `docs/state-machine.md` in sync — a stale diagram is worse than none

Any change to `match_worktrees`, `_spawn_missing_workspaces`, `nudge_if_idle`, `_track_dev_done`, `_maybe_autoclose`, the `cache.py` cell writers, tick cadence, or the spawn/teardown/nudge/devdone/color rules MUST update the matching diagram in the same PR.

### Docs have four altitudes — put a fact at exactly one of them

`FEATURES.md` (user) · `README.md` (visitor) · `docs/config.md` (operator) · `AGENTS.md` + `docs/state-machine.md` (you). **A change to user-visible behaviour updates `FEATURES.md` in the same PR** — nothing fails when it's skipped.

- **Don't restate across altitudes** — duplicated prose drifts silently.
- **`FEATURE_GUIDE_URL` points at `main`, deliberately**: the version bump lands before `tag.yml` pushes the tag, so a pinned URL 404s for the whole release-PR window. **Do not** pin it to a tag.
- **A user-facing doc names capabilities, never `file::symbol`** — it must survive a refactor that renames every function it describes.

### Inventory is derived every cycle, never stored

Each cycle re-reads `git worktree list` and cmux's workspace list. Only PR payloads are cached (`~/.config/cockpit/cache/<repo>__pr-<N>.json`, a network round-trip). **Never** add a stored identity file.

### Packaged as the `cockpit` console script — invoke by subcommand, never by file path

- **Dispatch:** `cli.py` routes `watch / setup / teardown / statusline / starship / idle-pill / new / close / nudge` + `--version`. **Add entry points as `cockpit <sub>` in `cli.py`**, not file-path invocations.
- **`teardown`** (`config.teardown_claude_integration`) is the inverse of `setup`'s `~/.claude` writes. Run it *before* `brew uninstall`.
- **Distribution:** a brew formula whose single source of truth is the tap repo `khivi/homebrew-cockpit` — **not** vendored here. No plugin, marketplace, or self-update path.
- **Version:** static in `pyproject.toml`, read via `importlib.metadata`. `preflight._warn_cockpit_not_on_path` soft-warns, never hard-fails.
- **Claude footprint** — three idempotent writes by `cockpit setup`: the statusLine command + idle-pill hooks, and three files under `~/.claude/commands/`. **`_COCKPIT_HOOKS` is exactly two hooks** (`Stop` → `idle-pill stop`, `UserPromptSubmit` → `idle-pill prompt`), the only two with a reader. A `Stop` → `statusline` hook and three `loop=` hooks were removed and **must not return without a reader**. `install_claude_hooks`' drop pass sweeps **every event in the file**, since a retired hook lives under an event the template no longer names; an event left with no groups is deleted, not emptied. `_COCKPIT_HOOK_CMD_RE` keeps matching `statusline` to clean out older installs.
- **`{python}` pin:** starship configs use `{python} -m cockpit.cli <sub>`, pinned to `sys.executable` at setup time. **Never run setup from inside a worktree venv** — it bakes an ephemeral `.venv/bin/python` that dies on cleanup.
- **Pin self-heals on upgrade:** `cockpit watch` re-pins on startup via `config.repin_interpreter_if_stale`, rewriting only the interpreter prefix so user edits survive. Startup-only.
- **idle-pill hook:** `cockpit/hooks/cmux-idle-pill.sh`, inside the package so it ships in the wheel, `bash`-exec'd (no reliance on the wheel preserving the exec bit).

**Slash commands are user commands, not a plugin.** `cockpit/claude_commands/*.md` wrap `cockpit new/close/broadcast $ARGUMENTS`. **A command template documents the CLI, never reimplements it.** `parse_args` **errors** on a bare `--context` rather than defaulting to none — an unexpanded flag means the substitution didn't happen. **Do not** teach the CLI to synthesize its own context. **They install as flat, hyphenated files** (`cockpit-new.md` → `/cockpit-new`); colon-namespacing is plugin-only. hatchling ships only **VCS-tracked** files, so a new template must be `git add`ed.

### `cockpit watch` is a Textual TUI, and the TUI *is* the daemon (`cockpit/tui/`)

- **No headless mode:** non-TTY `watch` exits 2. The app owns the pidfile. **Pidfile self-heal:** the fast tick calls `daemon.reassert_pidfile` when it goes missing, or every `cockpit close`/spawn kick reports "no daemon" for the process's life. **The reclaim must re-create the state *directory*** (`daemon._reclaim`) — a wipe of `$COCKPIT_HOME` makes the recovery write raise uncaught, killing the fast tick instead of healing it. Deliberately not `ensure_state_dirs()`, which also seeds a `config.json`.
- **Ticks:** slow + fast run in `@work(thread=True)` workers; bodies are lock-free, serialized by `_tick_lock` acquired *inside* the worker. Startup is slow-first.
- **Per-repo table republish:** the slow tick's `on_repo_done` hook fires after each repo. **Never** let it write a cell — only the daemon writes.
- **Signals:** `loop.add_signal_handler` only. **Never** `signal.signal` — it raises off the main thread.
- **Table is read-only** (`worktree_table.py`, keyed by worktree path), grouped under per-repo header rows (`HEADER_KEY_PREFIX`, a NUL-led sentinel that can't collide with a path). **Hierarchy is carried entirely by rendering** — the header's dim `─` rule plus `ROW_INDENT` — since header and rows share the Workspace column. `_RULE_WIDTH` is sized to the *typical* widest row, not the worst case. The status glyph sits in a **fixed-width slot** (`_STATUS_SLOT`) a glyphless row pays in blanks, since 🔇/🔔 differ in ink width per font; **do not** drop that padding. Workspace and Ticket cells are ellipsized, and anything truncated **must** stay on the cell's hover tooltip (`row_tooltips`). `current_path()` returns None on a header and `current_capabilities()` returns `{HEADER_CAP}`, which hides row-targeted keys.
- **Cells the table reads** (`cache.py::_write_pr_flat_cells`): `pr-muted` → 🔇, `pr-nudge` → 🔔 (mute wins), `pr-snoozed` → fold membership and **no glyph**, `pr-author` → `@login`, `pr-comments` → 💬 count, `pr-base` → the stacked `└` indent. `pr-nudge` is `PR.nudge_issue`, so bell and nudge can't disagree.
- **Every cell naming something on the web is an OSC 8 terminal hyperlink** (`worktree_table.py::_cell_links` / `_apply_links`) — the terminal owns the gesture, cockpit only names the destination. The GitHub cluster (`PR`, `🔀`, `💬`, `Title`) points at the PR, `Author` at that login's profile, the ticket cluster (`Ticket`, `📍`) at the tracker; `Workspace` (already double-click → focus), `✎` and `$` name nothing remote and stay unlinked. `CI` is the one cell that does **not** point at the PR — it points at `<pr-url>/checks`, since a red ✗ is clicked *through* rather than at. Six rules:
  - **The ticket URL must be *cached*.** Three of the four providers can only read their URL out of the PR body's delivery footer (`tickets._footer_url` — the Linear workspace slug, the Jira site and the Trello card slug are none of them derivable from the id), i.e. a `gh pr body`, which a renderer may not make. The daemon resolves it every cycle into the `ticket` block's per-ticket `url` (`cycle._stamp_ticket_urls`, handed the `pr.body` it already holds) and the table reads a string. **Do not** answer a missing link by resolving one in `worktree_table.py`.
  - **`t` reads that same cached string first** (`app._open_ticket_url`), falling back to the live `provider.ticket_url` only for a block written before the field existed — the key and the click must not send you to two different places, and the fallback keeps the cache field non-load-bearing.
  - **The stamp runs on carried blocks too, and always writes**, including `None`: it is pure string work over a body the cycle already fetched, and since the ticket *id* decides carry-vs-rebuild, a footer re-pointed at a new link under an unchanged id is only ever caught by writing unconditionally.
  - **A blank cell is never linked** — a hyperlink over blank padding is a click target with nothing in it, and the columns most often empty (`Author`, `CI`, `💬`) sit beside ones that aren't.
  - **No underline, and no click handler.** The terminal draws its own affordance and picks its own modifier; a `DataTable` click handler would need a single-click rule (so selecting a row launches a browser, since `PR` sits beside `Workspace`) or a double-click one colliding with Focus. The accepted cost is Apple Terminal, which has no OSC 8 support; `p`/`t` remain the keyboard route.
  - **The hover tooltip names the destination** (`row_tooltips`) — an OSC 8 link is *invisible* until the pointer is on it with a modifier down, so the hover text is the only place a cell admits it goes somewhere. `test_links_survive_all_the_way_into_terminal_output` pins the one thing outside cockpit's control, that Textual still emits the escape.

**Row actions** (`f p t d a c C m z n`) live on the app and — except `n`/`f` and `m`/`z` — never touch cmux or the cache. Footer help is gated on three axes: the resolved **backend** (`a`/`d` cmux-only; `f` hides only on `none`), **workspace presence** (`a` needs one, except on a repo header; `f` does not, since it spawns first — the old `w` key is gone), and the **row's content** (`ACTION_REQUIRES` fed by `current_capabilities()`). `c`/`C` also hide on a workspace-only primary checkout with no workspace.

Row caps are `{pr, ticket, muted, snoozed, workspace, primary}`: the first four read the same cells the row renders from; `workspace` comes from a **single `workspace_cwds()` read per inventory refresh**; `primary` is `wt.is_primary and wt.branch in MAIN_BRANCHES` — a primary checkout on a **feature** branch does not get it, since its close tears the branch down. Caps `None` shows the full legend. Actions stay bound and self-guard.

- `f` **focus** → ensure the row has a workspace, spawning one when missing, then `cmux focus`. Slow-kicks after a spawn, not a pure focus. **`use_worktree: false` repos** host several sessions at one cwd, so `f` resolves by **repo name** (`_workspace_ref_by_name`), then cwd, then spawn.
- `p` opens the PR URL. `t` is **provider-neutral** via `TicketProvider.ticket_url`: GitHub builds it from the delivered ref + nwo with no network; Linear reads the exact footer link, since its URL can't be hand-constructed.
- `c` **close** → `probe_blockers` → enqueue `TeardownRequest` → slow kick. The commit guard is **ownership-split** (`worktree_state_blockers`): our own branch uses `git.count_unlanded` (a patch-id check against `origin/<default>` **and** reachability from no remote ref other than `origin/<branch>`, so a stacked-on branch's commits drop out); a **coworker's** uses `git.commits_only_local`, since their work is safe on `origin/<branch>`. Pushing does **not** clear it. **Do not** collapse the two, and **do not** re-baseline `count_unlanded` on `origin/<branch>`.
- `c` on a **primary checkout**: `teardown` **always** skips `git worktree remove` (git refuses it). On its default branch it is a workspace-only close and the commit guard relaxes; on a **feature** branch the branch is torn down (HEAD moves back first, since git refuses `branch -D` of the checked-out ref) and the guard is **not** relaxed. `default is None` off-GitHub keeps it workspace-only. The checkout+delete is soft-fail.
- `C` **force-close** overrides the *soft* open-PR block but still refuses the *hard* `worktree_state_blockers`, so force never discards local work. **Closing never runs teardown inline** — no daemon means the marker stays durably queued.
- `m` **mute** writes a `NudgePref`, repaints via `_repaint_pref`, slow-kicks.

**`m` and `z` repaint on the keypress — `app._repaint_pref` → `cache.restamp_pref`, the one row-action cache write**, because the keypress *is* the source rather than something derived. It writes **both** the flat cells and the snapshot's fields (cells alone are reverted ~30s later by `republish_pr_caches_from_disk`); it is a **no-op when the snapshot is missing**; and the kick is still sent. **Do not** extend this to a cell the daemon derives.

- `z` **snooze** → the event-expiring sibling of mute. Writes `NudgePref.snoozed` + two wake snapshots read off the **cached** payload, resolved via `_cache_repo_name` — keying by the config `name` matches no file and would wake the snooze it just set. Silences the nudge like a mute (`quiet = muted or snoozed`) and sinks the row into the trailing fold. The two stay **separate fields**, never one tri-state, or the CLI's mute would self-clear; but `z` **clears any mute** it lands on, since mute wins everywhere it's read. Auto-wake is `cycle.py::_resolve_prefs`. Two events wake it: **review activity** (`wake_on`, built from `total_from_others` not `unaddressed`, so my own replies can't wake my own snooze) and **new work** (`nudge_issue` differs from `wake_nudge` **and is non-empty** — so an issue the snooze was set on top of doesn't wake it, and one resolving doesn't either). **Do not** give the snooze a time-based `until`.
- `d` **diff** → `gh pr diff` (plain, no `--color`) piped to `cmux diff -`. cockpit **must not** grow a second in-overlay renderer or a `delta` dependency (both tried and removed). `--layout unified`, since split columns overprint beside the dashboard. **The split opens in the ROW's workspace (`--workspace`), never cockpit's own.** **The call must run from the row's worktree (`--cwd` *and* the subprocess `cwd`)**, since cmux keys its comment store by repo root derived from this call and which input it reads for a piped patch is undocumented. **The inherited `CMUX_SURFACE_ID` is stripped** — a stale value fails the whole call; only that variable, the rest of `CMUX_*` must pass through. **Do not** answer that by matching `not_found` at press time the way `browser_disabled` is matched. It **degrades rather than refuses**. Comments are **local to cmux and never reach GitHub**; `p` remains the route. **Availability is probed at startup** (`diff_viewer_available`): the verb AND a live browser, unanswerable from a capability id since cmux advertises `browser.stream.v1` while the browser is off. **Do not** gate this on a capability id.
- `a` **ask** → **the one manual send**: a one-line modal (`AskScreen`) routed through `cmux.nudge_if_idle` with no `pref_key`, so it overrides mute/snooze while honouring every idle guard and a mid-turn session refuses it instead of having the text typed into a y/n prompt. Reaches an *existing* workspace only. Writes no cell or pill.

  **There is deliberately no manual *nudge* key beside it.** `N` was removed rather than fixed: gated on `workspace` alone it fired author-mode prose into coworkers' review sessions, onto PR-less orphans, and onto healthy PRs. **Do not** re-add a manual nudge key with a canned message; derive one from `PR.nudge_issue` if ever wanted.

  **The modal is an `Input`, never a `TextArea`** — a multi-line box would submit several truncated prompts. **On a repo group header `a` addresses the whole repo**, but **only on the two `HEADER_CAP` rows that name a repo**. The fan-out matches workspaces **by cwd against the repo's own `worktrees()`**, never a path-prefix test, and excludes the daemon's own. **Delivery is partial by construction** and must be reported with the gate's own reasons; a partial send keeps the draft **and the refs that missed**, and the retry targets only those, since re-deriving would re-deliver to sessions that already accepted. **A refusal keeps the text** and **names its cause** from the `skips` dict, since only `not at rest (Needs input)` says the session cannot self-heal. The modal reports **three** outcomes: Enter-with-text sends, escape **stashes**, Enter on an emptied box **drops**. **With diff comments pending and no draft waiting, the box opens on a lead-in** (`COMMENTS_LEAD`), and escape does *not* stash an untouched one. An **advisory** state hint is filled asynchronously (`rest_skip_reason`); the modal is pushed **first**, and the hint **never blocks the submit** — `nudge_if_idle` re-checks at send time and remains the sole authority.

  **`a` is also what delivers `d`'s diff-viewer comments** (`lib/diff_comments.py`), because cmux folds them into the next message *its own composer* submits and a cockpit workspace is a terminal running Claude's TUI, which has no composer (measured). **Do not** re-document `d` as delivering them itself. Seven rules: it **rides a message, never sends alone**; the **typed line leads, anchors follow**; it is built from the store's **structured fields, never `submissionText`**, whose fences `cmux send` would turn into Enter presses; it is **read-only on cmux's store**, recording delivery in `$COCKPIT_RUNTIME_DIR` — **do not** move that ledger into `$COCKPIT_HOME/state`; it marks delivered **only on a send the gate accepted**; the **repo-header fan-out carries none**; and lookup matches each store file's own `repoRoot`, offering **both** the worktree and its main checkout. Everything fails **open**.
- `A` **ask-snoozed** → `a`'s fan-out aimed at one snoozed fold. A **separate key rather than a third meaning of `a`**, since on a repo header both readings are live. **Scoped to the cursor row's repo**. Advertised only on the two rows carrying `FOLD_CAP`, which is stamped only when the repo has a pile and is the one row key hidden under **unknown** caps too. Membership comes from `snoozed_paths`, **the render's own record**, never re-derived, since `_split_snoozed` partitions at *chain* granularity. **Snoozing silences the automatic nudge, never a line you typed** — a `pref_key` here would make the key refuse every row it exists to reach.
- `n` **new** → modal, then `cockpit new <source>` detached via module dispatch (`python -m cockpit.cli new`, **not** `spawn.py` by path — that breaks imports). `N` is off `n` so New gets the bare key.
- **Row-action kicks are repo-scoped — except `z`.** State-changing keys kick `_kick_slow(<row's repo>)`, skipping the repo-spanning sweeps; the close queue is still drained. `s`, SIGUSR1, the interval and startup stay full-cycle. An unknown `only_repo` reconciles nothing. **`z` kicks full-cycle** because it is the only row key that changes sidebar fold membership, and `cycle_all` builds `ReviewFolds` only when `only_repo is None`. **Do not** instead build `folds` under `only_repo` — a bucket holding no ref from the scoped repo is dissolved, taking every other org's fold with it. **Do not** move the pass to the fast tick, whose network-free inputs would read absent payloads as "no reviews left".

**Global keys:** `q` quit, `s` sync, plus the repo-scoped `h`. **Sync is a key; output is a palette entry.** `s` lives in `GLOBAL_ORDER`, not `ROW_ACTIONS`. **Do not** give `action_show_output` a key back, and **do not** list sync in `COMMANDS` as well as binding it. Updates are `brew upgrade`, not an in-TUI key.

**Docs discovery is the menu — deliberately not a key**, since the footer already is the key reference. `FEATURES.md` opens from the palette's "Feature guide" entry via `open_url(FEATURE_GUIDE_URL)`; **never** point this at a local path, as the wheel may not ship the file. Three rules:

- **`ConfigCommands` implements `discover` as well as `search`** — `discover()` fills the palette while the box is empty and the base implementation yields nothing, so a provider with only `search` is invisible exactly when the palette opens. Both walk the one `COMMANDS` tuple. **Do not** add a palette entry without a `discover` hit.
- **`HeaderBar` also names the cursor row's repo (`#header-repo`), because the group header scrolls off.** Fed from `app._refresh_footer_caps` — the hook that already runs on every cursor move — via `WorktreeTable.current_repo_name` / `current_repo_color`. Three rules: the colour comes from a **`_repo_color` map filled by `update_inventory`**, never a `load_config()` read, which would put a disk hit on every arrow key; it is **its own `Static`**, so an arrow key doesn't repaint the once-a-second tick countdowns beside it; and it repeats `worktree_table._header_cells`' tint **deliberately** — that one appends the `─` rule, and coupling two sibling widgets costs more than the three lines. **Do not** answer this with a Repo column (it duplicates the header row in a table that already ellipsizes) or `DataTable.fixed_rows` (which pins the *top* rows, not the header above the cursor).
- **The palette's one visible entry point is `HeaderBar`'s trailing `☰ Menu`, and it is unconditional** — `ctrl+p` is Textual's binding and can never come from `BINDINGS`, so without a painted affordance the palette is invisible. It lives in the header (`#header-menu`, `width: auto` against a `1fr` status half) and carries **no** gate. The label is **not** the key. **Do not** move it into `FooterBar`, and **do not** print the key beside it.
- **A footer key explains itself on hover — `TOOLTIPS`, matched off the segment's own `@click` meta**, since the hints are two `Static`s with nothing per-key to hang a tooltip on. The click link wraps the **whole** segment; `c` and `C` share one tooltip; a gap clears it. **Do not** rebuild the footer as a widget per key.

**`h` parks a repo — the one repo-scoped key, and the only user state that stops the daemon polling.**

- **One key, three meanings, read off the cursor row**: expand/collapse the hidden section, un-park a revealed repo, or park the cursor row's repo. **Do not** re-introduce a second key for reveal.
- **The hidden row is the one row a single click acts on**, and the one row where **Enter** opens the section. **`on_click` must resolve the clicked row from `event.style.meta`, never the cursor** — Textual dispatches `WorktreeTable.on_click` *before* `DataTable._on_click` moves the cursor, silently demoting the single click to a double click.
- **Keyed by resolved path, and it fails open** — `$COCKPIT_HOME/hidden-repos.json` (`lib/hidden.py`), never the mutable config `name`.
- **Parking is not unregistering** — the repo stays in `config.json`, deliberately *not* a config field, so the three-faces rule doesn't apply.
- **A parked repo goes dormant, not merely invisible**: `cycle_all` filters it out. **The filter is skipped when `only_repo` is set.** The fast tick is untouched.
- **It clears the cmux sidebar too, and it is workspace-only** (`_park_workspaces`). Matched **by cwd against the repo's own `worktrees()`**, never a path-prefix test. The daemon's own workspace and any that isn't `workspace_is_idle` are spared.
- **Un-parking closes nothing and respawns nothing.** `h` re-renders via `_prime_table()`; parking must never cost a fetch.
- **A spawn into a parked repo un-parks it — `spawn.py::_unhide_spawn_target`, one gate for every mode.** It sits **after** the spawn and keys off `main_worktree_path(wt)`, the same resolved path `hidden.py` stores. The `load_hidden()` test comes first so the ordinary run pays a JSON read, not a `git worktree list`. **Do not** move this into a per-mode branch, and **do not** key it on the worktree path.
- **`n`'s repo picker keeps parked repos, sunk and dimmed**, and selection is not blocked. `_spawn_new` un-parks before launching, deliberately **redundant** with the `spawn.py` gate so the row repaints on the same keypress. **Do not** make the picker filter them out, and **do not** delete either half as duplication.
- **The parked set collapses into one trailing disclosure row** (`HIDDEN_ROW_KEY`, nested under `HEADER_KEY_PREFIX`). Expansion is **session only**. The cursor-skip loop stops at `hidden_start`.
- **`h` lives in the footer's global group**, advertised **only on a row that reads as a repo** (`HEADER_CAP`), since on a worktree row "Hide" reads as *hide this row*. The **binding stays live everywhere**.

- **Updates are brew's job — no in-process self-update.** No version check on the tick, no `u` key, no re-exec, no `cockpit update`.
- **stdout:** all tick prints go through one process-wide `_QueueWriter` — **never** per-tick `redirect_stdout` (the threads race).

### Only the daemon writes the cache; renderers read

`lib/starship.py` field printers are strictly read-only (no gh/git/subprocess/`atomic_write`).

- **Slow tick** (300s) — `cycle.py::cycle_all`: full reconcile (gh fetch, base-distance, per-PR JSON, PR flat cells, git-state cells, pills).
- **Fast tick** (30s) — `cockpit.py::_fast_tick`: pidfile re-assert, then a network-free republish of git-state, per-worktree cost, PR flat cells from disk, workspace-name and sidebar-colour reconcile, trailing-fold restore, and the `idle=` pill re-assert. The last three write into live cmux and are `dry`-gated; the local disk republish is not.

New cell → writer in `cache.py`, call site in the slow tick and/or fast tick. **Never** let a renderer read source state directly.

**Every flat cell is keyed by worktree path (`cache.py::cwd_cache`) or session id — never by branch.** A branch name is unique inside one repo and nowhere else, so a branch key silently merges every repo holding a worktree of that name: three `khivi/ci-gatekeeper` worktrees shared one `pr-num`, `pr-snoozed` and `base-distance`, so all three rows rendered whichever repo's daemon wrote last, and `z` on any of them wrote a `NudgePref` under *its own* repo and *another* repo's PR number — a pref no cycle ever reads, so the row never folded. Four rules:

- **The key is the *worktree*, deliberately not the repo+branch pair.** Both are unique, but only the path is something all three renderers (TUI row, starship footer, `restamp_pref`) already hold: starship is a separate process with no `gh`, so it cannot resolve the nwo the PR snapshots are filed under, and `git-repo` carries the mutable config label instead.
- **The path travels in the PR payload (`write_pr_cache`'s `cwd`)**, because `republish_pr_caches_from_disk` runs on the fast tick from the JSON alone. Re-deriving it there from `branch` would reintroduce exactly the ambiguity the key removes. Dedup in that pass is therefore **per worktree, not per branch**.
- **A PR with no local worktree writes no cells at all** — no row, no session, nothing that would read one. Its JSON snapshot is still written; every decision the cycle makes reads that.
- **`find_pr_payload_for_cwd` is the lookup a republish uses**, preferring the snapshot stamped with this exact worktree and falling back to a branch match only for a payload written before the field existed.

### `cmux events` is a doorbell — it wakes a tick, it is never state

`lib/events.py::watch_workspace_events` reads events as a **trigger only**: an event kicks the **fast** tick, which re-derives every workspace fact as the timer would. Nothing downstream reads a payload. The 30s interval stays the correctness floor, so every failure mode degrades silently. Gated on `has_capability("events.v1")` + `is_cmux()` — the shared probe, never a private `cmux capabilities` read. Five rules:

- **Subscribe to `workspace.created` + `workspace.closed` only.** Every tick writes pills, colours and names, which cmux reports as `sidebar.metadata.*` / `workspace.renamed` — subscribing to those makes the daemon ring its own doorbell forever.
- **Debounce lives in the app, not the reader.** `_events_pending` is the subtlety: an event arriving *during* a fast tick can't be dropped, so `_run_fast`'s `finally` owes one more kick.
- **The cursor file is cmux's resume bookmark, not a cache cell.** **Do not** grow it into stored inventory or route it through `cache.py`.
- **The child must die with the TUI** — `on_unmount` `killpg`s it, since killing the leader alone leaves a grandchild holding the stdout pipe.
- **A stream that dies instantly gives up** after `_MAX_FAST_EXITS` quick exits, or a cmux that rejects `events` respawns in a tight loop forever.

**Do not** feed an event into a decision, a cell, or the slow tick.

**The one payload read is the sidebar X, and it is a *gesture*, not state.** Derived inventory cannot express it: "the user just closed this" and "this worktree has no workspace yet" are the same observable state, which is why the X used to be a no-op. `_closed_workspace` lifts `workspace_id` + `cwd` into an optional `on_closed` callback; the payload is never cached or read by a tick, and every fact the teardown decision uses is still re-derived. Four rules:

- **`on_closed` is additive and optional** — a caller passing nothing gets byte-identical prior behaviour, and a raising handler is logged rather than killing the stream.
- **cockpit's own closes must be filtered, and this is the load-bearing half** — cockpit closes workspaces for four reasons that aren't teardown, so unfiltered, parking a repo would tear down every worktree in it. `_note_self_close` records the UUID **before** the close and lives inside `cmux_close_workspace_best_effort`, the funnel every close path goes through; **do not** re-implement it per call site. Keyed by **UUID, not cwd**.
- **It routes to the refusing gate, never to force.** Refusals are **loud**. **Do not** map the X onto `C`.
- **`quiet` suppresses the missing-worktree toast only** — every refusal still toasts, since the X gives no other feedback.

### `sidebar_color` — cosmetic, cmux-only, per-repo

Applied slow-tick via `_apply_repo_colors` and fast-tick via `_tint_repo_workspaces`, deduped in `pill_state` under `color:<ref>`. Validated at preflight (`_validate_sidebar_colors`, `sys.exit(2)` on unknown). Valid set = `colors.CMUX_COLOR_ANSI`.

### The `pr` pill replaces cmux's native sidebar PR row — which cockpit cannot set, and must not trust

cmux resolves a branch to a PR **by branch name alone**, so a branch that carried more than one PR renders as the first, and a draft renders as plain `open`. The row is unreachable from here. So it is turned **off** by the user (`sidebar.showPullRequests: false`, a cmux setting cockpit never writes) and cockpit renders its own from `ctx.prs`, which is `is:open`-scoped. Five rules:

- **It is the one pill that names the PR, so `draft`, `state` and the four `ci_*` kinds stop rendering in cmux** — still *emitted*, but their `_CMUX_RENDERERS` entries are `None`. **Do not** re-enable any without turning the `pr` pill off in the same change.
- **CI rides the pill as a trailing glyph, and a non-passing build takes the colour**, since a cmux pill carries exactly one. `passed` and absent CI leave GitHub's colour language alone. **`"ci"` must stay in `ACTIONABLE_KEYS`** even though nothing writes that key — it sweeps a stale `ci=` pill off an older install.
- **`PR_KEY` is in `_PR_PILL_CLEAR_KEYS` but deliberately not in `ACTIONABLE_KEYS`** — passive like `devdone`, but written by `apply_pills`, so it must be cleared or `clear_pr_pills` strands it on a reused branch.
- **Emoji in the value, not `set-status --icon`** — the renderer contract is a `(key, value, color)` 3-tuple.
- **It is emitted for every state including OPEN**, so a card with no PR pill means no tracked PR.

**Coverage is narrower than the row it replaces** — the pill only reaches tracked workspaces. Accepted cost; **do not** "fix" it by spawning pills for untracked workspaces.

### `orgs` is a load-time defaults layer — nothing below `load_config` knows orgs exist

`config.py::apply_org_defaults` merges an org block into each member inside `load_config()`, so the chain every reader walks (repo → global → default) gains an org rung with **zero** call-site changes. **Do not** add an org-aware reader, an `org_*` field, or a `repo_org(...)` helper. Four rules:

- **One level deep, repo wins.** A scalar the repo sets beats the org's; a *block* unions per **field**. Not a whole-block override, which would defeat `tickets.project` by silently dropping the org's `keys` and `token_env`. **Do not** make it recursive. A block is rebuilt into a fresh dict, never aliased, or one repo's mutation reaches its siblings'.
- **Never persisted** — the config writers re-read `config.json` from disk. **Do not** add a writer that serializes `load_config()`'s dict back to disk.
- **Validated as effective values** — `_validate_orgs` first (a repo naming an undefined org hard-fails), then the merge.
- **TUI ordering only, no nesting.** Deliberately **no** org header row, no cross-repo workspace-group, no park-the-whole-org key.

### Stacked PRs: one cmux sidebar group + one indented TUI row — derived from `PR.base`, never stored

GitHub's API carries no stack id or parent, only that each PR's base is the previous PR's head, so `lib/stacks.py::find_stacks` derives the chain from `PR.base`. **Do not** shell out to `gh stack view`, and **do not** persist a stack. Chains match on `PR.branch`, so a trunk-headed PR's synthesized branch matches nobody's base. A fork yields **one** chain, since a workspace lives in exactly one group.

`cycle.py::_reconcile_sidebar_groups` renders each chain of ≥2 PRs *with local workspaces* as one collapsible group (`square.stack`), headed by a row named **`<tip> (N)`** — the *tip*, not the root — with every member below, tip first. **Do not** re-name the group after the root. cmux-only, best-effort. Three rules:

- **Reconciled against cmux's live `workspace-group list`, not a `pill_state` mirror** — cmux is the authority, so a restart re-syncs for free.
- **Matched by member ref, never by name** — names collide across repos. Only groups overlapping this repo's owned refs are touched, so a hand-built group is never claimed.
- **The group header is cmux's spawned anchor, kept — never a group member.** Re-anchoring onto the stack root *swallowed* the root's own row. The anchor is spawned with `--cwd $HOME` so it sits **outside every registered repo**, or `_reap_workspace_orphans` reaps it and takes the group down, and is **closed on dissolve**, since `ungroup` preserves members and an unclosed anchor strands as a loose row. **An anchor outliving every member is its own leak**, so the reconcile also sweeps groups it owns no member of, gated on both `icon == square.stack` and `members <= {anchor}`.

**The anchor must own a live shell, or the fold dies and nobody notices** (`_durable_anchor`). `create` spawns its anchor with **no command**, and cmux gives such a workspace no terminal surface, so it does not survive — silently, since cockpit ran no dissolve and the next cycle rebuilds the whole fold. `create_workspace_group` swaps in an anchor spawned with `ANCHOR_KEEPALIVE_COMMAND` via `add` + `set-anchor`, then closes the husk through `cmux_close_workspace_best_effort` — **never a raw close**, or the `workspace.closed` reads as the user's ✕ and routes into teardown. Fails open. **Do not** "simplify" this back to using the anchor `create` returns. Diagnostic note: `watch.log` is a `deque(maxlen=200)`, so below 200 lines a missing `ungrouped` really means no dissolve ran.

The dedicated anchor sets the **minimum fold size at one, not two** — cmux drops a group only when the *anchor* is its last workspace, so `create_workspace_group` refuses only an **empty** ref list. Callers impose their own floor; the reviews fold has none.

**A snoozed stack gives up its group and joins the `snoozed` fold whole**, since a workspace lives in exactly one group and there is no nesting. A chain whose **tip** is snoozed is diverted into `folds.snoozed` as one contiguous run and left out of `desired`. Four rules: it keys on the **tip**, so one snoozed dependency doesn't bury the active chain above it; the divert is gated on `folds is not None`, or a repo-scoped kick strands the members as loose rows; it **replaced a position-only answer, which is why move_workspace_group_to_start is gone** — **do not** re-introduce a position-based answer; and the chain inherits the pile's park and collapse.

Grouping is **cosmetic** — it never spawns a worktree, closes a member, nudges, or writes a cell.

**The TUI shows the same stack as indentation, read off a flat cell** (`pr-base`), since renderers never read source state. `stacks.py::stack_order` returns `(index, depth)` so `_stack_rows` sorts each chain under its **tip**. **The nesting is exactly one level deep** — **do not** restore the per-level cascade. Keyed by **index, not branch**, so duplicate branches each keep a row and a base cycle falls back to flat rows.

### Reviews and snoozed PRs sink to the bottom — TUI row bands + two trailing cmux folds

Both surfaces answer *is this my turn?* from the same flat cells, never a stored marker: the table as row **order**, the sidebar as two collapsed **groups** parked at the bottom, per *org*.

**The snoozed band collapses behind a per-repo `▸ N snoozed` row, which is why a snoozed row carries no glyph.** Per *repo*, since the table has no org row. Six rules:

- **`z` opens it — one key, three meanings**, like `h`. **Do not** hang this off `h` or a new key. Enter and a **single** click toggle it too.
- **The fold row carries `SNOOZED_CAP` and deliberately not `HEADER_CAP`**, which would hide `z` itself and advertise `h`. `_skip` suppresses each row key except `SNOOZED_ROW_ACTIONS`. Both **drop their `ACTION_REQUIRES` entry** via `req = None`, **not** an early return — that would skip `BACKEND_ACTIONS`, and `A` is cmux-only.
- **The cursor-skip loop must stop there**, so the row keys off its own sentinel rather than nesting under `HEADER_KEY_PREFIX`, or a repo whose rows are all snoozed is unreachable.
- **A snooze moves the cursor onto the fold that swallowed the row**, since `update_inventory` restores by row *index*. It **asks the table** rather than predicting from the pref, because a snooze below the tip folds nothing.
- **The fold takes a stack whole** — `_split_snoozed` partitions at *chain* granularity.
- **Snooze has no row glyph but still suppresses the 🔔** — `_status_glyph` keeps its `snoozed` branch and returns blanks, since `pr-nudge` is never blanked for a snoozed PR. **Dropping the glyph must not drop the suppression**; that regression shipped once and `test_a_snoozed_row_shows_no_bell` pins it. 🔇 still wins for a row muted *and* snoozed. Expansion is session-only.

**The TUI renders the folds as row order, in three bands per repo** (`_row_band`): **0** my queue, **1** a coworker's PR I'm reviewing, **2** one I've snoozed. Four rules: **snooze outranks review**; the sort is **stable**; a chain bands by its **tip**, never its deepest member; and **mute is deliberately not a band**, since 🔇 means "stop nudging me about a PR I'm working on". Bands are **per repo**, unlike the sidebar folds.

**Coworker reviews fold into one trailing `<org> reviews (N)` group — per *org*, so it is the one fold that spans repos.** `not PR.mine` workspaces get the `eyeglasses` icon, re-parked at the bottom every cycle (`--to-index 9999`; `workspace-group list` reports no index, so the move is unconditional). The pile is keyed by the repo's **`org`**, or its `name` when it has none — an org is a team and a team's PRs are one review queue however many repos they span.

That key is why this is the **one** fold with its own reconcile pass: a repo alone can't tell whether its lone review has siblings elsewhere. `_reconcile_sidebar_groups` only *collects* into a `ReviewFolds` accumulator; the cross-repo `_reconcile_review_groups` drains it once, after every repo. Ownership splits along the icon: the per-repo pass filters review-iconed groups out entirely, and every review-iconed group is the cross-repo pass's. Three further rules: **stacks win the overlap**; a **lone** review folds too; and the drop-departed-members loop guards on `folds.owned`, not the bucket, since a hand-added foreign workspace is the user's. **Do not** persist a "this is a review" marker.

**An incomplete cycle suspends the dissolve — `ReviewFolds.partial`, guarding the one irreversible thing this pass does** (it closes the fold's anchor). Buckets are built from the `gh` fetch, so a repo that never reported leaves its bucket **absent** — indistinguishable from "no reviews any more". Two routine paths get there, and both set `partial`, which skips the dissolve loop while re-park, rename and re-member still run. Without it a network blip reads as a decision, tearing down all four folds and rebuilding them every cycle. **Do not** re-key this on the bucket being empty rather than the cycle being complete.

**Snoozed PRs fold into a second trailing `<org> snoozed (N)` group, below reviews** — the *same* accumulator and pass walking `_TRAILING_FOLDS`. Membership is `NudgePref.snoozed`, and it holds **my** PRs as well as coworkers'. Four rules: precedence on overlap is **stacks → snoozed → reviews**; **order comes from the pass order, not a rank field**, so **do not** reorder that tuple expecting names to sort it out; the two families are **matched separately**, or one pile claims the other's fold and re-icons it; and the per-repo pass must filter **both** icons out of `all_groups`, or a snooze-iconed group is dissolved every cycle.

**Both trailing piles are born collapsed — create-time only, never a per-cycle re-assert**, since cmux creates every group expanded. Only `_reconcile_review_groups` passes `collapsed=True`; a **stack** is the live queue and keeps its members visible. The create-time restriction is load-bearing: expanding a fold is a deliberate gesture, so a per-cycle collapse would slam it shut. **Do not** promote this to an unconditional re-assert, and **do not** read `is_collapsed` back to "correct" a fold.

**A fold lost mid-interval is rebuilt by the fast tick — `cycle.restore_trailing_folds`, a *replay* of the slow pass's decision, not a second authority.** The slow pass records each standing fold's `(name, refs)` in `pill_state`, and the fast tick replays it verbatim. Six rules:

- **It can only create** — no dissolve, rename, member change, or re-park. That asymmetry is the entire licence to run it at 30s. `test_restore_can_only_create` pins it.
- **It reads `read_workspace_groups`, never `list_workspace_groups`**, which flattens a failed read to `[]` — fatal for a pass that creates, since "cmux answered nothing" and "cmux did not answer" become the same empty list. **Do not** re-merge the two functions.
- **A live group of the same icon sharing any member means the fold is there** — matched by overlap, never by name.
- **Refs are filtered against live workspaces**; all gone → skip, and **leave the record**.
- **The record lives in `pill_state`, never on disk.** With no records it makes **no cmux call at all**.
- **Retiring a record rides `folds.partial`** — it is a dissolve by another name, disarming the repair.

**This does not fix whatever destroys the folds** — still unidentified; the forensics rule out every cockpit path that closes a workspace, since all of them print. **Do not** read this as having closed that question.

### Workspace names track repo + branch (`wt.workspace_name`), re-asserted on both ticks

`wt.label` (`git.py::branch_label`) derives from the *branch*, not the dir basename, and never to `""`. The cmux name is the bare `label` under an optional `<tag>·` prefix — repo is conveyed by tint, and by a name prefix only where the repo opts into one, so the two never double up by accident. Every path that spawns, renames or matches **by name** uses `workspace_name`; cwd→path matching is unaffected. `rename_workspace_if_needed` re-asserts idempotently. Cosmetic, never a `send`. **Consequence:** to relabel, rename the *branch*. **Consequence:** two repos with the same branch label produce the same name — cosmetic except orphan-auto-spawn's name-clash skip. **Exception:** any main-branch worktree — `wt.is_primary` **or** `wt.branch in MAIN_BRANCHES` — keeps its custom name; the branch half matters in a **bare repo**, where no sibling is ever `is_primary`.

**`sidebar_tag` is opt-in per repo and applied by ONE function, `git.py::tag_workspace_name`** — tint alone stops scaling past `CMUX_COLOR_ANSI`'s sixteen names, several of which don't read apart, and a repo with no `sidebar_color` carries no repo signal at all. Four rules:

- **Both naming halves apply it, or a fresh workspace gets renamed a tick after creation.** The tag rides `Worktree.sidebar_tag` (threaded like `branch_prefix` through `worktrees`/`worktrees_basic`) for the daemon, and `spawn.py`'s own `ws_name` for `cockpit new`. **Do not** add a third naming site.
- **`spawn.py` resolves the tag AFTER every routing hop**, since routing rewrites `args.repo` — resolving earlier tags the workspace with the cwd's repo rather than the target's. A branch with no repo determined (`--cwd` alone) gets **no** tag; **do not** fall back to `discover_repo()`, which guesses from wherever the user was standing.
- **Only call sites that read `workspace_name` need to pass it** — the fast tick's rename pass (`cockpit.py`), `cycle_all`, the orphan reap's `wt_by_name`, and the TUI's `_resolve_worktree` (whose `f` spawns by `workspace_name`). The cwd-matched passes (`_park_workspaces`, the ask fan-outs) are unaffected.
- **The primary checkout takes no tag** — its `workspace_name` is already the repo name, so tagging would print the repo twice and break `_workspace_ref_by_name`, which looks it up by that bare name.

### The daemon creates worktrees in the background — never blocking the tick on `git`

`cycle.py::_spawn_missing_workspaces` shells out via module dispatch in a detached `Popen(start_new_session=True)`:

- **My PR, no worktree** → `cockpit new --pr <n> --repo <name>`. Always on.
- **`review_prs` (per-repo, default false)** → every coworker open PR without a worktree. **Dependabot PRs are excluded by default** unless the repo sets `"dependabot": true` — the single gate, since the other paths are `author:self`-gated. **External (non-collaborator) PRs are also excluded by default** unless `"review_external": true`, because a fork contributor's PR body and diff are untrusted content and auto-spawning a Bash-capable agent on them is a prompt-injection risk on a public repo. The two gates are independent.

The seeded first turn is the configurable `skills.review`, defaulting to the **built-in `/review`**, which resolves in every spawned workspace. The auto-review is **dry-run** — it reports findings and asks before posting. **Keep the spawn-layer default in sync with `REVIEW_COMMAND_DEFAULT`**; don't hardcode a command string in `spawn.py`. `skills.plan` and `skills.actions` are sibling seams, both default `""`, each followed by the shared `plan_tail.txt` gate.

`_bg_spawn_pr` guards in-flight launches in `pill_state` against a double-launch and logs to `$COCKPIT_HOME/spawn.log`.

**A worktree younger than `_SPAWN_ADOPT_GRACE_SECONDS` (120s) is not adopted — `_too_young_to_adopt`, guarding *both* paths that attach a workspace to an existing worktree**, since `cockpit new` creates the worktree and its workspace as two steps of a separate process and a poll landing between them spawns a second Claude on the same task. Four rules: **both call sites**, since the `--pr` form races identically; **age, deliberately not a lock or spawn-side registration**, which would mean cross-process state on disk plus a stale-lock failure; it **fails open** (an unstattable path reads as old enough) — **do not** invert that test; and it is **not configurable**, since the two creating paths have no worktree to age and their in-flight guard is process-local and cannot see a user-typed `cockpit new`.

**A coworker's PR is review-mode everywhere — never author-mode.** `PR.mine` (defaults **true**) gates **no nudge** (`nudge_issue` requires `mine`, which also takes the 🔔 quiet, while pills, cells and the Issue column still render the issue) and a **review-mode seed prompt** (`build_pr_prompt` branches on `mine`, so a coworker's gets `review.txt` rather than `pr_authority.txt`'s force-push grant). **Do not** add a nudge or authority grant keyed off the worktree's existence alone.

**`use_worktree: false` repos opt out of all of the above.** A per-repo bool defaulting **true**; the inverse polarity is deliberate. `_spawn_missing_workspaces` **early-returns** for it. The row renders **only while a workspace is open on it**, collapsing to the group header otherwise, with `n` to start one. Registration is idempotent. **The `n` row-key branches on it.** **GOTCHA:** every read is `not repo.get("use_worktree", True)` — a bare `.get()` treats an unconfigured repo as opted-out and silently stops auto-spawning it. **Do not** let these repos reach any auto-spawn.

### The live PR list carries at most one PR per head branch

cockpit joins a PR to its worktree, workspace, row and cache **by head branch**, so two PRs on one head make readers disagree in *different directions*: a `{pr.branch: pr}` comprehension is last-wins while `match_worktrees` emits a pair for each, so `_spawn_missing_workspaces` spawns a workspace `_dedupe_workspaces` closes next cycle, forever. `gh.list_relevant_prs` collapses through `gh._one_pr_per_branch`; `cache.prune_superseded_pr_caches` is the on-disk half. **Do not** answer this by hardening one reader — the fix belongs at the producer.

**Both fetch legs can produce the collision.** The per-branch alias returns the **newest** PR whatever its state, so a duplicate opened seconds later and closed wins "newest" — which is why `gh._pr_rank` prefers OPEN **before** `updated_at` and number, matching `cache._pr_payload_rank`. **Do not** rank by number alone.

### Trunk-headed PRs get a synthesized branch — `main`/`master` heads never become the worktree branch

`gh.py::pr_worktree_branch(number, head, base)` is the **single** normalizer: a head in `MAIN_BRANCHES` becomes `pr-<N>-<base-slug>`, otherwise the head verbatim. Applied at the four join points that must agree: `_pr_from_node`, `resolve_pr_branch` (which fetches `refs/pull/N/head`, never touching local `main`), `list_open_pr_heads`, and `_relevant_pr_query`/`_collect_nodes` — which rejoin by the **embedded number**, since `headRefName: main` can't be the key. `branch_label` strips the token; `_SYNTH_PR_BRANCH_RE` never misfires on branches carrying `branch_prefix`. **Do not** re-thread `headRefName` straight into a worktree branch.

### Slack thread source — codename branch, MCP-delegated fetch, no `claude mcp list` probe

A Slack permalink classifies as `slack` mode, user-initiated only. Spawn synthesizes a codename branch seeded on the thread's **stable identity** (channel id + message ts), NOT the raw URL, so re-spawns stay idempotent. `_slack_prompt` delegates the read to the in-session MCP. **Never** add a `claude mcp list` pre-flight gate — the probe is unreliable for claude.ai-managed connectors, so a positive-detection gate would silently disable the feature; the prompt's own retry-then-STOP logic handles an absent connector.

**This rule is repo-wide.** Linear's probe hit exactly that false-negative — `claude mcp list` health-checks by connecting, and a managed connector handshakes asynchronously — so it reported Linear absent while live and dropped the ticket fetch on precisely the setup the feature targets. Removed; `prompts/linear.txt` carries the same retry-then-STOP step as `prompts/jira.txt`. **Do not** reintroduce a pre-flight for any provider.

### Prompt prose lives in packaged `cockpit/prompts/*.txt`, not Python string lists

Both prompt families render packaged templates via `templates.render(name, **slots)`. The split is strict: **templates carry only static prose + `{slots}`; the Python builder owns all control flow and value computation**, picking the template when there's a choice and building conditional blocks as slots. A missing slot raises `KeyError` loudly. **Do not** re-inline prompt prose into Python lists, and **do not** add conditionals to a `.txt`. hatchling ships only **VCS-tracked** files, so a new template must be `git add`ed; `tests/test_templates.py` asserts every template resolves.

### The one cache exception: session-scoped cells (written outside the daemon)

`lib.claude.stash_from_stdin` writes `context-<sid>`, `rate-limit-5h-<sid>`, `model-<sid>`, `permission-mode-<sid>`, `transcript-path-<sid>`, `cost-<sid>` from the statusLine stdin. Never read by the daemon, with **one** exception in one direction: it *reads* `cost-<sid>` to derive `wt-cost`. **Do not** extend this exception to a new cell — the daemon must never *write* one.

### `wt-cost` — session cost folded onto a worktree, the daemon's bridge across the two keyings

The `$` column totals what every session rooted at a worktree has spent. The data is keyed by **session id** while every row is keyed by **worktree path**, so `cache.py::write_worktree_cost_cache` joins them on the **fast** tick into one `wt-cost-<cwd>` cell. Renderers read only that cell. Four rules:

- **The join is Claude Code's project-directory slug, walked forwards only** — the slug is **lossy**, so `_claude_project_slug` is only ever applied worktree-path → directory. **Do not** try to recover a path from a slug.
- **The stem is `wt-cost`, deliberately not `cost`** — a shared stem would make `cost_reporting_available`'s glob latch itself on off cockpit's own derived value.
- **The column is gated on the data, never on the plan**, since the blob carries no tier and some plans report `0` for every session. **Do not** add a config field, and **do not** try to detect the plan.
- **Blank is not zero.** The cell is `""` for a costless worktree, because absent means "never reported" as often as it means free.

### Nudge idle-gate: trust the `idle=` pill, NOT cmux's native `Needs input`

`nudge_if_idle` (`lib/cmux.py`) must tell "parked at prompt (safe to `send`)" from "awaiting a y/n permission (unsafe)". cmux native `claude_code=` has `Running`, `Idle`, and the **ambiguous `Needs input`**, which fires for both. The gate: block on native `Running`; safe iff the `idle=` pill is present OR native is the unambiguous `Idle`; self-heal a dropped Stop-hook write by re-asserting `idle=` under native `Idle`. The pill uses a verify+retry loop. **Never** simplify the gate to trust `Needs input`.

**The re-assert runs on the fast tick, not only at line-of-send** (`reassert_idle_pills`), because the two at-rest signals differ in durability: native `claude_code=` vanishes on a cmux restart while the pill persists, and a workspace losing the former without ever having had the latter is unreachable **for good** — no keypress recovers it, since the re-assert used to live inside `nudge_if_idle`. Three rules: it **only ever writes** a pill, never clears one; it is **`dry`-gated**; and it **cannot** help a workspace reporting no native state at all. **Do not** widen it to a second at-rest authority.

**The liveness guard in the hook must compare against a listing that carries workspace *ids*.** `CMUX_WORKSPACE_ID` is a UUID while `cmux list-workspaces` prints only refs and names, so a guard matching against that output silently `exit 0`s for **every** session, leaving the whole fleet unreachable. That shipped, surviving review because the tests stubbed the listing with ref-shaped ids real cmux never sets. The guard reads `cmux workspace list --json`. **Do not** stub that listing with a `workspace:N` id.

**The gate is factored out as a *verdict*, and every caller uses that one function.** `_idle_skip_reason(status_lines)` returns why a send would be refused, in the guard order `nudge_if_idle` applies; `rest_skip_reason(ref)` is the public wrapper a *display* caller uses, so warning and decision cannot disagree. **Do not** re-derive the verdict at a call site — an earlier `a` hint did and got the guard order wrong.

**Every message is collapsed to one line before the send — `cmux.one_line`, delivery correctness, not cosmetics.** `cmux send` synthesizes keypresses; both spellings of a newline arrive as **Enter**, which in a Claude composer means submit, so an un-normalized multi-line message submits the first fragment as its own truncated prompt. Live, not hypothetical, and it fails silently. Three rules: it lives **inside `nudge_if_idle`**, the funnel every send path goes through — **do not** re-implement it per call site; it runs **before the `dry` print**; and it is why the `a` modal is an **`Input`, never a `TextArea`**. **Re-probe before relaxing any of this.**

### `cockpit broadcast` reuses the nudge gate — no second send path, no cache cell

`cockpit/broadcast.py` fans a line out to every idle workspace via `nudge_if_idle(..., tag="broadcast")` with no `pref_key`. A one-shot gesture: no cell, pill, or `pill_state`, and skipped refs are printed, never queued. **Do not** give it its own send path, idle check, or cache cell — extend `nudge_if_idle` instead.

**`--repo` is a filter over that one loop, never a second scope.** It matches each workspace's cwd against the repo's own `worktrees()` (`_repo_paths`) — **never a path-prefix test**, exactly like `_park_workspaces` and the repo-header `a`, since a worktree usually lives in a *sibling* directory. The repo is named by its **one** identity (`_repo_label`, the `name`-or-basename the table shows), casefolded — **do not** accept the path basename as a second spelling, since under a bare clone every repo's path ends in `.bare` and `--repo .bare` would then broadcast into whichever one sorted first. An unknown name exits **2** listing the configured repos rather than silently broadcasting to everything. The unscoped path makes **no** config read at all — broadcast reaches workspaces cockpit doesn't manage, so reading the config there could only narrow it.

### Nudge prefs are keyed per repo — a PR number alone is not an identity

`NudgePref` persists one JSON file per PR at `$COCKPIT_HOME/cache/nudges/<repo>__<number>.json`, keyed by the git **nwo name** — the same key the PR cache files use. Every entry point threads it, including the TUI's `_resolve_row_pref` (via `_cache_repo_name`, **not** the config `name`) and `cockpit nudge`'s `_resolve_pr`, which exits 2 rather than falling back to a bare number.

Keyed by number alone, two repos' PR #10 shared one file, so a mute silenced both and **each repo's cycle woke the other's snooze every tick**. **Do not** add a call site that invents a key without a repo, and **do not** default `repo_name` to `""`.

`load_pref` falls back to a legacy bare-`<number>.json`, deliberately **never unlinked** since several repos may still read it.

### Backend capability gate — probed once at startup, warns and degrades, never dies

`lib/tool.py::resolve_tool` checks presence only, so a cmux too old for a verb surfaced as a mid-cycle no-op. `lib/capabilities.py` answers whether it is new enough on **two independent axes**: `REQUIRED_VERBS` (each mapped to the tier it disables) and `REQUIRED_CAPABILITIES`. Verbs are parsed out of `cmux --help`, since there is no machine-readable list; a parse miss degrades to a warning. Six rules:

- **`capabilities`'s own absence from the verb list is the too-old signal**, reported as its own warning and explicitly *not* as "this cmux offers no capabilities". An empty *verb* set likewise warns about nothing.
- **Warn, never die** — the git+gh half of the dashboard works with no backend at all. **Do not** promote any of these to `sys.exit(2)`.
- **Cached in `capabilities.probe`, never in `resolve_tool`**, which stays uncached per call so tests can vary PATH and config.
- **Daemon-only** — `cockpit setup` may be about to install the backend. Skipped when the backend isn't cmux.
- **`has_capability(id)` is the gate for features built on the baseline** — pair it with `is_cmux()`, don't replace it.
- **Every entry must name a tier cockpit actually HAS.** `terminal.replay.v1` and `notification.feed.v1` gated features that don't exist and were removed, with a test pinning them out. Conversely `workspace.groups.v1` is required *because* the verb axis structurally cannot cover `workspace-group`, which is **absent from `cmux --help`'s `Commands:` list** though documented under its own `--help`, so a `REQUIRED_VERBS` entry would report it permanently missing. **Do not** add `workspace-group` to `REQUIRED_VERBS`, and **do not** narrow its capability. The full map is `docs/cmux-surface-audit.md`, whose live half is `tests/e2e/test_cmux_surface.py`. **Do not** write verb counts into prose here or there.

### `$COCKPIT_HOME` may be inside a file-sync folder — write pid-scoped, warn on conflicts

- **The temp file in `config.py::_atomic_write_text` carries `os.getpid()`.** `os.replace` is atomic, so a fixed `<name>.tmp` never yields a *torn* file — it yields a **wrong** one, since several cockpit processes write these concurrently and the loser's whole content lands under the winner's name. **Do not** go back to a fixed suffix, and **do not** re-inline the write.
- **`preflight._warn_sync_conflicts` surfaces a conflicted copy and cannot do more** — the conflict is resolved outside the process, so the edit is silently gone and the only symptom is a setting that "didn't take". It matches **only** `conflicted copy` and `.sync-conflict-`; iCloud's, Drive's and OneDrive's spellings are indistinguishable from ordinary filenames, and a false alarm trains the user to ignore a warning that means real data loss.

### Machine-local runtime state lives in `$COCKPIT_RUNTIME_DIR`, never `$COCKPIT_HOME`

`config.COCKPIT_RUNTIME_DIR` owns `PID_FILE` and `daemon_signal.STATE_DIR`, and **deliberately does not follow `COCKPIT_HOME`** — both are meaningless off the machine that wrote them, and under a synced home two machines fight over one pidfile and either can drain the other's queue. Five rules:

- **Not `$TMPDIR`**, where the flat cells live: the pidfile is an **IPC rendezvous**, and `TMPDIR` resolves per launch context, so the two sides would look in different places.
- **Not a hostname stamp** on a shared path — `gethostname()` drifts on macOS, and a drifted hostname reads as a *third* machine.
- **The legacy files are named, never read and never deleted** — not read because honouring a queued close from an unidentified machine is the bug being fixed; not deleted because another machine may still be running an older cockpit against it.
- **Test isolation sets the env var, not just the module attributes** — fixtures reload `lib.config`, which re-derives these paths and silently undoes an attribute-only patch. Fixtures isolating only `COCKPIT_HOME` **do not** cover the runtime dir, by design.
- **`preflight`'s two `COCKPIT_HOME`-inspecting warnings need a hermetic home in tests**, or assertions turn on the developer's real `~/.config/cockpit`.

### Config surface has three faces — keep them in sync

`cockpit/lib/config.py` is the authoritative reader, with two mirrors that drift silently: `cockpit/config.example.json` (documentation only, never installed) and `docs/config.md`. **Any change to a config field MUST update all three in the same PR.** (Provider ticket fields also flow through the provider's `CONFIG_FIELDS` — a fourth touch-point.)

### `tickets` config — the one provider selector (replaced the `use_linear` bool)

`tickets` is an **object** — `{provider, close_on_merge, dev_done, merge_done}` plus per-provider extras; the bare string is shorthand for `{provider: …}`. **The field table and defaults live in `docs/config.md`.** Nine invariants:

- **One field per concept, across every provider.** **Do not** re-split `dev_done`/`merge_done`/`token_env` per provider. (`project` and `board` are *not* the same concept and stay distinct. Trello keeps `key_env` **and** `token_env`.)
- **The superseded spellings are not read — they hard-fail** (`preflight._check_legacy`, `_LEGACY_TICKET_FIELDS`). Accepting both would leave the effective schema twice the documented one and let a typo'd canonical name read as configured. The check lives in preflight because `tickets_field_errors` can't tell a rename from a typo. **Do not** re-add an alias arg.
- **Resolution is per-field**, repo-block → global-block → default. Provider selection resolves by `_tickets_block`, where the repo's whole block wins outright.
- **The provider selects four things** — the spawn prompt, the `devdone=` pill, the done-on-merge writer, the TUI ticket columns. **`spawn.py`'s half is deferred, and both halves are paid-for regressions**: it resolves via a `(mode, provider)` → builder table (`_TICKET_PROMPTS`; add a provider by adding its pair, never by branching on a provider name), and (a) it reads `repo_tickets`, **never** the global `tickets()`, or a provider declared on the repo entry or an org block loses its fetch+rename prompt; (b) it runs **after** every routing hop, or it resolves the cwd's repo rather than the target's. **The ticket-key routing gate is per-repo for the same reason.** **When no pair matches**, the spawn falls through to `_plan_only_prompt(..., source=…)` seeding the bare ref — **do not** drop the `source` slot.
- **Credentials are env-only** — config carries the env var's *name*, never its value.
- **`start_label` is the one *spawn-time* tracker write** (GitHub, opt-in), best-effort — a failed label never blocks the spawn.
- **`use_linear` and the flat `linear_*` keys are gone**, and all five **hard-fail** in preflight naming the replacement rather than being silently ignored, since each one *disables* something the moment it stops being read. There is consequently **no** fallback path in any reader. **Do not** re-add a "guess the provider from a sibling field" rule.
- **`linear_team_keys` is Linear-*named* but provider-neutral**, since Jira declares `keys` too; a reader meaning "this repo is Linear" must pair it with the resolved provider.
- **`tickets::provider_for(cfg, repo_entry)` → a `TicketProvider` is the single source of truth** for `dev_done_value`, `parse_footers`, `fetch_states`, `fetch_titles`, `narrow_repos`. **Do not** re-introduce `provider == "github" ? …` ternaries — add a `TicketProvider` field.

**Extending the schema.** Each provider exports `CONFIG_FIELDS`; `tickets.py` composes them and validates via `tickets_field_errors`, which also rejects another provider's field. **Add a new setting** to the provider's `CONFIG_FIELDS` + a reader in `config.py` — not to a hardcoded list in preflight, and **not** as a top-level flat key. Several names are declared by more than one provider, so the allowed set is composed from the *active* one — **do not** flatten the per-provider schemas into one merged dict.

**Ticket→repo routing is two-stage: a free match, then a paid tiebreak.** `find_repos_by_ticket_key` over `tickets.keys` is offline and enough when one repo owns the team; in the many-repos-one-team shape every member declares the same keys, and the old fallback silently landed the worktree in whichever repo you were standing in. The discriminator is `tickets.project`, which costs a fetch, so `TicketProvider.narrow_repos` is called **only when the free match returned >1**. Three rules: it **never narrows to zero**; it groups candidates by **resolved credential**, since a team key is workspace-scoped and asking one org's workspace about another's ticket answers about a **different issue that merely shares an identifier**; and it is **routing-only**. **Do not** collapse this back to one key read off `candidates[0]`.

**Both stages are provider-shaped, and `spawn.py` must not branch on a provider name.** `_route_by_ticket` is the shared tail. **Linear** free-matches `keys`, tiebreaks on `project`. **Jira** free-matches `keys` — the **same field and reader**, since a Jira project key IS the identifier prefix, the analogue of a Linear *team* — and stays `_no_narrow`; **do not** give it a `project` field or duplicate `find_repos_by_ticket_key`. **The shape gate stays `LINEAR_RE_CI`**, since widening it only in the lookup is a no-op and widening it for real reclassifies branch names like `feature2-1` as tickets. **Trello** has **no free match at all**, so the `tickets.board` opt-in *is* the discriminator, and with none declared the spawn makes **zero** network calls. **GitHub** needs neither stage.

**A ticket URL is a first-class source, for every provider.** Linear and Jira URLs now match into the **same mode as the bare id**, extracting the identifier so everything downstream is byte-identical; previously they fell through to `branch` mode and `git worktree add -b <the whole URL>` died. **Do not** pass them through verbatim the way `slack`/`trello` mode does.

**Per-org credentials come free from the env-*name* indirection** — the name reader is another `_tickets_field` call, so an org block covers every member with **no** org-aware machinery. Two rules:

- **The identity caches key on the resolved secret, not the env var name** (`_secret_fingerprint`). Single-slot on one global env var, org B read org A's cached viewer id and then silently skipped *every* ticket.
- **Spawned sessions never receive ticket credentials** — `_bg_spawn_pr` passes the env minus `config.credential_env_names(cfg)`, since spawn-time fetch is MCP-delegated and a `review_prs` session runs over an untrusted diff. Strip by *resolved name*, never a prefix guess, and touch nothing else (`PATH`, `COCKPIT_HOME`, `CMUX_*` must pass through). A warning, log line, or error message carries an env var **name**, never a value.
- **An unset credential warns at startup, for *every* provider — `TicketProvider.credential_envs`** — otherwise it is a silent degrade whose only symptom is a bare id in the Ticket column. Three rules: **the provider names its own variables** (Trello returns **both** halves of its pair; GitHub **none**) — **do not** re-add a provider-name ternary, since the gate is `provider_for(...) is not None`; **resolution is the repo's**; and it stays a **warning**. It must stay in step with `credential_env_names`, pinned by a test.

### `devdone=` pill — the ticket provider is the one auxiliary (read-only) state source

Gated on `repo_tickets(...) != "none"` + a PR-body delivery footer. Delivery is **footer-only** (`provider.parse_footers`) — never a branch-slug or bare mention, which catch non-delivered tickets. The `{provider, tickets, fetched_at}` block is cached in the PR JSON under the `ticket` key, carrying the resolving provider so it is self-describing. `_prefetch_linear_blocks` decides refetch-vs-carry-forward per PR (footer-id change or past the TTL), then resolves the union of due ids across *all* a repo's PRs via `provider.fetch_states` — Linear one **batched** query per team, GitHub one `gh issue view` per issue, Trello one card fetch. It runs once before the write loop so each `write_pr_cache` still overwrites against the old file. `title` never feeds a decision. `_track_dev_done` raises the pill only when *every* delivered ticket equals `provider.dev_done_value(...)`, casefolded. Passive, never a `send`. **Do not** drop back to a per-PR fetch fan-out. A per-source failure isolates to its own ids.

### done-on-merge — the daemon's only sanctioned tracker *writes*, dispatched per provider

Opt-in via `tickets.close_on_merge`. `_transition_merged_tickets` dispatches on `provider.name`, each writer fired on `_is_post_merge_stale` independently of teardown, guarded by a per-run marker in `pill_state`, viewer-gated, idempotent and logged:

- **Linear** moves the ticket to `merge_done` via `issueUpdate`; skip unless assigned to the API-key `viewer`, skip if already at target or canceled (both states are `completed`, so name-equality decides). Viewer id and team state maps are cached; the viewer fetch is **lazy**.
- **GitHub** runs `gh issue close` on each delivered issue still open and assigned to the auth login — mainly catching cross-repo refs, since GitHub auto-closes same-repo ones.
- **Jira** transitions via the REST transitions API, since Jira moves issues by *transition*, not a direct status set.
- **Trello** moves the card to the list named `merge_done` (no default). Skip unless I'm a member.

A falsy/failed identity fetch is never cached; a failed write clears the marker to retry. **Precedent for any future daemon tracker write:** opt-in, viewer-gated, idempotent, logged.

### `update_stale_branches` — the daemon updates a PR head *server-side*, never by rebasing the worktree

Opt-in, slow-tick `_update_stale_branches`. Nine rules:

- **The trigger is `mergeStateStatus == "BEHIND"`, never the local `behind_of_base` count**, which is normal and harmless without the protection rule and would fire on every repo forever. **Do not** re-derive this from `base-distance`.
- **The update is the `updatePullRequestBranch` mutation, not `git rebase` + force-push** — GitHub rewrites its own refs, so no conflicted rebase can strand a `rebase-merge` state that reads as dirty and wedges teardown, and no force-push originates from an unattended process. **Do not** replace this with a local rebase.
- **`expected_head_oid` is mandatory and is the compare-and-swap** — the `--force-with-lease` equivalent. **Do not** drop it.
- **Scoped to the two quiescent states — approved or snoozed**, since any other PR may have a session actively committing. **Do not** widen it to every stale PR.
- **The stale-review-dismissal gate on the approved half is the counter-intuitive one**: under that rule any new commit *discards* the approval, so updating an approved PR makes it **un**-mergeable. `update_branch_skip_reason` refuses `APPROVED and <dismisses>`, keying on APPROVED since an unapproved snoozed PR has no approval to lose. **Do not** remove this gate.
- **That verdict is TWO sources, and reading only the GraphQL one silently opens the gate** — `dismissesStaleReviews` covers **classic branch protection only**, and a repo protected purely by **rulesets** reports `branchProtectionRule: null`. Not hypothetical: this repo has no classic protection and two active rulesets. `gh.branch_dismisses_stale_reviews` reads the merged effective rules and the two are ORed. **Do not** gate on the GraphQL field alone.
- **The ruleset lookup fails CLOSED, and it is the one place in cockpit that does**, since being wrong discards an approval and costs a human a second review round, silently. `None` is treated as *dismisses*. Only the approved half pays for it. **Do not** invert this for symmetry.
- **The marker is keyed by head oid**, since a branch goes stale repeatedly while a merge happens once. A *failed* mutation pops the marker; a *skip* keeps it.
- **REBASE rewrites the head, so the local worktree is reconciled — `git.resync_to_origin`, whose guard is a compare-and-swap too.** The reset is refused unless the tree is clean **and** HEAD still equals the pre-update sha; a clean-only test would reset away committed-but-unpushed commits. **Do not** weaken `expected_head` to a dirty-check.

## Dev setup and common commands

```bash

# One-time after cloning — wires pre-commit hooks for commit + push stages:
./setup.sh

# Run THIS worktree's build against a throwaway sandbox (never `uv run cockpit

# watch`, which shares state with the installed daemon — see below):
./dev.sh

# Run the test suite serially — right for a single test or a small selection:
pytest tests/test_spawn.py::test_linear_key_routes_to_matching_repo_without_repo_flag

# Run the WHOLE suite — always pass -n auto. Half the wall clock is idle time

# waiting on the real git/gh/cmux subprocesses, so it parallelises near-linearly

# (115s -> 16s on an 18-core laptop). `addopts` deliberately does not pass it,

# since worker boot costs ~2s and that is pure tax on the single-test line above:
pytest -n auto

# Type-check:
mypy cockpit/

# Lint + format — ALWAYS via the pinned pre-commit hook, scoped to your files:
pre-commit run ruff ruff-format --files <changed paths>
```

**Never lint/format with `uvx ruff` (or a globally-installed `ruff`).** `uvx` pulls the **latest** ruff, whose rules drift from the pinned version — running it tree-wide rewrites lines in files you never touched, producing churn the pinned hook then fights on commit. The pinned hook *is* the formatter, and it's what CI enforces.

### `./dev.sh` — five isolation axes, and none of them is optional

`uv run cockpit watch` from a worktree is **not** a dev run: it shares every piece of state with the installed daemon. `dev.sh` seeds a `.cockpit-dev/` sandbox. Each axis blocks a different path to real damage:

- **`COCKPIT_HOME`** → `config.json` and the PR cache.
- **`COCKPIT_RUNTIME_DIR`** → `cockpit.pid` and `close-requests/`, a *separate* variable because they deliberately don't follow `COCKPIT_HOME`. Shared, the dev build reclaims the pidfile and every `cockpit close` in *every* worktree routes into it, running the real `teardown`.
- **`TMPDIR`** → the flat cells are `tempfile.gettempdir() / "cockpit-cache"`, **not** under `COCKPIT_HOME`, so isolating only the home leaves the fast tick repainting the user's live footer.
- **`tool: none`** → every cmux write becomes a no-op through the existing gates. Deliberately an existing, validated config value and **not** a dev-only code branch — a branch nobody exercises in production is the wrong place to put a safety property.
- **`--dry`** → `tool: none` does **not** cover `_maybe_autoclose`, which removes worktrees and runs `git branch -D` through **git**. `--dry` also gates the tracker writes.

  **`--dry` covers three surfaces, each a hole found in review:** the reconcile cycle via `ctx.dry`; the **fast tick**, whose name and colour reconciles touch live cmux every 30s and are gated on `state["dry"]`; and the **TUI row keys that reach outside** (`n`/`f`/`h`/`a`) via `_blocked_by_dry`. Deliberately **not** `c`/`C`, which only enqueue a request already drained under `dry`, nor `m`/`z`, which reach nothing outside `COCKPIT_HOME`. **Do not** narrow the gate back to the cycle.

`dev.sh` forces `--dry` onto **every** `watch` invocation, not just its no-args default. Its config scrub drops `fast_skills`/`slow_skills` and deliberately **keeps** `skills`, which holds only slash-command names.

**`--dry` was fully plumbed through long before it was reachable** — `cockpit.py` hardcoded `dry=False`. It is now threaded via `_build_state(dry)` → `state["dry"]` → `_once_with` → `cycle_all`. **Do not** re-hardcode that call site, and **do not** add a second dev-only suppression path beside it.

`--dry` also suppresses the **cache writes**, which is why snapshot mode copies the real PR JSONs in. Every cmux-facing feature is **inert** under `tool: none`, so the sandbox is right for the table, cells, config, prompts and the cycle's decisions, and wrong for anything cmux-facing.

`dev.sh` **refuses `cockpit setup`** (exit 2), which writes `sys.executable` *outside* the sandbox and from a worktree bakes in a `.venv/bin/python` that dies on cleanup. Guards are covered by `tests/test_dev_script.py`; the happy path is deliberately untested.

## Release versioning

The version is **static** in `pyproject.toml`, read at runtime via `importlib.metadata`. There is no self-update path.

**Every merge to `main` that ships user-visible behaviour gets a release** — brew is the only delivery path. The semver bump is derived from the conventional-commit types `pr-title.yml` enforces.

**The release PR writes itself.** `release-please.yml` keeps one rolling `chore(main): release <version>` PR open and maintains `CHANGELOG.md`. **Merging that PR is the only human step** — it bumps `[project] version`, which is what `tag.yml` watches, so tag → tap → PyPI follow. Several merges batch into one release, which matters because PyPI refuses a re-upload.

Five settings there are load-bearing:

- **`skip-github-release: true`** — left to default, release-please pushes the tag itself under a token whose pushes don't trigger workflows, so `release.yml`/`publish.yml` would silently never run.
- **`skip-labeling: true`** — the **required** companion. release-please keeps release state in a **label on the merged release PR**, flipped in exactly the step `skip-github-release` turns off, so every later run finds a still-`pending` merged PR and aborts before proposing the next version. This wedged the pipeline after v1.8.0 and cannot self-heal. **The state is the label, not a GitHub Release** — cutting the missing Release does nothing. If it wedges again, check `gh pr view <release-pr> --json labels` first and clear a stale `autorelease: pending` by hand.
- **`if: "!startsWith(github.event.head_commit.message, 'chore(main): release')"`** — the job must skip the push that *merged* the release PR, since it re-runs on the commit it causes and races `tag.yml`; with no baseline tag yet it treats the repo as never released and regenerates the whole changelog. Nothing is ever releasable on that push, so the guard loses no coverage. It keys off the **commit subject**, so **do not** set `pull-request-title-pattern` without updating the guard.
- **`token: COCKPIT_GITHUB_API_TOKEN`** — a PR opened by the default `GITHUB_TOKEN` doesn't trigger workflows, so the release PR could never satisfy `main`'s required checks.
- **`concurrency: {group: release-please, cancel-in-progress: false}`** — two runs race the one release branch, and the loser dies **after** writing its commit but **before** updating the PR title, leaving the branch at one version while the PR advertises another. Since we squash-merge, an unnoticed merge commits the wrong release subject over the right tree. **`cancel-in-progress` stays `false`** — cancelling kills a run mid-ref-update. If a release PR's title disagrees with its changelog, **fix the title before merging**.

State lives in `release-please-config.json` and `.release-please-manifest.json` (the one file to correct by hand if a release is cut out of band). `CHANGELOG.md` is **release-please's file** — don't hand-edit it; it's excluded from `markdownlint` because MD004 would fail every release PR.

**`include-component-in-tag: false` is required, not cosmetic** — at its default release-please names tags `cockpit-v<version>` while `tag.yml` pushes `v<version>`, so it can't find the previous release and regenerates the changelog from the entire history.

`./cut-release.sh <version>` is the **manual fallback**: bump, commit `chore(release): <version>`, open and `--squash --admin` merge. It refuses a dirty tree, a non-semver argument, `main`/`master`, and a no-op bump. Using it means `.release-please-manifest.json` must be updated to match.

`tag.yml` watches `pyproject.toml` on `main`, pushes `v<version>` at the merge commit, then cuts the Release (both halves idempotent). It pushes with **`COCKPIT_GITHUB_API_TOKEN`, not `GITHUB_TOKEN`** — a ref pushed by the latter doesn't trigger the two downstream workflows. The Release is **presentation only**; it is *not* what keeps release-please unwedged.

The tag must point at a tree whose version equals the tag minus the `v` — both downstream workflows re-read `pyproject.toml` and hard-fail on a mismatch. The `publish.yml` guard matters most, since PyPI refuses a re-upload. `release.yml` then hands the tarball URL to `mislav/bump-homebrew-formula-action`, which **commits the new `url`+`sha256` straight onto the tap's `main`**.

**`create-branch: false` in `release.yml` is what makes that direct commit happen, and removing it re-breaks releases silently** — the tap's `main` is ruleset-protected, so left to default the action branches and then `POST /pulls` 403s *after* pushing a correct-looking branch, which is the v1.5.1/v1.6.0 failure mode where both formulas were right and neither reached the tap. When a release job fails, **check the tap for an orphaned `update-cockpit.rb-*` branch before re-cutting anything**. The formula's `resource` blocks are **not** touched — regenerate them by hand on a dependency bump.

### PyPI is the second tag consumer — trusted publishing

The same tag fires `publish.yml`, uploading as **`cmux-cockpit`** (bare `cockpit` collides with Red Hat's Cockpit; the import package and console script are unchanged). It runs *independently* of `release.yml`, so a green tap PR is not evidence PyPI succeeded — check both.

Auth is Trusted Publishing (OIDC): there is **no** PyPI token in this repo, in GitHub secrets, or in fnox. PyPI matches four claims — Owner `khivi`, Repository `cockpit`, Workflow `publish.yml`, Environment `pypi`. Earlier `invalid-publisher` failures were fixed browser-side, not by editing `publish.yml`; that fails *before* upload, so no version is consumed and `gh run rerun` recovers it. **Do not** touch `publish.yml` — check the pending-publisher claims first. See `docs/pypi-publishing.md`.

## Commit / PR-title convention

We squash-merge, so the **PR title** becomes the commit subject on `main`. Use [Conventional Commits](https://www.conventionalcommits.org/): `feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert`. Local WIP messages are unconstrained.

Enforced by `pr-title.yml` as the required `lint-pr-title` check. It runs alone so a title edit doesn't re-trigger pytest/mypy.

`main` is guarded by **two** stacked mechanisms, which is why a plain `gh pr merge` reports "the base branch policy prohibits the merge" even when green: classic **branch protection** requires the checks, and a **repository ruleset** additionally requires 1 approving review, code-owner review, resolved threads, and squash-only merges. A solo author can't approve their own PR, so every merge is `gh pr merge <N> --squash --admin`. Check the ruleset with `gh api repos/khivi/cockpit/rulesets`, not just `.../branches/main/protection`.

## Test layout

New modules get their own `test_<name>.py` — don't append tests for a new source file to an unrelated test module. Shell hooks under `cockpit/hooks/` are the exception: they live as `tests/test_<hook>.py` with no Python source mirror.

## Test style by layer

- **Leaf modules** (`cockpit/lib/*` wrapping `git`, `gh`, `cmux`, `subprocess.run`) test against the real tool on `tmp_path` — stubbing the command tests the stub.
- **Orchestrators** compose those leaves; tests mock collaborator calls to assert ordering and gating without re-validating the leaves.
- **CLI entry-points** test the argparse / routing layer, mocking at the orchestrator boundary.
- **TUI** (`tests/tui/*`) drive the app headlessly via `App.run_test()`/Pilot with the ticks and `load_config` injected. Test the TUI's own scheduling and gating, not the cycle underneath.
- **End-to-end** (`tests/e2e/*`) run against real binaries, no mocking — the slowest and most fragile, reserved for genuinely cross-layer behaviour.
- **Repo-wide invariant tests** assert a fact about the tree instead of prose nobody re-derives: `tests/e2e/test_cmux_surface.py` and `tests/test_comment_references.py` (every `backticked` symbol and path still resolves — in a comment or docstring, and in this file's own prose). A rename otherwise leaves names behind as claims that read fine and mean nothing, which is how github_done_on_merge survived in two docstrings as the live gate (deliberately unbackticked here — a backtick marks a name that resolves *now*). Both carry a small allowlist for genuinely external names, **not** a place to park a stale reference.

## Sync

AGENTS.md is canonical — `CLAUDE.md` imports it, `.github/copilot-instructions.md` symlinks to it; edit only this file.
