# cmux surface audit

What cmux offers, what cockpit uses, and why the gap is the size it is.

First measured 2026-08-25 against **cmux 0.64.22 (102) [ddd4a01bc]**. The judgments
here are written down; the counts are not — `tests/e2e/test_cmux_surface.py` holds
those against whatever cmux is installed, and fails when this classification falls
behind it.

This is a **map, not a backlog**. An unused verb costs nothing: the daemon's cost is
what it calls, and "cmux can do it" has never been a reason to. The point of writing
it down is that nobody had ever compared the two surfaces deliberately, so there was
no way to tell which unused verb was the right answer to a problem already solved a
harder way.

## How to re-measure

The advertised CLI surface — **the `Commands:` section runs to the next
unindented header, not to the first blank line.** It contains four
blank-line-separated groups (main, `# tmux compatibility commands`, markdown,
browser), so a `sed -n '/^Commands:/,/^$/p'` range stops after the first group
and silently reports the main group alone. That mistake undercounted this audit
by 22 verbs on its first pass:

```bash
cmux --help | awk '/^Commands:/{f=1;next} /^[^ \t]/{f=0} f' \
  | rg -o '^ {2}([a-z][a-z0-9-]*)' -r '$1' | sort -u
```

One line per command, first token only. Deeper-indented continuation lines do
not exist in this help (checked: zero lines match `^ {3,}`), so the first token
of every 2-space line is a top-level verb.

The RPC surface, and the capability ids, come out of one JSON blob:

```bash
cmux capabilities | python3 -c 'import json,sys; d=json.load(sys.stdin); \
  print(len(d["capabilities"]), "capabilities /", len(d["methods"]), "methods")'
```

The cockpit side must be **AST-parsed, not grepped**. Several `cmux()` calls span
multiple lines, so a regex on `cmux("verb"` silently misses `new-workspace` and
others; and `lib/events.py` builds a raw `["cmux", "events", …]` argv for `Popen`
rather than going through the `cmux()` wrapper, so it is invisible to any pattern
anchored on the wrapper's name:

```bash
python3 - <<'PY'
import ast, pathlib, collections
verbs = collections.defaultdict(set)
for p in sorted(pathlib.Path("cockpit").rglob("*.py")):
    for n in ast.walk(ast.parse(p.read_text(), str(p))):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "cmux" \
           and n.args and isinstance(n.args[0], ast.Constant):
            verbs[n.args[0].value].add(f"{p}:{n.lineno}")
        elif isinstance(n, ast.List) and n.elts and getattr(n.elts[0], "value", None) == "cmux":
            verbs[n.elts[1].value].add(f"{p}:{n.lineno}")
for v in sorted(verbs):
    print(f"{v:18} {' '.join(sorted(verbs[v]))}")
PY
```

## The shape of it

cockpit uses **roughly one advertised verb in nine**, one RPC method of several
hundred, and one negotiated capability as an actual gate. One verb it depends on
(`workspace-group`) is not advertised at all.

Exact counts are deliberately **not** written down here. They are derived data,
they change with every cmux release, and a hand-maintained count in prose is
precisely what went stale on this document's first pass — it read only the first
group of the `Commands:` section, undercounted by 22 verbs, and repeated the
wrong figure into five other places before anyone noticed. For current numbers
run the command above, or:

```bash
pytest tests/e2e/test_cmux_surface.py -q     # skips silently without cmux
```

That module is the live half of this audit. It holds the bucket membership as
data, asserts every advertised verb is either invoked by cockpit or classified,
and fails by name when a cmux upgrade ships something new — so the classification
below cannot quietly come to describe an older cmux.

One measurement artifact worth knowing, since two readings of "how many verbs"
disagree: `capabilities.parse_verbs` returns a **superset** of the true
top-level set. It splits alternations, which is right for
`disable-browser | enable-browser | browser-status` (three real verbs) and wrong
for the `browser <subcommand>` lines, where `goto|navigate` and
`back|forward|reload` are subcommands. Over-collection can only ever cause a
**false pass** — the gate asks "is required verb X present", and no required
verb is a leaked token — so it is recorded, not fixed.

The RPC surface is what reframes this. The advertised CLI is not the real
surface — `cmux rpc <method> [json-params]` takes an arbitrary method name, and the
dispatcher behind it exposes several hundred of them. cockpit reaches into it exactly
twice, for one method. Whole families sit there unexamined — `browser.*` alone is a
full Playwright-shaped automation surface.

## What cockpit uses

Grouped by the tier each verb serves. Call sites are current as of this measurement.

**Inventory** — re-derived every cycle, never stored.

