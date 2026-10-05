# Tickets — behavior spec

## Schema and providers

- [tickets.fields~1] `dev_done` and `merge_done` validate on Linear, Jira and
  Trello alike. Each concept has one field, never one per provider. GitHub
  accepts `dev_done` and rejects `merge_done`.
- [tickets.legacy~1] Given a superseded per-provider spelling in the tickets
  block, preflight exits 2. The error names the legacy field and its
  replacement. Preflight never ignores the spelling silently.
- [tickets.schema-composed~1] `token_env` validates for Linear. `key_env`
  validates for Trello and not for Jira. The allowed field set comes from the
  active provider's declarations, never from one merged dict.
- [tickets.provider-select~1] Given a block that declares only `keys`, no
  provider resolves. Nothing guesses a provider from a sibling field.
- [tickets.provider-fields~1] (untested: design rationale) Provider behavior
  hangs off `TicketProvider` fields, never provider-name ternaries.
- [tickets.no-preflight~1] A ticket spawn makes no `claude mcp list`
  subprocess call. The availability-probe helper does not exist. Each provider
  prompt carries its own retry-then-STOP step.
- [tickets.url~1] Given a Linear issue URL from the clipboard, `detect_source`
  classifies it into the same mode as the bare id. It extracts the identifier.
  The worktree branch derives from the id, never the URL.

## Credentials

- [tickets.credential-envs~1] Every variable that a provider's
  `credential_envs` declares is in the spawn stripper's `credential_env_names`
  set.
- [tickets.credential-warning~1] Given a GitHub-provider repo with no
  credential env, preflight prints nothing. The provider declares its own empty
  variable set. The warning has no provider-name ternary.
- [tickets.config-verb~1] `cockpit config tickets` names a credential's
  variable and its set/unset state. It never prints the value.

## Inspection

- [tickets.config-cwd~1] Given no repo named, `cockpit config tickets` answers
  for the repo that owns the cwd. Given a cwd outside every configured repo, it
  exits 2. It never reports an unconfigured tracker for that cwd.

## Routing

- [tickets.routing-survivors~1] Given two repos that declare the same keys and
  no project tiebreak, the route returns both names. The survivors reach the
  caller's picker and never collapse into a bare "unroutable".
- [tickets.jira~1] `narrow_repos` is a passthrough for GitHub and Jira. Jira
  has no paid second stage and no duplicated matcher.
- [ticket-routing.no-waiver~1] Given an ambiguous Trello card (a short link
  with no key, two repos that declare boards), starting it never reaches the
  spawn. Routing waives no provider.
- [ticket-routing.explicit-repo~1] Given a ticket that no candidate claims, the
  start refuses loudly and launches nothing. It never falls through to the
  daemon's own cwd.

## devdone and the inbox

- [devdone.batch-fetch~1] Given due tickets across several PRs, one batched
  fetch serves the union. Each PR's block is assembled from the shared result.
  There is never a fetch per PR.
- [ticket-inbox.axes~1] Given one org bucket that spans two credential env
  names, the fetch runs once per credential. The payload still merges into the
  one org bucket.
- [ticket-inbox.fetch~1] Given a provider that answers `[]`, the bucket is
  written empty. Given a failed fetch (`None`), only that fetch's bucket is
  suspended. A suspended bucket keeps the payload it had.
- [ticket-inbox.screen~1] Opening the inbox reaches no network, however many
  tickets it holds. The app computes the routing markers and hands them in.
  Asserted structurally: `tickets_screen.py` never references `narrow_repos`.
- [ticket-inbox.markers~1] (untested: design rationale) The three-way routing
  answer is a one-cell marker in the handle's ellipsis budget, never a Repo
  column.

## The per-org check

- [ticket-check.bucket~1] The check resolves a bucket to the repos whose org
  matches it, in config order. A repo that declares no org matches by its own
  name.
- [ticket-check.no-provider~1] A repo that tracks no tickets reaches no
  tracker. The check reports it as contributing none.
- [ticket-check.credentials-by-name~1] The check reports a credential as set or
  unset by env var name. The value never reaches the report.
- [ticket-check.connection-first~1] Given a credential that cannot
  authenticate, the check reports the connection failed. It claims no scope
  verdict.
- [ticket-check.scope-names~1] Given a declared scope that the tracker does not
  recognise, the check names that scope.
- [ticket-check.couldnt-ask~1] The check reports a scope it could not ask about
  as unchecked, never as fine.
- [ticket-check.mcp-probe~1] The check probes a declared MCP server through
  `claude mcp list` and reports its health. It does not probe a repo that
  declares none. Repos in one bucket at one cwd share one probe.
- [ticket-check.mcp-probe-never-gates~1] The probe gates nothing. "The listing
  did not name it" is reported as a probable miss. "The probe could not be
  read" is reported as unchecked. The two never merge.
- [ticket-check.read-only~1] The check writes nothing: no cache cell, no
  config, no tracker mutation.

## Orgs

- [orgs.merge~1] Given an org block and a repo block that carry the same nested
  dict field, the merge unions one level and does not descend. The repo's inner
  dict wins whole.
- [orgs.not-persisted~1] Given an org-inherited value that `load_config` shows,
  the value never lands back in `config.json` on disk.
- [tickets.mcp-server-org~1] An MCP server name declared on an org block
  resolves for every member repo through the ordinary per-field merge.
- [orgs.transparent~1] (untested: design rationale) Nothing below
  `load_config` knows orgs exist: no org-aware reader, `org_*` field, or
  resolution helper.
