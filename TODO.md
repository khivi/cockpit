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

### Prefer `rg` over `grep`

**Why:** On macOS the system `grep` is BSD, which silently lacks `--type` and other GNU flags. Falling back to `/usr/bin/grep -rn` works but is ~10× slower than `rg` and doesn't respect `.gitignore`. Burning a turn discovering the `--type` flag is missing is wasteful.

**How to apply:** Default to `rg <pattern> [<path>]` for any text search. `rg --type py "<pattern>"` for language-filtered. Reach for `grep` only for stdin pipes where rg isn't available.

### `git show --stat` before full diff

**Why:** `git show <sha>` or `git diff main...HEAD` on a multi-file commit dumps the whole patch into context. Even with rtk's compaction, a 40-file diff burns tokens that targeted reads avoid. The stat output gives the file list + change size; from there `Read <path>` extracts only what you need.

**How to apply:** Before `git show <sha>` or `git diff <range>`, ask "do I need every hunk?" If you only need the file list or a couple files, use `git show --stat <sha>` (or `git diff --stat <range>`) then `Read`/`rg` on the specific paths.

## Worktree-gone-but-branch-survives gap

**Symptom:** `/cockpit:new todo --repo Cockpit` fails with `fatal: a branch named 'khivi/todo' already exists`, but `git worktree list` shows no worktree on that branch. Branch carries unpushed commits — in this case `f395357 docs(todo): …` and `979e571 chore: bump version to 0.20.4` — that are not in `origin/main`.

**Why the worktree disappeared:** `_maybe_autoclose` (`scripts/cockpit.py:195`) has two guards that should have protected this case:

- Line 227: `wt.dirty_count > 0` → `autoclose skipped (uncommitted)`.
- Line 234: `count_commits_since(wt.path, merged_head) > 0` → `autoclose skipped (N commits after merge)`.

With 2 commits past the merge head, autoclose would have refused. So the teardown came from one of:

1. `/cockpit:close --force` — `forced=True` in `TeardownRequest` skips `probe_blockers` entirely (`lib/teardown.py:74`). Worktree is removed even with unpushed commits.
2. Manual `git worktree remove` — bypasses cockpit completely.

Either way, `lib/teardown.teardown` only calls `remove_worktree` + `delete_pr_caches_for_branch`. **It never deletes the branch** — by design (`teardown.py:87-99`), so commits survive as a dangling branch.

**Why this is painful:** `create_worktree`'s local/remote existence checks (`scripts/lib/git.py:309`) query the un-prefixed input ("todo"), so a prefixed branch (`khivi/todo`) left behind by a prior teardown is invisible to them. The fallback then runs `git worktree add -b khivi/todo …`, which errors because `khivi/todo` already exists.

### Resolution

- **Option A — implemented.** `create_worktree` now checks the prefixed name before the `-b` fallback and attaches when it finds a match (`scripts/lib/git.py:313-321`). `/cockpit:new todo` re-enters the workspace seamlessly.
- **Option B — implemented via hard-refuse.** `/cockpit:close --force` no longer overrides dirty or unpushed-to-origin/main; the dangling-branch scenario can no longer be produced from cockpit's own tooling. Manual `git worktree remove` can still strand a branch, in which case Option A handles re-entry.
