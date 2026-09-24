# Tickets — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

## Schema and providers

- [tickets.fields~1] `dev_done` and `merge_done` validate on Linear, Jira and
  Trello alike — one field per concept, never re-split per provider — while
  GitHub accepts `dev_done` and rejects `merge_done`, since it closes an issue
  on merge rather than moving it.
- [tickets.legacy~1] Given a superseded per-provider spelling in the tickets
  block, preflight exits 2 naming both the legacy field and its replacement —
  never silently ignoring it.
- [tickets.schema-composed~1] `token_env` validates for Linear, `key_env` for
  Trello and not for Jira — the allowed field set is composed from the active
  provider's declarations, never one merged dict.
- [tickets.provider-select~1] Given a block declaring only `keys`, no provider
  resolves — Jira declares the same field, so the block must name its
  provider, and nothing guesses one from a sibling field.
- [tickets.provider-fields~1] (untested: design rationale) Provider behavior
  hangs off `TicketProvider` fields, never provider-name ternaries.
- [tickets.no-preflight~1] A ticket spawn makes no `claude mcp list`
  subprocess call, and the availability-probe helper no longer exists — each
  provider prompt carries its own retry-then-STOP step instead.
- [tickets.url~1] Given a Linear issue URL pasted from the clipboard,
  `detect_source` classifies it into the same mode as the bare id with the
  identifier extracted — the worktree branch derives from the id, never the
  URL.

## Credentials

- [tickets.credential-envs~1] Every variable a provider's `credential_envs`
  declares is in the spawn stripper's `credential_env_names` set — a mismatch
  either warns about a name nothing holds or leaks a key into a session
  running over an untrusted diff.
- [tickets.credential-warning~1] Given a GitHub-provider repo with no
  credential env, preflight prints nothing — the provider declares its own
  (empty) variable set; there is no provider-name ternary in the warning.

## Routing

- [tickets.routing-survivors~1] Given two repos declaring the same keys and
  no project tiebreak, the route returns both names — survivors reach the
  caller's picker rather than collapsing into a bare "unroutable".
- [tickets.jira~1] `narrow_repos` is a passthrough for GitHub and Jira —
  Jira's project IS the key prefix the free match already used, so there is
  no paid second stage and no duplicated matcher.
- [ticket-routing.no-waiver~1] Given an ambiguous Trello card — a short link
  carrying no key, two repos declaring boards — starting it never reaches the
  spawn; no provider is waived from routing.
- [ticket-routing.explicit-repo~1] Given a ticket no candidate claims, the
  start refuses loudly and launches nothing — it never falls through to the
  daemon's own cwd.

## devdone and the inbox

- [devdone.batch-fetch~1] Given due tickets spread across several PRs, one
  batched fetch serves the union and each PR's block is assembled from the
  shared result — never a fetch per PR.
- [ticket-inbox.axes~1] Given one org bucket spanning two credential env
  names, the fetch runs once per credential while the payload still merges
  into the one org bucket — the two axes never collapse.
- [ticket-inbox.fetch~1] Given a provider answering `[]`, the bucket is
  written empty — asked-and-nothing-assigned is a real state — while a failed
  fetch (`None`) suspends only its own bucket, which keeps the payload it
  had.
- [ticket-inbox.screen~1] `tickets_screen.py` never references
  `narrow_repos` — routing markers are computed by the app and handed in, so
  opening the modal reaches no network however many tickets it holds.
- [ticket-inbox.markers~1] (untested: design rationale) The three-way routing
  answer is a one-cell marker in the handle's ellipsis budget, never a Repo
  column.

## Orgs

- [orgs.merge~1] Given an org block and a repo block both carrying the same
  nested dict field, the merge unions one level and does not descend — the
  repo's inner dict wins whole.
- [orgs.not-persisted~1] Given an org-inherited value visible through
  `load_config`, the value never lands back in `config.json` on disk — the
  merge is in-memory only.
- [orgs.transparent~1] (untested: design rationale) Nothing below
  `load_config` knows orgs exist — no org-aware reader, `org_*` field, or
  resolution helper.
