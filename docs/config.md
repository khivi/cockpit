# Config reference

Cockpit reads `~/.config/cockpit/config.json` (override the dir with `$COCKPIT_HOME`).
A fresh install seeds a minimal `{"repos": []}` — **not** `config.example.json`, which
is documentation only (its placeholder paths would error every tick). Repos are appended
by `cockpit new` / `registry.register_cwd`.

The config is read **once per process** (`config.py::load_config`) — edits are picked up
on the next daemon start, not mid-run.

**How settings are set.** Every field below is set by editing `config.json` directly —
`cockpit setup` is not a config wizard. The **one** setting it prompts for is `use_cship`
(the footer statusline), and only because it also installs the required cship/starship
binaries; everything else is a plain JSON edit with a sane default. A mistyped value is
caught at daemon start by `preflight` (hard-fail with the valid set listed), so you don't
need a wizard to avoid silent misconfiguration.

This file is the human reference; the authoritative default/resolution for each field is
its reader function in `cockpit/lib/config.py`. Keep all three in sync when a field
changes: `config.py` (reader), `config.example.json` (sample), this file.

## Per-repo fields (`repos[]`)

Each entry in the `repos` array. Ticket fields live in the nested `tickets` object
(next section); everything else is a direct key on the repo entry.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | — | Repo label (used in `find_repo_by_name`, the `git-repo` cell). |
| `path` | string | — | Absolute path to the repo root (main worktree, or the bare dir). |
| `branch_prefix` | string | `""` | Stripped from branch labels (e.g. `khivi/`). Empty for off-GitHub / in-place repos. |
| `default_base` | string | `"main"` | Base branch PRs target; drives base-distance + the `origin/{base}` startup warning. |
| `sidebar_color` | string | unset | cmux sidebar tint + TUI row tint (one of `colors.CMUX_COLOR_ANSI`). Validated at preflight. |
| `review_prs` | bool | `false` | Auto-spawn a review worktree for each coworker's open PR (collaborators only; see `review_external`). |
| `skills` | object | `{}` | Slash-command overrides (below). No-op for `review` unless `review_prs`. |
| `review_external` | bool | `false` | Also auto-spawn review worktrees for non-collaborator (fork) PRs. Off by default — untrusted content reaching a Bash-capable agent is a prompt-injection risk. |
| `dependabot` | bool | `false` | Include Dependabot PRs in `review_prs` auto-spawn. Excluded by default. |
| `use_worktree` | bool | `true` | When `false`, the user works directly in the main checkout and cockpit never spawns PR/review/orphan worktrees for the repo (and `n` on its row creates a single named workspace on the checkout, no worktree). Absent = `true` = normal worktree-managed repo. Set to `false` by bare `cockpit new`. |
| `orphan_nudge_grace_hours` | number | `4` | Grace before a no-PR ("orphan") worktree draws the push-or-close nudge. `0` disables. Also a top-level default. |
| `tickets` | object\|string | `{}` | Ticket-provider block (below). Bare string `"github"` == `{"provider": "github"}`. |
| `org` | string | unset | Name of an entry in the top-level `orgs` object whose defaults this repo inherits (below). Must be defined there — a dangling reference hard-fails at start. |

Legacy flat keys still honored as fallbacks (superseded by the `tickets` block):
`linear_keys`, `linear_dev_done_state`, `linear_merge_done_state`, `linear_done_on_merge`.

## `orgs` block

A named bundle of **per-repo defaults**, for when you watch several small repos
belonging to the same organisation instead of one big one. Any per-repo field
(above) may appear in an org block except `name`, `path`, and `org` itself —
those identify one repo, so preflight rejects them there.

```json
{
  "repos": [
    { "name": "svc-auth",    "path": "~/src/acme/svc-auth",    "org": "acme" },
    { "name": "svc-billing", "path": "~/src/acme/svc-billing", "org": "acme" },
    { "name": "svc-web",     "path": "~/src/acme/svc-web",     "org": "acme",
      "sidebar_color": "Cyan" }
  ],
  "orgs": {
    "acme": {
      "sidebar_color": "Magenta",
      "branch_prefix": "khivi/",
      "use_worktree": false,
      "tickets": { "provider": "github" }
    }
  }
}
```

