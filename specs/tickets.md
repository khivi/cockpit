# Tickets — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

## Schema and providers

- [tickets.schema.one-field-per-concept~1] `dev_done`, `merge_done` and `token_env`
  are one field each across every provider, never re-split per provider.
- [tickets.schema.no-legacy-alias~1] Superseded spellings hard-fail in preflight
  naming the replacement; no reader accepts an alias.
- [tickets.schema.per-provider-not-flattened~1] The allowed field set is composed
  from the active provider's `CONFIG_FIELDS`; another provider's field is
  rejected, and the per-provider schemas are never flattened into one dict.
- [tickets.provider.no-sibling-field-guess~1] Provider selection reads the
  `tickets` block alone; no rule guesses the provider from a sibling field.
- [tickets.provider.no-name-ternaries~1] (untested: design rationale) Provider
  behavior hangs off `TicketProvider` fields, never provider-name ternaries.
- [tickets.no-preflight-any-provider~1] No ticket provider has a connectivity
  pre-flight; each prompt carries its own retry-then-STOP step.
- [tickets.url.extract-not-verbatim~1] A Linear or Jira URL classifies into the
  same mode as the bare id with the identifier extracted, never passed through
  verbatim.

## Credentials

- [tickets.credentials.envs-in-step-with-stripper~1] `credential_envs` names each
  provider's own variables — both Trello halves, none for GitHub — and stays in
  step with `credential_env_names`.
- [tickets.credentials.warning-no-ternary~1] The unset-credential warning gates on
  a provider resolving and asks the provider for its variables — no
  provider-name ternary.

## Routing

- [tickets.routing.narrow-reports-all-survivors~1] `route_ticket_repos` returns
  every candidate surviving both stages; nothing reads a single key off the
  first candidate.
- [tickets.jira.no-narrow-duplicate~1] Jira free-matches the same `keys` field
  through the same reader and does not narrow — no `project` field, no
  duplicated matcher.
- [ticket-routing.start-ticket.no-per-provider-waiver~1] Every started ticket
  passes the config check; no provider is waived from routing.
- [ticket-routing.start-ticket.no-cursor-row-default~1] The resolved repo travels
  as an explicit `--repo`; there is no cursor-row or cwd default.

## devdone and the inbox

- [devdone.fetch.no-per-pr-fanout~1] Due ticket ids resolve as a union across a
  repo's PRs per provider batch, never a per-PR fetch fan-out.
- [ticket-inbox.grouping.two-axes-not-one~1] The inbox fetch groups by resolved
  credential while the payload keys by org; the two axes never collapse into
  one.
- [ticket-inbox.fetch.empty-not-suspended~1] `fetch_my_open` returns `None` for
  "couldn't ask" and `[]` for "answered with nothing"; a suspended bucket keeps
  its payload, keyed on the failure, never on emptiness.
- [ticket-inbox.screen.no-narrow-repos-for-marker~1] Routing markers are computed
  by the app and handed to the screen; the screen never calls
  `find_repos_by_ticket_key` or `narrow_repos` per repaint.
- [ticket-inbox.markers.no-repo-column~1] (untested: design rationale) The
  three-way routing answer is a one-cell marker in the handle's ellipsis budget,
  never a Repo column.

## Orgs

- [orgs.merge.one-level-deep~1] `apply_org_defaults` merges one level deep: a repo
  scalar wins, a block unions per field into a fresh dict — never recursive,
  never aliased.
- [orgs.merge.never-persisted~1] The merged config is never written back; the
  config writers re-read `config.json` from disk.
- [orgs.defaults.no-org-aware-reader~1] (untested: design rationale) Nothing below
  `load_config` knows orgs exist — no org-aware reader, `org_*` field, or
  resolution helper.
