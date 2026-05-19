# Plan: fix `/cockpit:new --name <slug>` (draft, pre-impl)

## Goal

`/cockpit:new --name cship --repo Cockpit` always creates a *new* branch
`<branch_prefix><short>` (e.g. `khivi/cship`) from `default_base`. Never
fetches, never attaches to an existing branch with that name.

`--name` semantics: user gives the name, cockpit creates a new branch.
Attaching to existing branches is the job of positional / `--branch` / `--pr`.

## Bug repro

```text
/cockpit:new --name cship --repo Cockpit
ERROR: git -C .../Cockpit fetch origin cship:cship failed:
       fatal: couldn't find remote ref cship
```

Root cause: `spawn.py:280` auto-promotes `--name cship` to `branch = "cship"`,
losing the fact that the user said `--name`. Downstream `create_worktree`
treats `"cship"` like any other branch input: checks local, then calls
`_fetch_remote_branch` whose `git ls-remote --heads origin cship` does
**suffix matching** on refnames — falsely matches some `*/cship` ref and
returns success. The follow-up `git fetch origin cship:cship` then fails
because fetch requires an exact ref.

## Approach

Plumb a `from_name: bool` flag from the `--name` auto-promotion site through
`resolve_worktree` and into a new-branch-only worktree path. When set:

1. Apply `branch_prefix` upfront (if set and no `/` in short).
2. Skip `_has_local_branch` / `_fetch_remote_branch` entirely.
3. Bump suffix (`-2`, `-3`, ...) until the prefixed branch is free locally
   AND remotely AND has no worktree. Mirrors the existing `collision_free`
   helper for worktree paths.
4. `git worktree add -b <prefixed[-N]> <wt_path> origin/<base>` — no fetch
   dance, no ls-remote pattern footgun.
5. Print a `note:` line if the suffix was bumped, so the user sees the
   actual branch name.

## Files to touch

- `scripts/spawn.py` — set `from_name=True` when `--name` auto-promotes;
  thread through `resolve_worktree`.
- `scripts/spawn.py:resolve_worktree` — new-branch-only path on `from_name`.
- `scripts/lib/git.py` — new helper `create_new_branch_worktree()` (or
  `new_only=True` on `create_worktree`) that skips PR/local/remote
  resolution.
- `scripts/lib/git.py:_fetch_remote_branch` — defensive: tighten ls-remote
  to exact `refs/heads/{branch}` pattern so suffix-matching can never
  falsely claim a remote exists. Benefits `--branch` callers too.
- Tests: TBD pending discovery of existing test scaffolding.

## Risks

- Collision bump renames silently. Mitigated by `note:` print.
- `worktree add -b ... origin/<base>` requires `origin/<base>` fetched —
  already covered by the existing `_git(repo, "fetch", "origin", base)`.

## Deferred (follow-up issue)

**Bug B — autoclose nukes a fresh worktree on a reused branch name.**
`_maybe_autoclose` (cockpit.py:162) keys on `wt.branch in merged_branches`
without a "freshly spawned" guard. The recent `c5790bf` added a
reused-branch guard to orphan-spawn (cockpit.py:466) but not to autoclose.

With the `--name` fix in place, the user's specific repro is dodged
(collision bump produces `khivi/cship-2` which is not in `merged_branches`).
The deeper bug still triggers via explicit `--branch khivi/cship` reuse;
worth a separate PR.

## Open questions

- Existing test scaffolding for `spawn.resolve_worktree` /
  `create_worktree`? Will check before adding new test files.
