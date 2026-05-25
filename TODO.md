# TODO

## Linear title in cship pill

Cockpit no longer renders the statusline itself — `use_cship: true` delegates to the `cship` binary. Any "Linear title in the statusline" work belongs in cship's repo, not here.

The data path cockpit could still own: enrich `~/.config/cockpit/cache/{repo}__pr-{N}.json` with `linear_id` / `linear_title` so cship (or any other consumer) reads them without its own Linear API call. Deferred until cship grows a hook for that.