- `list-workspaces` — `cmux.py:434` `list_workspaces`, `:717` `workspace_names`, `:1015` `cmux_close_workspace_best_effort`
- `rpc workspace.list` — `cmux.py:770` `workspace_cwds`, `:971` `_note_self_close`

**Nudge** — the idle gate and the two delivery verbs.

- `list-status` — `cmux.py:511` `_claude_ready`, `:682` `nudge_if_idle`, `:804` `workspace_is_idle`
- `send` / `send-key` — `cmux.py:532-533` `deliver_followup`, `:696-697` `nudge_if_idle`, `spawn.py:1205-1206` `main`

**Pills and tint** — cosmetic, `_CMUX_ONLY_VERBS`, all best-effort.

- `set-status` — `cmux.py:148` `_set_status`, `cycle.py:1902` `_refresh_orphan`, `app.py:1440` `_set_loop_pill`
- `clear-status` — `cmux.py:152` `_clear_status`, `app.py:1451` `_set_loop_pill`
- `workspace-action` — `cmux.py:171` `set_workspace_color`, only ever `--action set-color`

**Sidebar folds** — stacks, reviews, snoozed. One verb, nine call sites, eight subverbs.

- `workspace-group` — `cmux.py` `list_workspace_groups`, `create_workspace_group`, `_set_group_icon`, `add_to_workspace_group`, `remove_from_workspace_group`, `rename_workspace_group`, `move_workspace_group_to_end`, `ungroup_workspaces`

**Workspace lifecycle** — spawn, rename, close, focus.

- `new-workspace` — `cmux.py:468`/`:484` `spawn_workspace`
- `rename-workspace` — `cmux.py:480` `spawn_workspace`, `:562` `rename_workspace_if_needed`
- `close-workspace` — `cmux.py:1014` `cmux_close_workspace_best_effort`
- `select-workspace` — `cmux.py:944` `select_workspace`

**Doorbell and self-probe** — the event stream, and this gate's own two calls.

- `events` — `events.py:123` `_stream_once`, built as a raw `Popen` argv
- `capabilities` (and `--help`) — `capabilities.py:131-133` `probe`

## Defects found

### 1. `workspace-group` is undocumented, and the verb gate is structurally blind to it

`cmux --help | grep -c workspace-group` → **0**. The verb does not appear in the
`Commands:` section at all, yet `cmux workspace-group --help` documents thirteen
subcommands, and it powers every sidebar fold cockpit builds.

`capabilities.py::parse_verbs` reads exactly that `Commands:` list, so cockpit's most
elaborate cmux dependency has no verb gate and cannot be given one — adding
`workspace-group` to `REQUIRED_VERBS` would report it permanently missing.

**Fixed on this branch, via the other axis.** The negotiated capability list *does*
advertise `workspace.groups.v1` (plus `group_actions`, `group_create`,
`create_in_group`), and `workspace.group.*` is its own family of RPC methods. So
`REQUIRED_CAPABILITIES` gains `workspace.groups.v1`. The umbrella id is the right one
to require: its absence means no folds at all, where the narrower three would add
warning noise without a distinct failure mode.

The folds themselves stay best-effort `check=False` — the gate buys a preflight
warning naming the tier, not a behaviour change.

### 2. `AGENTS.md` claimed a read that does not happen

