# Tickets — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

## Schema and providers

- [tickets.fields~1] `dev_done`, `merge_done` and `token_env` are one field each
  across every provider, never re-split per provider.
- [tickets.legacy~1] Superseded spellings hard-fail in preflight naming the
  replacement; no reader accepts an alias.
- [tickets.schema-composed~1] The allowed field set is composed from the active
  provider's `CONFIG_FIELDS`; another provider's field is rejected, and the per-
  provider schemas are never flattened into one dict.
- [tickets.provider-select~1] Provider selection reads the `tickets` block
  alone; no rule guesses the provider from a sibling field.
- [tickets.provider-fields~1] (untested: design rationale) Provider behavior
  hangs off `TicketProvider` fields, never provider-name ternaries.
- [tickets.no-preflight~1] No ticket provider has a connectivity pre-flight;
  each prompt carries its own retry-then-STOP step.
- [tickets.url~1] A Linear or Jira URL classifies into the same mode as the bare
  id with the identifier extracted, never passed through verbatim.

## Credentials

- [tickets.credential-envs~1] `credential_envs` names each provider's own
  variables — both Trello halves, none for GitHub — and stays in step with
  `credential_env_names`.
- [tickets.credential-warning~1] The unset-credential warning gates on a
  provider resolving and asks the provider for its variables — no provider-name
  ternary.

## Routing

- [tickets.routing-survivors~1] `route_ticket_repos` returns every candidate
  surviving both stages; nothing reads a single key off the first candidate.
- [tickets.jira~1] Jira free-matches the same `keys` field through the same
  reader and does not narrow — no `project` field, no duplicated matcher.
- [ticket-routing.no-waiver~1] Every started ticket passes the config check; no
  provider is waived from routing.
- [ticket-routing.explicit-repo~1] The resolved repo travels as an explicit
  `--repo`; there is no cursor-row or cwd default.

## devdone and the inbox

- [devdone.batch-fetch~1] Due ticket ids resolve as a union across a repo's PRs
  per provider batch, never a per-PR fetch fan-out.
- [ticket-inbox.axes~1] The inbox fetch groups by resolved credential while the
  payload keys by org; the two axes never collapse into one.
- [ticket-inbox.fetch~1] `fetch_my_open` returns `None` for "couldn't ask" and
  `[]` for "answered with nothing"; a suspended bucket keeps its payload, keyed
  on the failure, never on emptiness.
- [ticket-inbox.screen~1] Routing markers are computed by the app and handed to
  the screen; the screen never calls `find_repos_by_ticket_key` or
  `narrow_repos` per repaint.
- [ticket-inbox.markers~1] (untested: design rationale) The three-way routing
  answer is a one-cell marker in the handle's ellipsis budget, never a Repo
  column.

## Orgs

- [orgs.merge~1] `apply_org_defaults` merges one level deep: a repo scalar wins,
  a block unions per field into a fresh dict — never recursive, never aliased.
- [orgs.not-persisted~1] The merged config is never written back; the config
  writers re-read `config.json` from disk.
- [orgs.transparent~1] (untested: design rationale) Nothing below `load_config`
  knows orgs exist — no org-aware reader, `org_*` field, or resolution helper.