Resolution is **repo → org → global → default**: the org fills only keys the repo
itself doesn't set, so `svc-web` above keeps Cyan while its siblings take Magenta.

Block-valued keys (`tickets`, `skills`) merge **one level deep**, per field, with
the repo's value winning — the same granularity a repo already has over the
global block. So a repo can set just `tickets.project` and keep the org's
`provider` / `keys` / `api_key_env`, which is the point: `project` is the routing
discriminator meant to differ *within* an org. Everything else (scalars, lists)
is taken whole from whichever side sets it, repo first.

Two effects, both falling out of that merge (`config.py::apply_org_defaults`,
applied at load):

- **One colour per org.** `sidebar_color` drives both the cmux sidebar tint and
  the TUI row tint, so an org's repos read as one block.
- **One `use_worktree: false` per org**, not one per repo — the usual shape when
  the org is many small repos you work in-place and only occasionally pull.

The TUI additionally renders an org's repos **adjacent**
(`config.py::repos_grouped_by_org`), each org sitting where its first member
appears in `config.json`; repos with no org keep their config position. Ordering
only — parking (`h`) is still per repo.

## `tickets` block

Fields resolve **per-field** repo-block → global-block → default (`config.py::_tickets_field`),
so a global `tickets.close_on_merge` applies to a repo whose block omits it. Each provider
owns its accepted-field schema (`linear.py` / `github_issues.py` / `jira.py` / `trello.py`
export `CONFIG_FIELDS`); preflight rejects a field belonging to another provider.

| Field | Providers | Default | Meaning |
|---|---|---|---|
| `provider` | all | `none` | `none` \| `linear` \| `github` \| `jira` \| `trello`. |
| `close_on_merge` | all | `false` | Daemon transitions the delivered ticket to its terminal state on PR merge (opt-in — makes the daemon a tracker *writer*). |
| `keys` | linear, jira | `[]` | Identifier prefixes (Linear team `["PE"]`, Jira project `["PROJ"]`) — routes a `PE-1234` / `PROJ-123` spawn to this repo, and gates the Linear reads/writes. |
| `project` | linear | unset | Linear project this repo's work lives in. Tiebreaker when several repos share a team and so share `keys`: `PE-1234` then matches them all, and cockpit resolves the ticket's project (one fetch, made **only** on that ambiguity) to pick one. Matched by name, case-insensitively. (Jira has no equivalent — a Jira *project key* is `keys`, i.e. the team analogue.) |
| `dev_done_state` | linear | `Dev Done` | Linear state that lights the `devdone=` pill. |
| `merge_done_state` | linear | `Done` | Linear state a delivered ticket moves to on merge (if `close_on_merge`). |
| `dev_done_label` | github | `ready for review` | Issue label that lights the `devdone=` pill. |
| `start_label` | github | unset | Label applied when spawning a worktree on a GitHub issue (the one spawn-time write). |
| `site_url` | jira | `""` | Jira Cloud base URL. Empty → provider makes no REST call. |
| `email` | jira | `""` | Jira account email (paired with `$JIRA_API_TOKEN` for Basic auth). |
| `dev_done_status` | jira | `Dev Done` | Jira status that lights the `devdone=` pill. |
| `merge_done_status` | jira | `Done` | Jira status a delivered issue transitions to on merge. |
| `board` | trello | unset | Trello board this repo's cards live on — routes a `cockpit new https://trello.com/c/<id>` spawn. A card short link carries no board, so this is the *whole* route (not a tiebreaker): unset on every repo → zero fetches and no routing; declared on several → one fetch resolves the card's board. Matched by name, case-insensitively. |
| `dev_done_list` | trello | `""` (off) | Trello list (column) that lights the `devdone=` pill. No default — boards name lists arbitrarily. |
| `merge_done_list` | trello | `""` (off) | Trello list a delivered card moves to on merge. No default. |
| `api_key_env` | linear | `LINEAR_API_KEY` | **Name** of the env var holding the Linear API key. |
| `token_env` | jira, trello | `JIRA_API_TOKEN` / `TRELLO_API_TOKEN` | **Name** of the env var holding the Jira API token / Trello API token (the provider decides which). |
| `key_env` | trello | `TRELLO_API_KEY` | **Name** of the env var holding the Trello API key. |

