# GitHub — behavior spec

## PR identity

- [gh.pr-rank~1] Given a branch carrying an OPEN PR and a newer, higher-numbered
  duplicate that was closed, `_one_pr_per_branch` resolves the branch to exactly
  one PR — the OPEN one — in either input order: OPEN wins before `updated_at`
  and number.
- [pr-list.one-per-branch~2] Given a merged snapshot and a live snapshot sharing
  one branch in one repo, the on-disk prune drops the superseded loser and keeps
  the winner; a snapshot under the same branch name in another repo is
  untouched.
- [gh.trunk-head~1] Given a synthesized `pr-<N>-…` branch in the fetch list, the
  relevant-PR query routes it by its embedded number — `pullRequest(number:)`,
  no `headRefName` variable — while an ordinary branch keeps its head-ref alias;
  a `main`-headed PR never joins by branch name.
- [gh.review-decision~1] (untested: design rationale) The null-`reviewDecision`
  fallback is derived once at PR construction; no renderer or gate re-derives an
  approval.

## update_stale_branches

- [update-stale.trigger~1] Given every merge state, only `BEHIND` reads as
  stale; `CLEAN`, `BLOCKED`, `UNKNOWN` and `DIRTY` do not, and the local
  base-distance count never feeds the trigger.
- [update-stale.mutation~1] Nothing reachable from `_update_stale_branches`
  shells a `git rebase` or a force-push — the update is the server-side
  `updatePullRequestBranch` mutation, with `resync_to_origin`'s plain reset the
  one allowed local reconciliation.
- [update-stale.cas~1] The mutation is called with the PR's fetched head oid as
  its expected-head compare-and-swap argument, method REBASE.
- [update-stale.scope~1] Only the two quiescent states update: an unapproved,
  unsnoozed PR is left alone, while a snoozed PR updates without an approval.
- [update-stale.dismissal-gate~1] Given an approved PR whose base dismisses
  stale reviews, the update is skipped — updating would discard the approval.
- [update-stale.ruleset-read~1] The dismissal verdict combines the classic
  `dismissesStaleReviews` field with an injectable ruleset verdict, so a caller
  resolving the merged effective rules can block what the classic field alone
  would allow.
- [update-stale.two-sources~1] Given a rulesets-only repo — classic protection
  null, `branch_dismisses_stale_reviews` true — an approved update is still
  blocked; the GraphQL field alone never opens the gate.
- [update-stale.fail-closed~1] Given the ruleset lookup answers None ("couldn't
  ask"), an approved PR is not updated — unreadable reads as dismisses — and a
  snoozed PR never pays the lookup, having no approval to lose.
- [update-stale.resync~1] Given a local commit made after the cycle's fetch,
  `resync_to_origin` refuses and the commit survives — the reset requires a
  clean tree AND a HEAD still equal to the pre-update sha, never a dirty-check
  alone.
