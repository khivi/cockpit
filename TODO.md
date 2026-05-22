# TODO

## spawn.py: Linear and Slack positional detection

`detect_source()` in `scripts/spawn.py` currently handles GitHub PR URLs, bare PR numbers, and branch names. Two planned extensions:

- **Linear ID** (`PE-1234`, case-insensitive `[A-Z]{2,}-\d+`): resolve via Linear GraphQL API to get title + description, derive branch `khivi/<id-lower>-<title-slug>`, generate plan-only prompt with ticket body. Requires `LINEAR_API_KEY` env var; fall back to branch mode if unset.
- **Slack URL** (`https://<workspace>.slack.com/archives/<channel>/p<ts>`): resolve thread via Slack API, derive branch from first-message slug, generate plan-only prompt with thread text. Requires Slack MCP or `SLACK_TOKEN`; fall back to branch mode if unavailable.

Both should follow the same detect → derive-branch → plan-only-prompt pattern already used for PR mode.

## Linear title in cship pill

Cockpit no longer renders the statusline itself — `use_cship: true` delegates to the `cship` binary. Any "Linear title in the statusline" work belongs in cship's repo, not here.

The data path cockpit could still own: enrich `~/.config/cockpit/cache/{repo}__pr-{N}.json` with `linear_id` / `linear_title` so cship (or any other consumer) reads them without its own Linear API call. Deferred until cship grows a hook for that.

## Memory-promotion candidates (assistant patterns)

Patterns staged for review and potential promotion to global rules (`~/.claude/CLAUDE.md` or `claude/rules/`) via the `/promote-memories` skill.

### `git show --stat` before full diff

**Why:** `git show <sha>` or `git diff main...HEAD` on a multi-file commit dumps the whole patch into context. Even with rtk's compaction, a 40-file diff burns tokens that targeted reads avoid. The stat output gives the file list + change size; from there `Read <path>` extracts only what you need.

**How to apply:** Before `git show <sha>` or `git diff <range>`, ask "do I need every hunk?" If you only need the file list or a couple files, use `git show --stat <sha>` (or `git diff --stat <range>`) then `Read`/`rg` on the specific paths.
