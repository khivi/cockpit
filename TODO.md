# TODO

## Linear title in footer pill

Currently `footer.py` extracts the Linear ID from the branch name (e.g. `PE-4081`) via regex — no API call, no cache. Good enough for parity with cship.

Next step: enrich the pill with the Linear **title** so it reads `PE-4081 "Ticket title here"` instead of bare `PE-4081`.

### Design

- Render path stays network-free. The daemon does the work, the footer reads cache.
- Reuse the existing per-PR cache (`~/.config/cockpit/cache/{repo}__pr-{N}.json`) — no new cache file.
- Each cycle, for every PR with a Linear ID in branch or PR body, fetch the Linear issue title and store it on the cached payload:

  ```json
  {
    "number": 27933,
    "title": "Fix login regression after token refresh",
    "branch": "khivi/PE-4081-fix-login",
    "linear_id": "PE-4081",
    "linear_title": "Token refresh races logout flow",
    ...
  }
  ```

- `footer._pr_segment` (or a new `_linear_pill`) reads `linear_id` + `linear_title` from the cache hit and renders `PE-4081 "Token refresh races logout flow"`.
- For untracked git repos (no cache hit), keep the branch-name regex fallback so the pill still shows the bare ID.

### Open questions

- **Linear auth.** cockpit currently has zero Linear dependencies. Options:
  - Linear GraphQL API + `LINEAR_API_KEY` env var (cleanest; opt-in via config.json).
  - `linear` / `lr` CLI if installed (avoid a hard dep).
  - Skip silently if neither is available — the ID-only pill still works.
- **Where to extract the ID.** Branch name covers most cases; some PRs put it only in the body. Probably: branch first, fall back to PR body via the existing `gh` query (add `body` to `_PR_FIELDS` in `lib/gh.py`).
- **TTL.** Linear titles rarely change. Once fetched, keep until the PR cache file is rewritten on PR close/cleanup. No per-cycle re-fetch needed if `linear_title` is already populated.

### Acceptance

- Branch `khivi/PE-4081-fix-login` with `LINEAR_API_KEY` set → footer shows `PE-4081 "<title>"`.
- Same branch without the env var → footer shows bare `PE-4081` (no regression).
- Branch with no Linear ID anywhere → no pill (no regression).