The inventory invariant read *"Each cycle re-reads `git worktree list` and `cmux
tree`"*. Nothing in `cockpit/` invokes `cmux tree`. The real reads are `rpc
workspace.list` and `list-workspaces`.

**Fixed on this branch.** The invariant is correct; only the command name was wrong.

### 3. `terminal.replay.v1` was required for a feature that was never built

`REQUIRED_CAPABILITIES` mapped it to `"screen preview"` — the row-terminal-peek
feature dropped 2026-08-20, plan-only, no code ever written. A cmux lacking the
capability was told to upgrade so that a non-existent feature would work.

**Fixed on this branch:** dropped.

### 4. `notification.feed.v1` was required for a feature that does not exist either

Mapped to `"the notification feed"`. cockpit calls **no** notification verb —
`notify`, `list-notifications`, `jump-to-unread`, `mark-notification-read`,
`dismiss-notification`, `clear-notifications`, `open-notification` are all unused, and
none of the ten `notification.*` RPC methods is called.

**Fixed on this branch:** dropped.

### 5. The machine-readable verb list already landed — follow-up, not fixed here

`parse_verbs` carries a `ponytail:` note: *"parses help text because cmux ships no
machine-readable verb list; swap the body for `cmux verbs --json` if one ever lands."*

It landed. `cmux capabilities` returns a `methods` array — several hundred entries — in
the same JSON blob cockpit already fetches and already parses. `parse_capabilities` reads
`payload.get("capabilities")` and discards `methods` entirely.

A `parse_methods()` reader would retire that debt, gate `workspace-group` on
`workspace.group.create` directly rather than through the umbrella capability, and
give every future feature a precise gate. It is a real change with its own tests and
preflight wording, so it is deliberately **not** riding this documentation PR.

Two related observations worth recording while they are in view:

- `has_capability` has exactly **one** reader in the codebase (`events.py:75` →
  `events.v1`). The other `REQUIRED_CAPABILITIES` entries exist only to shape a
  preflight warning; none of them gates a code path.
- `REQUIRED_VERBS` covers 5 of the 15 verbs cockpit invokes. Four ungated ones are
  hard dependencies that *are* advertised and so could be gated —
  `close-workspace`, `select-workspace`, `rename-workspace`, `rpc`. The rest are
  either best-effort `_CMUX_ONLY_VERBS`, capability-gated (`events`), or
  self-gating (`capabilities`). Widening the list is cheap and was left out only to
  keep this change scoped.

## Unused verbs that could matter

Everything already probed is recorded here so nobody re-derives it. The rest of
the unused set is bucketed below.

| Verb | What it does | What cockpit could use it for | Blocker / cost |
|---|---|---|---|
| `diff` | Native diff viewer. Reads a patch on stdin, `--source unstaged\|staged\|branch\|last-turn`, `--layout split\|unified`. Renders in a browser split. | A real PR/branch diff view — syntax highlighting, dual line numbers, collapsed unmodified regions. Strictly better than a Textual overlay. | **Now used** — the TUI's `d` key pipes `gh pr diff` into it (`app._open_diff`), gated on `capabilities.diff_viewer_available()`. Needs `cmux enable-browser`; preflight warns when it is off. Split layout overprints at narrow width, so cockpit sends `--layout unified`. |
| `open` | Opens a URL or path in a cmux browser pane. | `p` could open the PR in-app instead of the system browser. cmux settings already carry `openPullRequestLinksInCmuxBrowser`. | Browser must be enabled. Changes `p`'s behaviour, so it wants a config opt-out. |
| `read-screen` | Reads a session's terminal, `--scrollback`, `--lines <n>`. | Peek at why a session stopped without focusing it. | **Now used** — `cmux.py::_screen_signals_idle`, the fast tick's fallback self-heal for a workspace reporting no `claude_code=` state at all (see the Nudge idle-gate section of `AGENTS.md`). **Probed working 2026-08-20**: returns real scrollback past one viewport from a full-screen TUI on the alternate screen, as plain text — zero ESC bytes across 40 lines. |
| `notify` | Native notification, `--title/--subtitle/--body`. | A passive signal that, unlike the nudge, **does not type into a session** — so no idle gate, no permission-prompt hazard. The one obvious hole in the current nudge design. | None known. Unprobed. |
| notification family (`list-notifications`, `mark-notification-read`, `dismiss-notification`, `open-notification`, `jump-to-unread`, `clear-notifications`) | Read and manage the cmux notification feed. | Surfacing cmux's own notifications in the TUI; `jump-to-unread` as a row-less "take me to what wants me" key. | cockpit currently requires `notification.feed.v1` while calling none of these (defect 4). Ten RPC methods behind them, all unexamined. |
| `right-sidebar` | `files\|find\|vault\|sessions\|feed\|dock` — native file browser and finder. | A file browser per worktree, free, instead of anything hand-built. | Cosmetic; changes the user's sidebar state, which cockpit does not otherwise own. |
| `sidebar-state` | Reads current sidebar state. | The one read that could tell cockpit what it just did to the sidebar — currently every group operation is write-only and reconciled against `workspace-group list`. | Unprobed. Would be the first sidebar *read* other than group list. |
| `reorder-workspace` | Moves a **single** workspace, `--index/--before/--after`. | Row-order control without a group. cockpit currently sinks rows only via `workspace-group move --to-index 9999`, which needs a group and an anchor workspace to exist. | Per-workspace, so ordering N rows is N subprocesses. |
| `reorder-workspaces` | Bulk reorder, `--order <ref>,<ref>,…`. | The bulk form of the above — one call for a whole repo's ordering, sidestepping the per-workspace cost. | Would fight the user's own drag-ordering: cockpit only ever parks a *group* it built, and has nowhere to record where a row used to sit. |
| `todo` | Per-workspace todo list: `add/list/check/uncheck/start/rm/clear`. | Surfacing a session's plan in the sidebar. | Writes UI state cockpit would then own and have to reconcile. |
| `set-progress` / `clear-progress` | Per-workspace progress bar, `0.0-1.0` + `--label`. | A visible long-operation indicator (spawn, fetch, teardown) on the affected row. | Trivial; nothing blocks it. |
| `log` / `list-log` / `clear-log` | Per-workspace log buffer, `--level`, `--source`. | Somewhere for `spawn.log` and per-cycle errors to land that is attached to the row they concern, instead of a file. | `list-log` is per-workspace, so reading N rows is N subprocesses. |
| `tree` / `top` / `memory` | Process tree, CPU/memory per workspace, memory grouping. | A resource column beside `$`; catching a runaway agent. | `top` supports `--all` and `--format tsv`, so unlike most of this list it is **one** call for every workspace. The cheapest unexplored thing here. |
| `browser` | 43 subcommands — navigate, click, fill, screenshot, snapshot, eval, network routing, cookies, storage, tracing. A Playwright-shaped automation surface, mirrored by ~100 `browser.*` RPC methods. | Nothing in cockpit's current scope. Listed because `diff` and `open` both render into a browser pane, so anything built on either inherits `enable-browser` as a prerequisite. | Needs the browser enabled. By far the largest single family, and entirely unexamined. |
| `markdown` | `markdown [open] <path>` — formatted viewer panel with live reload. | Rendering a PR body, a plan file, or `AGENTS.md` in-app rather than in the pager. | Its own panel, so it competes with the TUI for screen rather than composing with it. Unprobed. |

**The standing cost argument.** `cmux list-status` has **no bulk mode** — `--help`
accepts `--workspace`/`--window` only, and a bare invocation returns a single
workspace. Any per-row cmux state therefore costs one subprocess per row per refresh.
That is what makes most of this table expensive on a tick, and why `top --all` stands
out.

## The rest, bucketed

Five buckets, membership in `tests/e2e/test_cmux_surface.py::UNUSED_VERBS`.
Nothing here is a gap; each is a family cockpit has no business in.

**tmux compatibility.** `bind-key`, `unbind-key`, `capture-pane`,
`break-pane`, `join-pane`, `swap-pane`, `resize-pane`, `respawn-pane`,
`pipe-pane`, `last-pane`, `next-window`, `find-window`, `display-message`,
`list-buffers`, `set-buffer`, `paste-buffer`, `set-hook`, `popup`,
`clear-history`, `wait-for`. cmux ships these under its own
`# tmux compatibility commands` heading so tmux muscle memory and existing tmux
scripts keep working. cockpit is not a tmux script and addresses workspaces, not
panes — but note `capture-pane` takes `--scrollback --lines <n>`, making it a
near-duplicate of `read-screen`, and `wait-for` is a real synchronisation
primitive if anything ever needs to block on a session reaching a state.

