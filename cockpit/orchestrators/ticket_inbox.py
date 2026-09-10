"""The ticket inbox — tickets assigned to me that have no worktree yet.

Every other row cockpit shows is derived from `git worktree list`: work already
started. This is the one surface for work *not* started, and it exists because a
ticket assigned to me with no branch is invisible to every other part of the
daemon — the `devdone=` pill and the Ticket column only ever read ids they found
in a PR body's delivery footer.

Shaped like `ReviewFolds`, and for the same reason: a repo alone cannot tell
whether its tickets have siblings in another repo of the same org. The slow
tick's per-repo pass `add()`s what each repo contributes; `publish()` drains the
whole accumulator once, after every repo has reported.

Two axes cross here, deliberately:

  * the **fetch** groups by resolved credential, because a Linear team key is
    scoped to the workspace its API key opens — asking one org's workspace about
    another's ticket answers about a different issue that merely shares an
    identifier. This is the trap `_secret_fingerprint` and
    `tickets._linear_narrow_repos`' grouping were both written for.
  * the **payload** keys by org bucket, because that is what the TUI groups
    under and what the trailing review fold already keys on.

One credential group can therefore feed several buckets, and one bucket can be
fed by several groups. Grouping on the pair `(provider, credential, bucket)` is
what keeps both properties true at once without a reconciliation step.

The bucket label is computed by the caller and passed in — this module never
reads `org`. `orgs` is a load-time defaults layer and nothing below
`load_config` may resolve one; keeping the label an argument means that rule
needs no exemption here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from cockpit.lib.cache import write_ticket_inbox
from cockpit.lib.linear import extract_ticket
from cockpit.lib.tickets import provider_for


def active_ids(branches: Iterable[str], delivered: Iterable[str]) -> set[str]:
    """Casefolded ids of tickets work has already started on — the `in_flight`
    half of the inbox, from two local signals and no network.

    `branches` are worktree branch names, mined with `extract_ticket`: that
    covers Linear and Jira, whose identifiers share the `KEY-123` shape a branch
    slug embeds. `delivered` are ids named in a PR body's delivery footer, which
    is provider-neutral and therefore what covers a Trello short link and a
    GitHub `owner/repo#N` — neither of which ever appears in a branch name.

    Branch-slug matching is the loose heuristic `extract_ticket` documents, and
    loose is the right way round here: a false positive hides a row for work that
    probably *is* underway, while a false negative offers to start something
    twice.

    Pure, and it takes both inputs rather than fetching either — the slow tick
    reads them off the cycle context it already holds and the fast tick off
    `git worktree list` plus the PR payloads, and neither seam moves into here.
    """
    ids = {found.casefold() for b in branches if (found := extract_ticket(b))}
    ids |= {str(d).casefold() for d in delivered if d}
    return ids


@dataclass(frozen=True)
class InboxRepo:
    """One repo's contribution to the inbox, recorded by the per-repo pass.

    `cred` is the provider's credential env-var *names* for this repo
    (`TicketProvider.credential_envs`) — names, never values, and the grouping
    key. Two repos naming the same var share a fetch; two naming different vars
    never do, even if both happen to hold the same secret. That asymmetry is
    deliberately the safe one: over-splitting costs an extra round-trip, while
    over-merging asks the wrong workspace. It is the same key
    `tickets._linear_narrow_repos` groups on.
    """

    bucket: str
    provider_name: str
    cred: tuple[str, ...]
    scopes: tuple[str, ...]
    nwo: str
    repo_entry: dict = field(compare=False, hash=False)


@dataclass
class TicketInbox:
    """Accumulator filled per repo, drained once by `publish`."""

    repos: list[InboxRepo] = field(default_factory=list)
    #: Casefolded ids of tickets that already have a worktree or a PR delivering
    #: them. Collected across every repo because a bucket spans repos, and the
    #: worktree that started an org's ticket may live in any of them.
    active: set[str] = field(default_factory=set)
    #: Set when the cycle did not reach every repo. A bucket is the union of its
    #: repos' contributions, so a repo that never reported makes the bucket
    #: *shrink* — indistinguishable from tickets having been finished. Suspends
    #: every write, exactly as `ReviewFolds.partial` suspends the dissolve.
    partial: bool = False

    def add(
        self,
        *,
        bucket: str,
        provider_name: str,
        cred: tuple[str, ...],
        scopes: tuple[str, ...],
        nwo: str,
        repo_entry: dict,
    ) -> None:
        if not bucket:
            return
        self.repos.append(
            InboxRepo(
                bucket=bucket,
                provider_name=provider_name,
                cred=cred,
                scopes=scopes,
                nwo=nwo,
                repo_entry=repo_entry,
            )
        )


def _groups(
    repos: list[InboxRepo],
) -> dict[tuple[str, tuple[str, ...], str], list[InboxRepo]]:
    """Bucket the recorded repos by `(provider, credential, org bucket)` — one
    fetch each. Insertion-ordered, so the fetch order follows config order."""
    out: dict[tuple[str, tuple[str, ...], str], list[InboxRepo]] = {}
    for repo in repos:
        out.setdefault((repo.provider_name, repo.cred, repo.bucket), []).append(repo)
    return out


def _dedup(tickets: list[dict]) -> list[dict]:
    """Drop repeats by casefolded id, newest `updated_at` first.

    A bucket fed by two credential groups can see one ticket twice; so can two
    repos in one group whose scopes overlap. The sort is what makes the screen's
    order stable across a fetch that returns groups in a different order.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for ticket in tickets:
        key = str(ticket.get("id") or "").casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(ticket)
    out.sort(key=lambda t: str(t.get("updated_at") or ""), reverse=True)
    return out


