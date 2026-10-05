# GitHub — behavior spec

## Host

- [gh.host-from-origin~1] Given a repo whose `origin` is on a non-github.com
  host, every `gh` call about it states that host. The calls are the PR
  fetches, the ruleset read, the update-branch mutation, the login lookup and
  the issue transport. A github.com repo's calls carry no host at all. An
  unparsable remote reads as github.com.
- [gh.host-required~1] `_graphql` cannot be called without a stated host. A new
  GraphQL call site never inherits the default host silently.
- [gh.host-unauthenticated-warns~1] Given a configured repo on a host where
  `gh` holds no token, startup warns and names the host and the login command.
  The repo never renders as a repo with no PRs. A github.com repo asks `gh`
  nothing.
- [gh.reap-prefix-per-host~1] The orphan reaper tests the branch ref of a
  stranded workspace against the login of the host of its own repo. An
  enterprise-login ref stays recognised as mine. Given a host that cannot
  answer, the reaper falls back to the process-wide login. That fallback
  deletes no ref rather than the wrong one.

## PR identity

- [gh.pr-rank~1] Given a branch with an OPEN PR and a newer, higher-numbered
  duplicate that is closed, `_one_pr_per_branch` resolves the branch to
  exactly one PR. That PR is the OPEN one, in either input order. OPEN wins
  before `updated_at` and number.
- [pr-list.one-per-branch~2] Given a merged snapshot and a live snapshot that
  share one branch in one repo, the on-disk prune drops the superseded loser.
  It keeps the winner. A snapshot under the same branch name in another repo
  stays untouched.
- [gh.trunk-head~1] Given a synthesized `pr-<N>-…` branch in the fetch list,
  the relevant-PR query routes it by its embedded number. It uses
  `pullRequest(number:)` and no `headRefName` variable. An ordinary branch
  keeps its head-ref alias. A `main`-headed PR never joins by branch name.
- [gh.review-decision~1] (untested: design rationale) PR construction derives
  the null-`reviewDecision` fallback once. No renderer or gate re-derives an
  approval.

## update_stale_branches

- [update-stale.trigger~1] Given every merge state, only `BEHIND` reads as
  stale. `CLEAN`, `BLOCKED`, `UNKNOWN` and `DIRTY` do not. The local
  base-distance count never feeds the trigger.
- [update-stale.mutation~1] Nothing reachable from `_update_stale_branches`
  shells a `git rebase` or a force-push. The update is the server-side
  `updatePullRequestBranch` mutation. The plain reset in `resync_to_origin` is
  the one allowed local reconciliation.
- [update-stale.cas~1] The mutation receives the fetched head oid of the PR as
  its expected-head compare-and-swap argument. The method is REBASE.
- [update-stale.scope~1] Only the two quiescent states update. An unapproved,
  unsnoozed PR stays alone. A snoozed PR updates without an approval.
- [update-stale.dismissal-gate~1] Given an approved PR whose base dismisses
  stale reviews, the update is skipped.
- [update-stale.ruleset-read~1] The dismissal verdict combines the classic
  `dismissesStaleReviews` field with an injectable ruleset verdict. A caller
  that resolves the merged effective rules can block what the classic field
  alone would allow.
- [update-stale.two-sources~1] Given a rulesets-only repo, with classic
  protection null and `branch_dismisses_stale_reviews` true, an approved update
  stays blocked. The GraphQL field alone never opens the gate.
- [update-stale.fail-closed~1] Given a ruleset lookup that answers None
  ("couldn't ask"), the update skips an approved PR. Unreadable reads as
  dismisses. A snoozed PR never pays the lookup.
- [update-stale.resync~1] Given a local commit made after the fetch of the
  cycle, `resync_to_origin` refuses and the commit survives. The reset requires
  a clean tree and a HEAD still equal to the pre-update sha. A dirty-check
  alone never suffices.