**Layout.** `new-pane`, `new-split`, `new-surface`, `split-off`,
`move-surface`, `focus-pane`, `list-panes`, `tab-action`, `rename-tab`, the window
verbs, `send-panel`/`send-key-panel`, and the rest of the pane/surface/tab/window
tier. cockpit is workspace-granular by design: a worktree maps to a workspace, and
every cell, pill, and row key is keyed that way. A pane-aware cockpit is a different
product, not a missing feature — and the `send-panel` pair in particular would
duplicate `send`/`send-key` at a granularity nothing else in the codebase models.

**Remote and infra.** `ssh`, `mosh`, `ssh-tmux`, `mosh-tmux`, the
`ssh-session-*` trio, `remote-daemon-status`, `remotes`, `vm`/`cloud`,
`ai-accounts`, `auth`/`login`, `ping`, `iroh-diag`. Not applicable: cockpit manages
local git worktrees on the machine the daemon runs on. Remote worktrees would be a
new product decision, not an unused capability.

**Agent launchers and session lifecycle.** `claude-teams`,
`codex-teams`, `omo`/`omx`/`omc`, `hooks`, `agent-hibernation`, `restore`,
`restore-session`, `feed`. cockpit spawns agents through `new-workspace --command`
with its own seeded prompt, which is the seam the whole `prompts/` template layer
hangs off; the launcher verbs would replace that seam rather than extend it.
`hooks` overlaps cockpit's own `~/.claude` hook install (`config.install_claude_hooks`)
and is the one here worth a second look if the idle-pill mechanism ever needs
rebuilding.

**App chrome and dev-only.** `welcome`, `docs`, `settings`, `shortcuts`,
`themes`, `config`, `reload-config`, `version`, `feedback`, `help`, `debug-terminals`,
`set-app-focus`, `simulate-app-active`, `simulate-sidebar-drag`, `simulator`, `ios`,
`disable-browser`. User-facing app surface or cmux's own test hooks. Nothing here is
a cockpit feature. (`disable-browser`/`enable-browser` is the one exception in
passing — `diff` and `open` both need the browser enabled, so a feature built on
either has to say so.)