def publish(
    inbox: TicketInbox, cfg: dict, *, dry: bool = False
) -> dict[str, list[dict]]:
    """Fetch every group's tickets and write one payload per org bucket.

    Returns `{bucket: tickets}` for the buckets it wrote — the fetched shape, so
    a caller (and a test) can see what a cycle produced without reading the cache
    back.

    Each ticket is stamped `in_flight` against `inbox.active` here, so a freshly
    written payload is correct immediately rather than 30s later when the fast
    tick's `stamp_inbox_in_flight` next runs.

    Two things suspend a bucket, and both are the "couldn't ask" case rather than
    an "answered with nothing" one:

      * `inbox.partial` — the cycle didn't reach every repo, so every bucket is
        potentially short. Suspends all of them.
      * a group whose `fetch_my_open` returned None — the tracker couldn't be
        asked. Suspends only the buckets that group feeds.

    A suspended bucket keeps whatever payload it already had, which is the whole
    point: the alternative is an inbox that empties on a network blip and refills
    on the next cycle.

    `dry` suppresses the cache write like every other write under `--dry`, but
    the fetch still runs and the return value still describes what *would* have
    been written — a dry run reports rather than going quiet.
    """
    fetched: dict[str, list[dict]] = {}
    failed: set[str] = set()
    for (_provider_name, _cred, bucket), group in _groups(inbox.repos).items():
        rep = group[0].repo_entry
        provider = provider_for(cfg, rep)
        if provider is None:
            continue
        scopes = sorted({s for repo in group for s in repo.scopes})
        nwos = sorted({repo.nwo for repo in group if repo.nwo})
        tickets = provider.fetch_my_open(scopes, nwos=nwos, cfg=cfg, repo_entry=rep)
        if tickets is None:
            failed.add(bucket)
            continue
        fetched.setdefault(bucket, []).extend(tickets)
    if inbox.partial:
        return {}
    written: dict[str, list[dict]] = {}
    for bucket, tickets in fetched.items():
        if bucket in failed:
            continue
        deduped = _dedup(tickets)
        for ticket in deduped:
            ticket["in_flight"] = str(ticket.get("id") or "").casefold() in inbox.active
        if not dry:
            write_ticket_inbox(bucket, deduped)
        written[bucket] = deduped
    return written
