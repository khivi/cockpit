"""Failed background spawns -- the second surface not derived from `git worktree list`.

A PR whose `cockpit new --pr` child failed has no worktree, so no row. The child
leaves a result marker (`lib/spawn_results.py`); this folds the markers into one
payload for the TUI (`cache.write_spawn_failures`).

Shaped like `ticket_inbox`: the slow tick's per-repo pass `add()`s what each
repo knows, and `publish()` drains the accumulator once, after every repo has
reported, so a scoped run skips it. It adds no network call -- the PR titles come
from the PR list the cycle already fetched.

Retiring is the daemon's job and only the daemon's: a marker is deleted once its
PR is no longer open, or already has a worktree. Only a repo that reported this
cycle can retire its markers; one that raised or was skipped leaves its markers
alone, since an absent PR list reads as "nothing open".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cockpit.lib import spawn_results
from cockpit.lib.cache import write_spawn_failures


@dataclass
class SpawnFailures:
    """Accumulator. `keys` maps every name a marker may carry for a repo (its
    nwo, or the config name the child falls back to) to that repo's
    `{pr number: title}` for open PRs, and `branches` its worktree branches."""

    open_prs: dict[str, dict[int, str]] = field(default_factory=dict)
    pr_branch: dict[str, dict[int, str]] = field(default_factory=dict)
    branches: dict[str, set[str]] = field(default_factory=dict)
    # A repo failed to report: the list is short, so don't write it (markers are
    # still retired per reporting repo -- that is safe).
    partial: bool = False

    def add(
        self,
        names: list[str],
        prs: dict[int, tuple[str, str]],
        branches: set[str],
    ) -> None:
        """`prs` is `{number: (title, branch)}` of the repo's open PRs."""
        for name in filter(None, names):
            self.open_prs[name] = {n: t for n, (t, _b) in prs.items()}
            self.pr_branch[name] = {n: b for n, (_t, b) in prs.items()}
            self.branches[name] = branches


def publish(acc: SpawnFailures, *, dry: bool = False) -> list[dict] | None:
    """Retire stale markers, write the survivors; returns what was written.

    None means the marker directory couldn't be read: nothing is written, so the
    TUI keeps its last payload instead of reading a blip as "all clear". The
    same holds under `acc.partial`.
    """
    pending = spawn_results.iter_pending()
    if pending is None:
        return None
    out: list[dict] = []
    for r in pending:
        titles = acc.open_prs.get(r.repo)
        if titles is None:
            continue  # its repo did not report this cycle: unknown, leave alone
        branch = acc.pr_branch[r.repo].get(r.pr)
        if branch is None or branch in acc.branches[r.repo]:
            if not dry:
                spawn_results.clear(r.repo, r.pr)
            continue
        out.append(
            {
                "pr": r.pr,
                "repo": r.repo,
                "title": titles[r.pr],
                "failures": r.failures,
                "ts": r.ts,
                "error": r.error,
            }
        )
    if not dry and not acc.partial:
        write_spawn_failures(out)
    return out
