# TODO

_Nothing queued._

## Done

- **Ticket title in PR cache** — the delivery block (`payload["ticket"]`, renamed
  from `linear`) now carries a provider-neutral `title` per ticket
  (`provider.fetch_titles`, Linear/Jira/Trello/GitHub), so cship (or any
  consumer) reads the ticket name from `~/.config/cockpit/cache/{repo}__pr-{N}.json`
  without its own API call. Rendering the title in the statusline is cship's job.