Secrets are **env-only**, never config: `LINEAR_API_KEY`, `JIRA_API_TOKEN`,
`TRELLO_API_KEY`, `TRELLO_API_TOKEN`. The `*_env` fields above store an env var
*name*, never a value — which is how two orgs on separate Linear workspaces (or
Trello accounts) each get their own credential: set `api_key_env` once in the
`orgs` block and every member repo inherits it through the same per-field chain.

```json
"orgs": {
  "acme": {"tickets": {"provider": "linear", "keys": ["ACME"],
                       "api_key_env": "LINEAR_API_KEY_ACME"}}
}
```

Credentials are **daemon-only**: `cockpit new` processes the daemon spawns get an
environment with every configured credential var stripped. A spawned agent reads
its tracker through the Linear/Jira/Trello MCP connector, not the REST key.

## `skills` block

Slash commands seeded as a spawned workspace's first turn. Fields resolve
**per-field** repo-block → global-block → default (`config.py::_skills_field`).

| Field | Default | Meaning |
|---|---|---|
| `session` | unset (no-op) | Slash command run as its own first turn in **every** spawn (e.g. `/session-coordination`). Global only — a per-repo `skills.session` is accepted but every spawn resolves the same global value in practice, since there's no per-repo caller. |
| `review` | `/review` | Slash command seeded as the first turn of an auto-spawned `review_prs` worktree. No-op unless `review_prs`. Override per-repo (e.g. `/pr-review`) or globally. |
| `plan` | unset (built-in plan-only prose) | Slash command seeded as the first turn of a plan-only spawn (a PR/branch worth studying before implementing), followed by the source/PR context and the shared no-code/wait-for-approval gate. Unset keeps cockpit's built-in `plan_only.txt` prose. Override per-repo (e.g. `/plan-pr`) or globally. |
| `actions` | unset (built-in Actions prose) | Slash command seeded as the first turn of a GitHub-Actions-run-URL spawn, followed by the run/PR context (run URL, job id, linked PR) and the same no-code gate. Unset keeps cockpit's built-in `--log-failed` investigation prose. Override per-repo (e.g. `/actions-pr`) or globally. |

## Top-level fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `repos` | array | `[]` | Watched repos (above). |
| `orgs` | object | `{}` | Named bundles of per-repo defaults, inherited by repos naming them in `org` (above). |
| `slow_poll_interval_seconds` | number | `300` | Full reconcile cadence (gh fetch, PR JSON, pills). |
| `fast_poll_interval_seconds` | number | `30` | Network-free republish cadence (git-state + PR flat cells from disk). |
| `autoclose_age_days` | number | `14` | Age past which an abandoned worktree is autoclosed. |
| `orphan_nudge_grace_hours` | number | `4` | Default orphan-nudge grace (per-repo key overrides). |
| `linear_state_ttl_seconds` | number | `3 × slow` (900) | Backstop staleness for the cached Linear delivery block. |
| `linear_identity_ttl_seconds` | number | `12 × slow` (3600) | Cache lifetime for Linear viewer id + team state maps. |
| `skills` | object | `{}` | Slash-command overrides (above). |
| `use_cship` | bool | `false` | Install/point the statusLine at cship; seed `cship.toml`/`starship.toml` (via `cockpit setup` only). |
| `use_slack` | bool | `false` | Enable the Slack-MCP fetch+rename prompt for Slack-thread spawn sources. |
| `tool` | string | `auto` | Workspace backend: `auto` \| `cmux` \| `limux` \| `none`. |
| `theme` | string | `dark` | `dark` \| `light` — tunes cmux pills + the cship/starship footer palette. |
| `tui_theme` | string | `textual-dark` | Textual theme for the `cockpit watch` TUI chrome only. Persisted from the TUI theme picker. |
| `statusline_hide` | list | `[]` | Statusline fields to hide from the footer. Any of: `model`, `context`, `rate-limit`, `repo`, `branch-identity`, `worktree-status`, `permission-mode`, `cost`, `session-time`, `ticket`, `pr-state`, `pr-num`, `pr-comments`, `pr-checks`, `pr-title`, `pr-muted`. |

Global `tickets`, `skills`, and the legacy flat `linear_*` keys may also appear
at top level as defaults inherited by repos that omit them.
