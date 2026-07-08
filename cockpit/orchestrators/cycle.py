"""Per-repo reconciliation pipeline.

Composes gh + cmux + git + cache + starship + teardown wrappers into the
per-cycle sequence driven by `cockpit/cockpit.py`. The CLI entry points
(`--watch`) live in `cockpit.py`; everything between "read
config" and "next cycle" lives here.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, cast

import cockpit.lib.daemon_signal as daemon_signal
from cockpit.lib import version
from cockpit.lib.cache import (
    clear_branch_pr_cache,
    find_pr_payload,
    load_pr_payloads_by_branch,
    muted_payload,
    prune_superseded_pr_caches,
    write_base_ahead,
    write_base_distance,
    write_branch_pr_cache,
    write_git_state_cache,
    write_pr_cache,
)
from cockpit.lib.cmux import (
    ORANGE,
    ORPHAN_ICON,
    ORPHAN_KEY,
    CmuxUnavailable,
    apply_devdone_pill,
    apply_pills,
    apply_stale_pill,
    apply_wip_pill,
    clear_pr_pills,
    close_gone_cwd_workspaces,
    cmux,
    cmux_close_workspace_best_effort,
    deliver_followup,
    find_cockpit_workspaces,
    nudge_if_idle,
    rename_workspace_if_needed,
    set_workspace_color,
    spawn_orphan_workspace,
    spawn_pr_workspace,
    spawn_workspace,
    status_pills,
    workspace_is_idle,
    workspace_names,
    workspace_state,
)
from cockpit.lib.colors import (
    CMUX_COLOR_ANSI,
    Colorizer,
    blue,
    bold,
    cyan,
    dim,
    green,
    yellow,
)
from cockpit.lib.config import (
    COCKPIT_HOME,
    ensure_state_dirs,
    jira_email,
    jira_merge_done_status,
    jira_site_url,
    linear_merge_done_state,
    linear_team_keys,
    orphan_nudge_grace_seconds,
    review_command,
    ticket_close_on_merge,
    trello_merge_done_list,
)
from cockpit.lib.constants import MAIN_BRANCHES
from cockpit.lib.gh import (
    PR,
    OpenPRHead,
    fetch_merged_branches,
    is_dependabot,
    list_open_pr_heads,
    list_relevant_prs,
    repo_nwo,
)
from cockpit.lib.git import (
    Worktree,
    ahead_of_base,
    behind_of_base,
    branch_commits_ahead,
    delete_local_branch,
    ff_default_branch_worktrees,
    has_remote_branch,
    has_unique_commits,
    is_ancestor,
    list_local_branches,
    log_ff_advances,
    origin_head_branch,
    prune_worktrees,
    worktree_age_seconds,
    worktrees,
    worktrees_basic,
)
from cockpit.lib.github_issues import (
    close_issue,
    fetch_issue,
)
from cockpit.lib.github_issues import (
    viewer_login as github_viewer_login,
)
from cockpit.lib.issue_color import issue_color
from cockpit.lib.jira import (
    JIRA_API_TOKEN_ENV,
)
from cockpit.lib.jira import (
    fetch_issue_meta as jira_fetch_issue_meta,
)
from cockpit.lib.jira import (
    fetch_myself as jira_fetch_myself,
)
from cockpit.lib.jira import (
    transition_issue as jira_transition_issue,
)
from cockpit.lib.linear import (
    LINEAR_API_KEY_ENV,
    fetch_team_states,
    fetch_ticket_meta,
    fetch_viewer_id,
    update_ticket_state,
)
from cockpit.lib.log_format import verb
from cockpit.lib.nudges import NudgePref
from cockpit.lib.nudges import load_pref as _load_nudge_pref
from cockpit.lib.pills import ci_glyph
from cockpit.lib.prompts import claude_command, shell_quote, split_prompt_prefix
from cockpit.lib.tickets import TicketProvider, provider_for
from cockpit.lib.tool import has_workspace_backend, is_cmux
from cockpit.lib.trello import (
    TRELLO_API_KEY_ENV,
    TRELLO_API_TOKEN_ENV,
)
from cockpit.lib.trello import (
    fetch_card_meta as trello_fetch_card_meta,
)
from cockpit.lib.trello import (
    fetch_myself as trello_fetch_myself,
)
from cockpit.lib.trello import (
    move_card as trello_move_card,
)
from cockpit.orchestrators.teardown import TeardownRequest, teardown

# Cutoff for the *deep* merged-branches fetch that feeds the branch-ref reaper.
# Effectively unbounded (≈100 years) so a branch whose worktree was removed long
# ago is still recognized as merged — the reaper has no 14-day autoclose window
# to lean on. The `fetch_merged_branches` page cap (1 000 PRs) still bounds the
# query; the oldest merges beyond that simply reap on a later tick.
_DEEP_MERGED_CUTOFF_DAYS = 36500

_NUDGE_DESC = {
    "comments": lambda pr: (
        f"{pr.unaddressed} unresolved review thread(s) — reply or push fixes"
    ),
    "ci": lambda pr: (
        f"CI is failing ({pr.ci}) — run `gh pr checks {pr.number}` and address it"
    ),
    "conflicts": lambda _pr: "merge conflicts vs base — rebase and force-push",
}


def _cache_only(cfg: dict) -> bool:
    """True when the cmux-only tier must be skipped — the backend isn't cmux.
    Drives `ctx.headless`; gates pills/colors/focus/nudges + the orphan-reaper
    only. Teardown and the workspace tier have their own gates (see `cycle_repo`).
    """
    return not is_cmux()


def maybe_nudge(
    ref: str,
    message: str,
    dry: bool,
    tag: str,
    *,
    pr_number: int | None = None,
) -> bool:
    """Nudge `ref` if idle; return True iff the nudge actually fired."""
    if nudge_if_idle(
        ref,
        message,
        dry=dry,
        tag=tag,
        pr_number=pr_number,
    ):
        snippet = message if len(message) <= 60 else message[:57] + "..."
        print(
            f"  {verb('nudged', color=yellow)} {tag} → {ref}  {dim(snippet)}",
            flush=True,
        )
        return True
    return False


def _linear_state_ttl_seconds(cfg: dict) -> float:
    """Backstop staleness for the cached Linear delivery block. A ticket can move
    into the dev-done state without its PR's `Linear:` footer changing, so the
    cache is refetched when the footer id-set changes OR when it ages past this.
    Defaults to three slow cycles; override with `linear_state_ttl_seconds`.
    """
    explicit = cfg.get("linear_state_ttl_seconds")
    if explicit is not None:
        return float(explicit)
    return 3 * float(cfg.get("slow_poll_interval_seconds", 300))


def _linear_identity_ttl_seconds(cfg: dict) -> float:
    """Cross-tick cache lifetime for the near-immutable Linear *identity* data the
    merge-transition path reads — the API key's `viewer` id and each team's
    workflow-state name→UUID map. Both change far less often than ticket states,
    so they're cached in `pill_state` rather than refetched every slow tick.
    Defaults to twelve slow cycles; override with `linear_identity_ttl_seconds`.
    """
    explicit = cfg.get("linear_identity_ttl_seconds")
    if explicit is not None:
        return float(explicit)
    return 12 * float(cfg.get("slow_poll_interval_seconds", 300))


def _provider(ctx: RepoCycle) -> TicketProvider | None:
    """The repo's ticket-provider strategy, or None for `tickets: none`."""
    return provider_for(ctx.cfg, ctx.repo_entry)


def _ticket_footer_ids(ctx: RepoCycle, pr: PR) -> list[str]:
    """The ids of the tickets `pr` *delivers*, per the repo's ticket provider.

    Linear → `Linear: [PE-1234](url)` footers; GitHub → `Closes #123` closing
    keywords (resolved against the repo's own nwo). Empty for `tickets: none`.
    The two providers share the block shape (`{"tickets": [{"id", "state"}]}`),
    so the rest of the prefetch/devdone/merge path is provider-neutral.
    """
    provider = _provider(ctx)
    if provider is None:
        return []
    return provider.parse_footers(pr.body, f"{ctx.owner}/{ctx.name}")


def _decide_linear_refetch(ctx: RepoCycle, pr: PR, now: float) -> tuple[str, object]:
    """Decide how `pr`'s ticket-delivery block resolves this cycle — pure, no
    network. Returns one of:

      * `("skip", None)`   — repo has no ticket provider; leave the block None so
        `write_pr_cache` leaves the field untouched.
      * `("carry", block)` — the prior snapshot's footer id-set is unchanged and
        still within the TTL backstop; carry it forward verbatim.
      * `("build", ids)`   — (re)build the block from freshly-fetched states for
        `ids` (the footer set, possibly empty), triggered by a footer change or a
        prior snapshot aged past the TTL.

    Reads the *prior* on-disk snapshot via `ctx.pr_payloads` (loaded before the
    write loop overwrites the file), so a re-link refreshes immediately and an
    independent state transition is caught within the TTL.
    """
    if _provider(ctx) is None:
        return "skip", None
    ids = _ticket_footer_ids(ctx, pr)
    prior_block = (ctx.pr_payloads.get(pr.branch) or {}).get("linear")
    if prior_block:
        prior_ids = [t.get("id") for t in prior_block.get("tickets", [])]
        fresh = now - float(
            prior_block.get("fetched_at", 0)
        ) < _linear_state_ttl_seconds(ctx.cfg)
        if prior_ids == ids and fresh:
            return "carry", prior_block
    return "build", ids


def _prefetch_linear_blocks(ctx: RepoCycle) -> None:
    """Populate `ctx.linear_blocks` for every PR in one batched Linear round-trip
    per team, replacing a per-PR `fetch_ticket_state` fan-out.

    Two passes: first decide each PR's outcome (`_decide_linear_refetch`, pure) —
    carrying unchanged blocks forward and collecting the union of ticket ids that
    need a fresh state across *all* PRs — then resolve that union with a single
    `fetch_ticket_states` (one query per team) and assemble the rebuilt blocks
    from it. So a repo's whole crop of due tickets costs one round-trip per team,
    not one per ticket; nothing due → no network. Caller gates `ctx.dry`.

    Runs before the write loop's `write_pr_cache` calls so the decision still
    reads the prior on-disk snapshot, matching the old per-PR resolve ordering.
    """
    now = time.time()
    builds: list[tuple[str, list[str]]] = []  # (branch, footer ids) to rebuild
    due: set[str] = set()
    for pr in ctx.prs:
        outcome, data = _decide_linear_refetch(ctx, pr, now)
        if outcome == "carry":
            ctx.linear_blocks[pr.branch] = data  # type: ignore[assignment]
        elif outcome == "build":
            ids: list[str] = data  # type: ignore[assignment]
            builds.append((pr.branch, ids))
            due.update(ids)
        else:  # skip — repo has no ticket provider
            ctx.linear_blocks[pr.branch] = None
    if not builds:
        return
    states = _fetch_ticket_states(ctx, sorted(due)) if due else {}
    for branch, ids in builds:
        tickets = [{"id": tid, "state": states.get(tid)} for tid in ids]
        ctx.linear_blocks[branch] = {"tickets": tickets, "fetched_at": now}


def _fetch_ticket_states(ctx: RepoCycle, ids: list[str]) -> dict[str, str | None]:
    """Resolve `{id: dev-done-comparable state}` via the repo's provider strategy
    (`cockpit.lib.tickets`). Returns empty when the repo has no provider."""
    provider = _provider(ctx)
    if provider is None:
        return {}
    return provider.fetch_states(
        ids,
        repo_nwo=f"{ctx.owner}/{ctx.name}",
        repo_dir=str(ctx.repo_path),
        cfg=ctx.cfg,
        repo_entry=ctx.repo_entry,
    )


def _track_dev_done(ctx: RepoCycle, ref: str, block: dict | None) -> None:
    """Toggle the `devdone=` pill from the resolved Linear-delivery `block`
    (`{"tickets": [{"id", "state"}], ...}` or None — stashed in
    `ctx.linear_blocks` by `_resolve_linear_block`; no network here).

    The pill is raised — green — only when the PR delivers at least one ticket
    AND *every* delivered ticket is in the `linear_dev_done_state` workflow state
    (default "Dev Done"); the whole PR's scope is dev-complete. Shows the id when
    a single ticket, the `done/total` count when several. Cleared otherwise, so a
    ticket slipping back out of dev-done drops the pill. No-op in dry runs.
    """
    if ctx.dry:
        return
    provider = _provider(ctx)
    if provider is None:
        return
    tickets = (block or {}).get("tickets") or []
    if not tickets:
        apply_devdone_pill(ref, None)
        return
    target = provider.dev_done_value(ctx.cfg, ctx.repo_entry).casefold()
    done = [t for t in tickets if (t.get("state") or "").casefold() == target]
    if len(done) != len(tickets):
        apply_devdone_pill(ref, None)
        return
    label = tickets[0]["id"] if len(tickets) == 1 else f"{len(done)}/{len(tickets)}"
    apply_devdone_pill(ref, label)


def match_worktrees(
    prs: list[PR], wts: list[Worktree], self_user: str
) -> tuple[list[tuple[PR, Worktree]], list[PR]]:
    pr_by_branch = {pr.branch: pr for pr in prs}
    wt_by_branch = {w.branch: w for w in wts}
    matched: list[tuple[PR, Worktree]] = []
    skipped_self: list[PR] = []
    for pr in prs:
        if pr.author != self_user:
            continue
        wt = wt_by_branch.get(pr.branch)
        if wt is None:
            skipped_self.append(pr)
        else:
            matched.append((pr, wt))
    for wt in wts:
        pr_opt = pr_by_branch.get(wt.branch)
        if pr_opt is None or pr_opt.author == self_user:
            continue
        matched.append((pr_opt, wt))
    return matched, skipped_self


def _resolve_wt(
    ref: str,
    ws_name: str,
    cwds: dict[str, Path],
    wt_by_path: dict[Path, Worktree],
    wt_by_name: dict[str, Worktree],
) -> Worktree | None:
    """Resolve a workspace ref to its Worktree via cwd → path lookup, then name."""
    cwd = cwds.get(ref)
    if cwd is not None and (wt := wt_by_path.get(cwd.resolve())) is not None:
        return wt
    return wt_by_name.get(ws_name)


def _orphan_snapshot(
    wt: Worktree, behind_base: int
) -> tuple[frozenset[tuple[str, str]], str]:
    """Pill-state snapshot + display tag for an orphan worktree."""
    stale_tag = f" stale ↻{behind_base}" if behind_base > 0 else ""
    tag = f"orphan{' wip' if wt.dirty else ''}{stale_tag}"
    snap = frozenset(
        [
            ("orphan", ORPHAN_ICON),
            ("wip", str(wt.dirty_count) if wt.dirty else ""),
            ("stale", str(behind_base) if behind_base > 0 else ""),
        ]
    )
    return snap, tag


def _is_post_merge_stale(wt: Worktree, merged_branches: dict[str, str]) -> bool:
    """True if `wt`'s branch matches a merged PR whose head is still contained
    in HEAD — i.e. the merged work lives here and nothing has diverged onto a
    fresh lineage.

    Gated by reachability (`is_ancestor`), not commit count, so it stays True
    when the worktree pulled main on top of a squash-merge (the merge head
    remains an ancestor) yet flips False when the branch name was reused for new
    work after the old PR merged (the new HEAD no longer descends from the merge
    head). See `is_ancestor` for the full case table.
    """
    merged_head = merged_branches.get(wt.branch)
    if merged_head is None:
        return False
    return is_ancestor(wt.path, merged_head)


def _is_reused_branch_merge(wt: Worktree | None, pr: PR) -> bool:
    """True when `pr` is merged/closed but the worktree's HEAD has advanced past
    the PR's recorded head — the branch was reused for new local work, so the
    cached merged snapshot no longer describes this worktree.

    The display inverse of `_is_post_merge_stale`: that gate (keyed on the
    `merged_branches` map) decides autoclose; this one is keyed on the PR's own
    `head_oid`, so it also covers CLOSED-not-merged PRs, which never enter
    `merged_branches`. When the head SHA is unknown locally `is_ancestor`
    returns False — the same cold-repo false-negative autoclose accepts, here
    blanking a card that should briefly show the merged PR until the next fetch.

    Returns False when the head is unknown (`pr.head_oid` absent, e.g. an old
    cached PR pre-dating the field) so a real PR is never hidden.
    """
    if wt is None or not pr.head_oid:
        return False
    if str(pr.state).upper() not in ("MERGED", "CLOSED"):
        return False
    return not is_ancestor(wt.path, pr.head_oid)


def _is_orphan_main_sibling(wt: Worktree) -> bool:
    """True if `wt` is a non-trunk worktree fast-forwarded onto main with no
    local work left.

    After a feature PR squash-merges and the user pulls main, the original
    branch name is gone — `merged_branches` can't identify the worktree. The
    safe signal is "clean working tree AND no commits unique to HEAD vs
    `origin/<default>`". Caller must have already established
    `not wt.is_primary` and `wt.branch in MAIN_BRANCHES`.
    """
    if wt.dirty_count > 0:
        return False
    # unpushed == -1 means git failed; treat as "unknown, don't sweep".
    return wt.unpushed == 0


def _teardown_worktree(
    wt: Worktree,
    cwds: dict[str, Path],
    repo_path: Path,
    repo_name: str,
    *,
    dry: bool,
    delete_branch: bool = False,
) -> None:
    # Path match is the primary route; the fallback closes by workspace *name*,
    # which is `wt.workspace_name` (the branch label), not the dir basename.
    ref = _workspace_ref_for_path(wt.path, cwds) or wt.workspace_name
    teardown(
        TeardownRequest(
            ref=ref,
            name=wt.short,
            worktree_path=wt.path,
            branch=wt.branch,
            repo_path=repo_path,
            repo_name=repo_name,
            forced=True,
            delete_branch=delete_branch,
        ),
        dry=dry,
    )


def _workspace_ref_for_path(wt_path: Path, cwds: dict[str, Path]) -> str | None:
    """Find the cmux workspace ref whose cwd matches `wt_path`.

    The workspace name can diverge from the worktree dir name (e.g. a
    ticket-named workspace rooted in a feature worktree), so name-based
    closing misses or hits the wrong workspace. Resolve by path instead.
    """
    target = wt_path.resolve()
    for ref, cwd in cwds.items():
        if cwd.resolve() == target:
            return ref
    return None


def _cached_linear_identity[T](
    ctx: RepoCycle, key: str, fetch: Callable[[], T | None]
) -> T | None:
    """Return a Linear identity value from `pill_state[key]` when still within the
    identity TTL, else call `fetch()` and cache a *truthy* result. A falsy/failed
    fetch isn't cached, so a transient failure retries next tick (never poisons
    the cache with None). Used for the merge-transition's near-immutable reads.
    """
    now = time.time()
    entry = ctx.pill_state.get(key)
    ttl = _linear_identity_ttl_seconds(ctx.cfg)
    if isinstance(entry, dict) and (now - float(entry.get("ts", 0))) < ttl:
        return cast("T | None", entry.get("value"))
    value = fetch()
    if value:
        ctx.pill_state[key] = {"value": value, "ts": now}
    return value


def _cached_viewer_id(ctx: RepoCycle) -> str | None:
    """The API key's `viewer` id, cached across ticks. Keyed by a non-secret
    fingerprint of the key so rotating `LINEAR_API_KEY` invalidates the entry
    (the raw key is never stored in `pill_state`)."""
    raw = os.environ.get(LINEAR_API_KEY_ENV) or ""
    fp = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return _cached_linear_identity(ctx, f"linear-viewer:{fp}", fetch_viewer_id)


def _cached_team_states(ctx: RepoCycle, team_id: str) -> dict | None:
    """A team's workflow-state name→UUID map, cached across ticks (subsumes the
    old per-cycle dedupe — a same-tick second lookup is a cache hit)."""
    return _cached_linear_identity(
        ctx, f"linear-team-states:{team_id}", lambda: fetch_team_states(team_id)
    )


def _cached_github_viewer(ctx: RepoCycle) -> str | None:
    """The authenticated `gh` user's login, cached across ticks like the Linear
    viewer id. The "only close my own issues" gate for the GitHub merge writer.
    Keyed globally (gh auth is process-wide), reusing the identity TTL cache."""
    return _cached_linear_identity(
        ctx,
        "github-viewer",
        lambda: github_viewer_login(repo_dir=str(ctx.repo_path)),
    )


def _transition_merged_tickets(ctx: RepoCycle) -> None:
    """Dispatch the opt-in done-on-merge writer to the repo's ticket provider —
    Linear (move the ticket to the terminal state), GitHub (close the issue),
    Jira (workflow transition), or Trello (move the card to the terminal list).
    All share the `_is_post_merge_stale` trigger and the `merged-done:` marker;
    `tickets: none` does nothing. The *one* sanctioned tracker write per provider.
    """
    if ctx.dry:
        return
    provider = _provider(ctx)
    if provider is None:
        return
    if provider.name == "linear":
        _transition_merged_linear(ctx)
    elif provider.name == "github":
        _transition_merged_github(ctx)
    elif provider.name == "jira":
        _transition_merged_jira(ctx)
    elif provider.name == "trello":
        _transition_merged_trello(ctx)


def _transition_merged_linear(ctx: RepoCycle) -> None:
    """Opt-in: move a merged PR's delivered Linear tickets to the configured
    terminal state (`linear_merge_done_state`, default "Done").

    This is the *one* place cockpit *writes* to Linear — every other Linear
    touch is read-only. It runs on the same `_is_post_merge_stale` signal
    `_maybe_autoclose` uses, but independently: a ticket moves to Done on merge
    even when teardown is held back (uncommitted work, unaddressed threads) — a
    merged PR means the work shipped regardless of leftover local state.

    Gates, all of which must hold:
      * `linear_done_on_merge` enabled for this repo (per-repo over global);
      * the repo is Linear-configured (`linear_keys`) and `LINEAR_API_KEY` set;
      * not a dry run.

    Per delivered ticket (read from the cached PR snapshot's `linear` block —
    the strict footer set, no extra network to discover them):
      * skip if already evaluated this daemon run (a `merged-done:` marker in
        `pill_state` — keeps a kept-but-merged worktree from re-querying every
        tick; the already-at-target check below is the cross-restart backstop);
      * skip unless the ticket is assigned to me (the API key's `viewer`, fetched
        lazily on the first real candidate and cached across ticks) — never move
        a teammate's ticket;
      * skip if it's already at the target state, or its state `type` is
        `canceled` (never resurrect a canceled ticket — note "Dev Done"/"Done"
        both have `type: completed`, so equality, not type, decides "already
        done");
      * otherwise resolve the target state's UUID for the ticket's team and fire
        the `issueUpdate` mutation.
    """
    if ctx.dry:
        return
    if not ticket_close_on_merge(ctx.cfg, ctx.repo_entry):
        return
    if not linear_team_keys(ctx.cfg, ctx.repo_entry):
        return
    if not os.environ.get(LINEAR_API_KEY_ENV):
        return

    target = linear_merge_done_state(ctx.cfg, ctx.repo_entry)
    target_cf = target.casefold()
    # Viewer id resolved lazily on the first real candidate ticket (cached across
    # ticks), so a repo with nothing eligible makes zero Linear calls per tick.
    viewer_id: str | None = None
    viewer_resolved = False

    for wt in ctx.wts:
        if wt.is_primary or wt.branch in MAIN_BRANCHES:
            continue
        if not _is_post_merge_stale(wt, ctx.merged_branches):
            continue
        payload = find_pr_payload(wt.branch, ctx.name)
        tickets = ((payload or {}).get("linear") or {}).get("tickets") or []
        for entry in tickets:
            tid = entry.get("id")
            if not tid:
                continue
            marker = f"merged-done:{ctx.owner}/{ctx.name}:{tid}"
            if ctx.pill_state.get(marker):
                continue
            if not viewer_resolved:
                viewer_id = _cached_viewer_id(ctx)
                viewer_resolved = True
            if not viewer_id:
                # Can't confirm ownership → move nothing (fail-safe). No marker,
                # so a transient viewer-query failure retries next tick.
                continue
            meta = fetch_ticket_meta(tid)
            if not meta:
                continue  # transient failure → no marker, retry next tick
            # From here the ticket has been evaluated; mark it so a kept merged
            # worktree doesn't re-query every tick regardless of the outcome.
            ctx.pill_state[marker] = True
            if meta.get("assignee_id") != viewer_id:
                continue
            state_name = (meta.get("state") or "").casefold()
            if state_name == target_cf or meta.get("type") == "canceled":
                continue
            team_id = meta.get("team_id")
            if not team_id:
                continue
            states = _cached_team_states(ctx, team_id)
            state_id = (states or {}).get(target_cf)
            if not state_id:
                print(
                    f"  {verb('linear', color=yellow)} {dim(tid)}: "
                    f"target state {target!r} not found for its team — skipped",
                    flush=True,
                )
                continue
            if update_ticket_state(meta["id"], state_id):
                print(
                    f"  {verb('linear')} {tid} → {target} "
                    f"{dim(f'(merged {wt.short})')}",
                    flush=True,
                )
            else:
                # Mutation failed — clear the marker so a later tick retries.
                ctx.pill_state.pop(marker, None)
                print(
                    f"  {verb('linear', color=yellow)} {dim(tid)}: "
                    f"transition to {target!r} failed — will retry",
                    flush=True,
                )


def _transition_merged_github(ctx: RepoCycle) -> None:
    """Opt-in: close a merged PR's delivered GitHub issues — the analog of
    `_transition_merged_linear`, but the terminal action is `gh issue close`.

    Gates: `github_done_on_merge` (per-repo over global) and not dry (the
    dispatcher already checked `dry`). GitHub auto-closes same-repo issues
    referenced by a closing keyword on merge, so this mainly catches cross-repo
    refs and issues that were linked but not auto-closed.

    Per delivered issue (read from the cached PR snapshot — the strict closing
    set, no extra network to discover them):
      * skip if already evaluated this run (a `merged-done:` marker in
        `pill_state`, shared with the Linear writer — a repo is one provider);
      * skip unless the viewer (`gh` auth login, lazy + cached) is among the
        issue's assignees — never close a teammate's issue;
      * skip if it's already closed;
      * otherwise `gh issue close`. A failure clears the marker to retry.
    """
    if not ticket_close_on_merge(ctx.cfg, ctx.repo_entry):
        return

    nwo = f"{ctx.owner}/{ctx.name}"
    repo_dir = str(ctx.repo_path)
    viewer: str | None = None
    viewer_resolved = False

    for wt in ctx.wts:
        if wt.is_primary or wt.branch in MAIN_BRANCHES:
            continue
        if not _is_post_merge_stale(wt, ctx.merged_branches):
            continue
        payload = find_pr_payload(wt.branch, ctx.name)
        tickets = ((payload or {}).get("linear") or {}).get("tickets") or []
        for entry in tickets:
            ref = entry.get("id")
            if not ref:
                continue
            marker = f"merged-done:{ctx.owner}/{ctx.name}:{ref}"
            if ctx.pill_state.get(marker):
                continue
            if not viewer_resolved:
                viewer = _cached_github_viewer(ctx)
                viewer_resolved = True
            if not viewer:
                # Can't confirm ownership → close nothing (fail-safe). No marker,
                # so a transient viewer-query failure retries next tick.
                continue
            issue = fetch_issue(ref, repo_nwo=nwo, repo_dir=repo_dir)
            if not issue:
                continue  # transient failure → no marker, retry next tick
            # Evaluated; mark so a kept merged worktree doesn't re-query each tick.
            ctx.pill_state[marker] = True
            if viewer not in (issue.get("assignees") or []):
                continue
            if issue.get("state") == "closed":
                continue  # already done
            if close_issue(ref, repo_nwo=nwo, repo_dir=repo_dir):
                print(
                    f"  {verb('github')} {ref} closed {dim(f'(merged {wt.short})')}",
                    flush=True,
                )
            else:
                # Close failed — clear the marker so a later tick retries.
                ctx.pill_state.pop(marker, None)
                print(
                    f"  {verb('github', color=yellow)} {dim(ref)}: "
                    f"close failed — will retry",
                    flush=True,
                )


def _cached_jira_viewer(ctx: RepoCycle, site: str, email: str) -> str | None:
    """The authenticated Jira user's `accountId`, cached across ticks like the
    Linear/GitHub viewers — the "only transition my own issues" gate. Keyed by a
    non-secret fingerprint of `$JIRA_API_TOKEN` so rotating the token invalidates
    the entry (the raw token is never stored in `pill_state`)."""
    raw = os.environ.get(JIRA_API_TOKEN_ENV) or ""
    fp = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return _cached_linear_identity(
        ctx,
        f"jira-viewer:{fp}",
        lambda: jira_fetch_myself(site_url=site, email=email),
    )


def _transition_merged_jira(ctx: RepoCycle) -> None:
    """Opt-in: transition a merged PR's delivered Jira issues to the terminal
    status (`jira_merge_done_status`, default "Done") — the Jira analog of
    `_transition_merged_linear`, the terminal action being a Jira workflow
    transition.

    Gates, all of which must hold: `close_on_merge` enabled (per-repo over
    global), a configured `site_url` + `email`, `$JIRA_API_TOKEN` set. (The
    dispatcher already checked `dry`.)

    Per delivered issue (read from the cached PR snapshot — the strict footer
    set, no extra network to discover them):
      * skip if already evaluated this run (the shared `merged-done:` marker — a
        repo is one provider, so it can't collide with Linear/GitHub markers);
      * skip unless the issue is assigned to me (my `accountId`, lazy + cached) —
        never move a teammate's issue;
      * skip if it's already at the target status;
      * otherwise fire the transition. A failure clears the marker to retry.
    """
    if not ticket_close_on_merge(ctx.cfg, ctx.repo_entry):
        return
    site = jira_site_url(ctx.cfg, ctx.repo_entry)
    email = jira_email(ctx.cfg, ctx.repo_entry)
    if not site or not email or not os.environ.get(JIRA_API_TOKEN_ENV):
        return

    target = jira_merge_done_status(ctx.cfg, ctx.repo_entry)
    target_cf = target.casefold()
    # Viewer id resolved lazily on the first real candidate (cached across ticks),
    # so a repo with nothing eligible makes zero Jira calls per tick.
    viewer: str | None = None
    viewer_resolved = False

    for wt in ctx.wts:
        if wt.is_primary or wt.branch in MAIN_BRANCHES:
            continue
        if not _is_post_merge_stale(wt, ctx.merged_branches):
            continue
        payload = find_pr_payload(wt.branch, ctx.name)
        tickets = ((payload or {}).get("linear") or {}).get("tickets") or []
        for entry in tickets:
            key = entry.get("id")
            if not key:
                continue
            marker = f"merged-done:{ctx.owner}/{ctx.name}:{key}"
            if ctx.pill_state.get(marker):
                continue
            if not viewer_resolved:
                viewer = _cached_jira_viewer(ctx, site, email)
                viewer_resolved = True
            if not viewer:
                # Can't confirm ownership → move nothing (fail-safe). No marker,
                # so a transient viewer-query failure retries next tick.
                continue
            meta = jira_fetch_issue_meta(key, site_url=site, email=email)
            if not meta:
                continue  # transient failure → no marker, retry next tick
            # Evaluated; mark so a kept merged worktree doesn't re-query each tick.
            ctx.pill_state[marker] = True
            if meta.get("assignee_id") != viewer:
                continue
            if (meta.get("status") or "").casefold() == target_cf:
                continue  # already done
            if jira_transition_issue(key, target, site_url=site, email=email):
                print(
                    f"  {verb('jira')} {key} → {target} {dim(f'(merged {wt.short})')}",
                    flush=True,
                )
            else:
                # Transition failed — clear the marker so a later tick retries.
                ctx.pill_state.pop(marker, None)
                print(
                    f"  {verb('jira', color=yellow)} {dim(key)}: "
                    f"transition to {target!r} failed — will retry",
                    flush=True,
                )


def _cached_trello_viewer(ctx: RepoCycle) -> str | None:
    """The authenticated Trello member's id, cached across ticks like the
    Linear/GitHub/Jira viewers — the "only move my own cards" gate. Keyed by a
    non-secret fingerprint of `$TRELLO_API_TOKEN` so rotating the token
    invalidates the entry (the raw token is never stored in `pill_state`)."""
    raw = os.environ.get(TRELLO_API_TOKEN_ENV) or ""
    fp = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return _cached_linear_identity(
        ctx,
        f"trello-viewer:{fp}",
        trello_fetch_myself,
    )


def _transition_merged_trello(ctx: RepoCycle) -> None:
    """Opt-in: move a merged PR's delivered Trello cards to the terminal list
    (`trello_merge_done_list`) — the Trello analog of `_transition_merged_jira`,
    the terminal action being a card move to another board column.

    Gates, all of which must hold: `close_on_merge` enabled (per-repo over
    global), a configured `merge_done_list` (no default — an unset value leaves
    the move off), `$TRELLO_API_KEY` + `$TRELLO_API_TOKEN` set. (The dispatcher
    already checked `dry`.)

    Per delivered card (read from the cached PR snapshot — the strict footer set,
    no extra network to discover them):
      * skip if already evaluated this run (the shared `merged-done:` marker — a
        repo is one provider, so it can't collide with the other providers');
      * skip unless I'm a member of the card (my member id, lazy + cached) —
        never move a teammate's card;
      * skip if it's already on the target list;
      * otherwise fire the move. A failure clears the marker to retry.
    """
    if not ticket_close_on_merge(ctx.cfg, ctx.repo_entry):
        return
    target = trello_merge_done_list(ctx.cfg, ctx.repo_entry)
    if not target:
        return
    if not os.environ.get(TRELLO_API_KEY_ENV) or not os.environ.get(
        TRELLO_API_TOKEN_ENV
    ):
        return

    target_cf = target.casefold()
    # Viewer id resolved lazily on the first real candidate (cached across ticks),
    # so a repo with nothing eligible makes zero Trello calls per tick.
    viewer: str | None = None
    viewer_resolved = False

    for wt in ctx.wts:
        if wt.is_primary or wt.branch in MAIN_BRANCHES:
            continue
        if not _is_post_merge_stale(wt, ctx.merged_branches):
            continue
        payload = find_pr_payload(wt.branch, ctx.name)
        tickets = ((payload or {}).get("linear") or {}).get("tickets") or []
        for entry in tickets:
            ref = entry.get("id")
            if not ref:
                continue
            marker = f"merged-done:{ctx.owner}/{ctx.name}:{ref}"
            if ctx.pill_state.get(marker):
                continue
            if not viewer_resolved:
                viewer = _cached_trello_viewer(ctx)
                viewer_resolved = True
            if not viewer:
                # Can't confirm ownership → move nothing (fail-safe). No marker,
                # so a transient viewer-query failure retries next tick.
                continue
            meta = trello_fetch_card_meta(ref)
            if not meta:
                continue  # transient failure → no marker, retry next tick
            # Evaluated; mark so a kept merged worktree doesn't re-query each tick.
            ctx.pill_state[marker] = True
            if viewer not in (meta.get("members") or []):
                continue
            if (meta.get("list") or "").casefold() == target_cf:
                continue  # already done
            if trello_move_card(ref, target):
                print(
                    f"  {verb('trello')} {ref} → {target} "
                    f"{dim(f'(merged {wt.short})')}",
                    flush=True,
                )
            else:
                # Move failed — clear the marker so a later tick retries.
                ctx.pill_state.pop(marker, None)
                print(
                    f"  {verb('trello', color=yellow)} {dim(ref)}: "
                    f"move to {target!r} failed — will retry",
                    flush=True,
                )


def _maybe_autoclose(
    repo_path: Path,
    repo_name: str,
    wts: list[Worktree],
    merged_branches: dict[str, str],
    cwds: dict[str, Path],
    *,
    prs: list[PR] | None = None,
    dry: bool,
) -> None:
    """Remove worktrees + workspaces for merged branches that are clean.

    Removes any merged branch (mine or coworker's) when the worktree is clean.
    Coworker worktrees are safe to clean since they can be re-created from the
    merged PR if needed.

    Authoritative merge signal: `gh pr list --state merged` (via
    `merged_branches`), which maps each branch to the `headRefOid` it pointed at
    when merged. A branch being *present* in that map is not enough — a branch
    name reused for new work after its old PR merged (delete-and-recreate, or a
    reset onto a different lineage) is still listed, and tearing it down nukes a
    worktree the user just created. Gate on `_is_post_merge_stale` instead: tear
    down only worktrees whose HEAD still descends from the recorded merge head;
    a reused branch on a fresh lineage does not, so it survives.

    That reachability gate cannot prove "branch work is in main" for squash- or
    rebase-merges (the resulting SHAs differ) — but it does not need to. It asks
    the answerable question, "is the merge head still in this worktree's
    history", which stays True after a squash-merge + `git pull` main (the merge
    head remains an ancestor) and only goes False when the branch diverged onto
    a fresh lineage. So the squash-then-pull-main worktree still cleans up.

    Smart-skip on PR signals the author likely still wants to revisit before
    cleanup: draft, CI not passing, or unaddressed review threads.

    Teardown delegates to `orchestrators.teardown.teardown` (forced=True since we've
    already validated merge-state-clean above).

    A merged PR is the *only* trigger that reaches teardown here — a worktree
    with no merged PR (research/planning, an open PR, a coworker branch with no
    PR) is never touched. Stale-but-merged worktrees are the sole auto-reap
    case; everything else lives until the user closes it (TUI `c`).
    """
    pr_by_branch = {pr.branch: pr for pr in (prs or [])}
    for wt in wts:
        if wt.is_primary:
            continue
        if wt.branch in MAIN_BRANCHES:
            if _is_orphan_main_sibling(wt):
                _teardown_worktree(wt, cwds, repo_path, repo_name, dry=dry)
            elif wt.dirty_count > 0 and not dry:
                # Held back by uncommitted work — new changes started on `main`
                # inside a merged worktree. No other refresh path covers main-
                # branch siblings (both pill loops skip MAIN_BRANCHES), so surface
                # a WIP pill here to explain why the workspace is being kept.
                ref = _workspace_ref_for_path(wt.path, cwds)
                if ref is not None:
                    apply_wip_pill(ref, wt.dirty_count)
            continue
        if not _is_post_merge_stale(wt, merged_branches):
            continue
        # Merged: the Linear dev-done pill raised while the PR was open is now
        # moot. The PR has left the tracked open-PR set, so `_track_dev_done`
        # won't run again to clear it on a kept workspace (live, the ticket has
        # usually moved to "Done" anyway, but don't depend on that) — clear it
        # here so a skipped teardown doesn't strand the pill.
        merged_ref = _workspace_ref_for_path(wt.path, cwds)
        if merged_ref is not None and not dry:
            apply_devdone_pill(merged_ref, None)
        if wt.dirty_count > 0:
            print(
                f"  {verb('autoclose')} {dim(f'skipped (uncommitted) {wt.short}')} "
                f"{dim(f'({wt.dirty_count} dirty)')}",
                flush=True,
            )
            continue
        pr = pr_by_branch.get(wt.branch)
        if pr is not None:
            reasons: list[str] = []
            if pr.is_draft:
                reasons.append("draft")
            if pr.ci not in ("passed", "none", "unknown", ""):
                reasons.append(f"ci={pr.ci}")
            if pr.unaddressed > 0:
                reasons.append(f"{pr.unaddressed} unaddressed")
            if reasons:
                joined = ", ".join(reasons)
                print(
                    f"  {verb('autoclose')} {dim(f'skipped ({joined}) {wt.short}')}",
                    flush=True,
                )
                continue
        # Delete the local branch ref too, but only when HEAD sits at the merged
        # head with nothing on top. `_is_post_merge_stale` permits teardown when
        # the merge head is *any* ancestor of HEAD — that includes a branch the
        # user committed new (clean, unpushed) work onto after the merge. The
        # worktree removal alone leaves that work recoverable via the branch ref;
        # `git branch -D` would not, so keep the ref in that case.
        merged_head = merged_branches.get(wt.branch)
        delete_branch = merged_head is not None and not has_unique_commits(
            wt.path, merged_head
        )
        _teardown_worktree(
            wt, cwds, repo_path, repo_name, dry=dry, delete_branch=delete_branch
        )


def _branch_reap_reason(ctx: RepoCycle, branch: str, default: str | None) -> str | None:
    """Why `branch` is safe to delete, or None to keep it.

    Two safe cases, checked merged-first so a squash-merged branch whose remote
    was already deleted is still recognized (it would fail the "contained in
    default" test the no-remote path uses):

      - merged PR (all-time, via `merged_branches_deep`) with no commits on top
        of the recorded merge head. Mirrors the `has_unique_commits(wt.path,
        merged_head)` guard `_maybe_autoclose` applies before `git branch -D`: a
        branch reset/recreated onto a fresh lineage (commits not reachable from
        the merge head) reads > 0 and is kept.
      - no remote tracking ref AND no commits unique vs `origin/<default>` — the
        branch was never pushed and is fully contained in the default branch, so
        nothing is lost. A never-pushed branch WITH unique commits is work the
        user may not have backed up anywhere; it is kept (the "block" decision).

    Returns None on any git failure: `branch_commits_ahead` yields -1 (≠ 0) for
    an unknown merge-head SHA or bad ref, so an unverifiable branch is kept.
    """
    merged_head = ctx.merged_branches_deep.get(branch)
    if merged_head is not None:
        if branch_commits_ahead(ctx.repo_path, merged_head, branch) == 0:
            return "merged PR"
        return None
    if (
        not has_remote_branch(ctx.repo_path, branch)
        and default is not None
        and branch_commits_ahead(ctx.repo_path, f"origin/{default}", branch) == 0
    ):
        return "no remote, contained in default"
    return None


def _reap_branch_refs(ctx: RepoCycle) -> None:
    """Delete stale local branch refs that have no worktree and are provably safe.

    Closes the gap `_maybe_autoclose` leaves: it only iterates *existing*
    worktrees, so a branch whose worktree was already removed (manual `rm`, a
    prior teardown, an OS tmpdir wipe) keeps its dangling local ref forever. This
    pass enumerates every local branch (mine or coworker's — branch identity, not
    prefix, decides safety) and deletes the ones `_branch_reap_reason` clears.

    Always kept: `MAIN_BRANCHES` / the repo's default branch, any branch with a
    live worktree, any branch with an open PR, and anything whose safety can't be
    verified.

    Worktree-presence is read **fresh** here (`worktrees_basic`), NOT from the
    start-of-cycle `ctx.wts` snapshot — this is a destructive `git branch -D`
    decision, and it must not straddle two git reads taken at different instants.
    `git worktree add -b` creates the branch ref and the worktree atomically, so
    a fresh worktree read is guaranteed consistent with the fresh
    `list_local_branches` enumeration below: a branch visible to one is visible
    to the other. The snapshot broke that atomicity — `_spawn_missing_workspaces`
    runs earlier in the same `cycle_repo` and its detached `git worktree add`
    lands *after* the snapshot, so a brand-new never-pushed branch (trivially
    "no remote, contained in default") was invisible to `ctx.wts` and drew a
    `git branch -D` every spawn cycle (git refused it, kept the worktree, but
    the reap misfired). The snapshot's only claimed value — deferring a
    just-removed worktree's branch by one tick — is redundant: every case
    `_branch_reap_reason` clears is provably recoverable (in origin or merged
    history), so reaping it the same tick loses nothing. No double-delete.

    Runs every slow tick — worktree teardown is unconditional cockpit behavior,
    the same as `_maybe_autoclose`.
    """
    default = origin_head_branch(ctx.repo_path)
    wt_branches = {wt.branch for wt in worktrees_basic(ctx.repo_path)}
    open_pr_branches = {pr.branch for pr in ctx.prs if pr.state == "OPEN"}
    for branch in list_local_branches(ctx.repo_path):
        if branch in MAIN_BRANCHES or branch == default:
            continue
        if branch in wt_branches or branch in open_pr_branches:
            continue
        reason = _branch_reap_reason(ctx, branch, default)
        if reason is None:
            continue
        action = "[dry] reap-branch" if ctx.dry else "reap-branch"
        print(f"  {verb(action)} {bold(branch)}  {dim(reason)}", flush=True)
        if ctx.dry:
            continue
        ok, err = delete_local_branch(ctx.repo_path, branch)
        if not ok:
            print(
                f"  warn: git branch -D {branch} failed: {err}",
                file=sys.stderr,
                flush=True,
            )


def _refresh_base_distance(
    repo_path: Path, wts: list[Worktree], default: str | None
) -> dict[str, int]:
    """Fetch `origin/<default>` once per repo, then compute and cache both
    rebase-staleness (`HEAD..origin/<default>`) and ahead-of-base
    (`origin/<default>..HEAD`) for each feature worktree.

    Returns a `{branch: behind_count}` map for the caller to consume (e.g.
    orphan pill staleness). On any failure (no origin/HEAD, fetch error)
    all feature worktrees get an empty cache so stale readings don't
    survive.

    `git fetch` is run with `--quiet` from the main repo path; refs are
    shared across worktrees, so fetching once per repo is sufficient.
    """
    feature = [w for w in wts if not w.is_primary]
    distances: dict[str, int] = {}
    if not feature:
        return distances

    def _invalidate() -> dict[str, int]:
        for wt in feature:
            write_base_distance(wt.branch, -1)
            write_base_ahead(wt.branch, -1)
        return distances

    if not default:
        return _invalidate()
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_path), "fetch", "--quiet", "origin", default],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(
            f"  {yellow('skip')} base-distance refresh for {repo_path.name}: {e}",
            file=sys.stderr,
            flush=True,
        )
        return _invalidate()
    if res.returncode != 0:
        print(
            f"  {yellow('skip')} base-distance refresh for {repo_path.name}: "
            f"fetch origin {default} exited {res.returncode}: "
            f"{res.stderr.strip()}",
            file=sys.stderr,
            flush=True,
        )
        return _invalidate()
    for wt in feature:
        n = behind_of_base(wt.path, default)
        distances[wt.branch] = n
        write_base_distance(wt.branch, n)
        write_base_ahead(wt.branch, ahead_of_base(wt.path, default))
    return distances


@dataclass
class RepoCycle:
    """Per-repo, per-cycle context bundle. Mutable dicts (pill_state /
    pr_cache) are passed by reference and persist across cycles.
    """

    cfg: dict
    repo_path: Path
    owner: str
    name: str
    self_user: str
    wts: list[Worktree]
    prs: list[PR]
    tracked: dict[str, tuple[PR, Worktree]]
    names: dict[str, str]
    cwds: dict[str, Path]
    merged_branches: dict[str, str]
    merged_branches_deep: dict[str, str]
    pill_state: dict
    dry: bool
    headless: bool
    # False when the workspace-state fetch failed (limux degrade): cwds/names are
    # empty-but-unreliable, so the workspace-capable tier (spawn/skills) is skipped
    # to avoid re-spawning duplicates of live-but-unlisted workspaces.
    workspace_state_ok: bool = True
    default_branch: str | None = None
    prefs: dict[int, NudgePref] = field(default_factory=dict)
    base_distance: dict[str, int] = field(default_factory=dict)
    pr_payloads: dict[str, dict] = field(default_factory=dict)
    review_candidates: list[OpenPRHead] = field(default_factory=list)
    # The repo's config.json entry — carries `linear_keys` for the devdone gate.
    # Defaulted so existing RepoCycle(...) call sites and test stubs need no change.
    repo_entry: dict = field(default_factory=dict)
    # branch → resolved Linear-delivery block (or None) for this cycle, stashed by
    # _write_pr_caches. Read by _track_dev_done — NOT via ctx.pr_payloads, whose
    # pre-write snapshot wouldn't carry the freshly-resolved block (it'd lag a
    # cycle and miss the pill the cycle a footer first appears).
    linear_blocks: dict[str, dict | None] = field(default_factory=dict)


def _repo_name_color(repo_entry: dict) -> Colorizer:
    """Colorizer for this repo's name in the cycle log — its `sidebar_color`
    (echoing the cmux sidebar tint) when set, else plain `bold`. The value is
    preflight-validated, so an unset/missing key is the only fallback case.
    """
    return CMUX_COLOR_ANSI.get(repo_entry.get("sidebar_color") or "", bold)


def _prepare_cycle(
    repo_entry: dict,
    self_user: str,
    *,
    cfg: dict,
    pr_cache: dict,
    pill_state: dict,
    dry: bool,
) -> RepoCycle | None:
    """Validate the repo, fetch wts/state/merged in parallel, fetch relevant PRs,
    print the cycle header. Returns None if the repo should be skipped this cycle.
    """
    repo_path = Path(os.path.expanduser(repo_entry["path"]))
    if not repo_path.is_dir():
        print(
            f"  {yellow('skip')} {repo_entry.get('name', repo_path.name)}: "
            f"path does not exist ({repo_path})",
            flush=True,
        )
        return None
    try:
        owner, name = repo_nwo(repo_path)
    except RuntimeError as e:
        print(f"  {yellow('skip')} {repo_path}: {e}", flush=True)
        return None

    # Drop admin entries for worktree dirs deleted out-of-band before we read
    # the list, so teardown/autoclose never act on a path that no longer exists.
    prune_worktrees(repo_path)

    headless = _cache_only(cfg)
    workspace_state_ok = True  # cleared by the limux degrade below; see the field
    with ThreadPoolExecutor(max_workers=4) as ex:
        wts_fut = ex.submit(
            worktrees,
            repo_path,
            repo_entry.get("branch_prefix", ""),
            repo_entry.get("name", ""),
        )
        # cmux AND limux can list workspaces ('none' has no tool). Fetch the cwd
        # map on limux too — autoclose uses it to close the merged worktree's
        # workspace by ref in the same tick, not just remove the worktree.
        state_fut = ex.submit(workspace_state) if has_workspace_backend() else None
        merged_fut = ex.submit(
            fetch_merged_branches,
            owner,
            name,
            cutoff_days=int(cfg.get("autoclose_age_days", 14)),
        )
        # Unbounded merged map for the branch-ref reaper (`_reap_branch_refs`),
        # which sees branches whose worktrees were removed long before the
        # 14-day autoclose window. Fetched in parallel so it adds no latency.
        merged_deep_fut = ex.submit(
            fetch_merged_branches, owner, name, cutoff_days=_DEEP_MERGED_CUTOFF_DAYS
        )
        wts = wts_fut.result()
        try:
            names, cwds = ({}, {}) if state_fut is None else state_fut.result()
        except CmuxUnavailable as e:
            if not headless:
                # cmux: the whole reconcile is workspace-centric — skip the repo.
                print(
                    f"  {yellow('skip')} {owner}/{name}: cmux unavailable: {e}",
                    flush=True,
                )
                return None
            # limux: a listing hiccup shouldn't skip the repo — degrade to empty
            # cwds (autoclose still reaps) and mark the inventory unreliable.
            print(
                f"  {yellow('warn')} {owner}/{name}: workspace state unavailable: {e}",
                file=sys.stderr,
                flush=True,
            )
            names, cwds = {}, {}
            workspace_state_ok = False
        merged_branches = merged_fut.result()
        merged_branches_deep = merged_deep_fut.result()

    # Pass every local feature branch (mine + coworker). The per-branch leg
    # in list_relevant_prs fetches any-state PRs so the cache refreshes after
    # OPEN→MERGED / OPEN→CLOSED — `is:open author:self` alone misses those.
    branches = sorted({w.branch for w in wts if w.branch not in MAIN_BRANCHES})
    try:
        prs = list_relevant_prs(owner, name, self_user, branches, cache=pr_cache)
    except RuntimeError as e:
        print(
            f"  {yellow('skip')} {owner}/{name}: list_relevant_prs failed: {e}",
            flush=True,
        )
        return None

    # When `review_prs` is set, also pull every other-authored open PR so the
    # spawn phase can create review worktrees for ones we don't track yet. The
    # daemon's normal query is `author:self` + per-worktree aliases, so without
    # this the daemon never sees a coworker's PR until a local worktree exists.
    # Gated on a workspace backend (cmux + limux), matching the spawn phase that
    # consumes it — `none` can't create the review workspace, so don't fetch.
    review_candidates: list[OpenPRHead] = []
    if repo_entry.get("review_prs") and has_workspace_backend():
        try:
            review_candidates = list_open_pr_heads(owner, name)
        except RuntimeError as e:
            print(
                f"  {yellow('warn')} {owner}/{name}: review_prs open-PR fetch "
                f"failed: {e}",
                file=sys.stderr,
                flush=True,
            )

    tracked = find_cockpit_workspaces(prs, wts, names=names, cwds=cwds)
    # Resolve nudge prefs once per cycle — the single point of mute-state I/O.
    # Everything downstream (write_pr_cache, write_branch_pr_cache, apply_pills,
    # status_pills) reads from this dict. See AGENTS.md "PR cache writers".
    prefs = {pr.number: _load_nudge_pref(pr.number) for pr in prs}
    mine = sum(1 for pr in prs if pr.author == self_user)
    coworker_relevant = len(prs) - mine
    feature_wts = [w for w in wts if w.branch not in MAIN_BRANCHES]
    wip_count = sum(1 for w in feature_wts if w.dirty)
    ts = datetime.now().isoformat(timespec="seconds")
    print(
        f"{green(f'[{ts}]')} {_repo_name_color(repo_entry)(f'{owner}/{name}')}  "
        f"mine: {mine}  coworker-with-wt: {coworker_relevant}  "
        f"worktrees: {len(feature_wts)}  tracked: {len(tracked)}  wip: {wip_count}",
        flush=True,
    )
    return RepoCycle(
        cfg=cfg,
        repo_entry=repo_entry,
        repo_path=repo_path,
        owner=owner,
        name=name,
        self_user=self_user,
        wts=wts,
        prs=prs,
        tracked=tracked,
        names=names,
        cwds=cwds,
        merged_branches=merged_branches,
        merged_branches_deep=merged_branches_deep,
        pill_state=pill_state,
        dry=dry,
        headless=headless,
        workspace_state_ok=workspace_state_ok,
        default_branch=origin_head_branch(repo_path),
        prefs=prefs,
        review_candidates=review_candidates,
    )


def _write_pr_caches(ctx: RepoCycle) -> None:
    """Refresh base-distance cache + PR caches for the cship statusline.

    Mirroring PR fields into the cship cache lets `starship.toml [custom.*]`
    modules render fresh on the first session render without each field
    having to spawn its own `gh pr view` from cold.
    """
    # Build the branch→payload map once for the cycle's downstream consumers
    # (_refresh_tracked_pills reads `reusedBranch` from it), replacing a per-PR
    # find_pr_payload scan. The loader applies the same rank dedup, so this
    # pre-write snapshot matches what a post-write per-call lookup would return.
    ctx.pr_payloads = load_pr_payloads_by_branch(ctx.name)
    if ctx.dry:
        return
    ctx.base_distance = _refresh_base_distance(
        ctx.repo_path, ctx.wts, ctx.default_branch
    )
    for wt in ctx.wts:
        write_git_state_cache(wt.path, wt.repo_name)
    wt_by_branch = {wt.branch: wt for wt in ctx.wts}
    open_branches = {p.branch for p in ctx.prs if p.state == "OPEN"}
    # Resolve every PR's Linear-delivery block in one batched pass BEFORE the
    # write loop (each `write_pr_cache` overwrites the snapshot the refetch-vs-
    # carry-forward decision reads, so this must run against the old files).
    _prefetch_linear_blocks(ctx)
    for pr in ctx.prs:
        pref = ctx.prefs.get(pr.number)
        wt_opt = wt_by_branch.get(pr.branch)
        # None for non-Linear repos → field untouched by write_pr_cache.
        linear = ctx.linear_blocks.get(pr.branch)
        reused = _is_reused_branch_merge(wt_opt, pr)
        # The author's login only when this is someone else's PR (a coworker /
        # review PR) — empty for my own. Resolved here, the one place self_user
        # is known, and baked into the cache so the flat-cell republish paths
        # don't need it (see write_pr_cache's `other_author`).
        other_author = pr.author if pr.author != ctx.self_user else ""
        write_pr_cache(
            ctx.name,
            pr,
            wt_opt,
            pref,
            linear=linear,
            reused_branch=reused,
            other_author=other_author,
        )
        if reused:
            # Branch reused for new local work after this PR merged/closed —
            # show no PR. Only clear the branch-keyed cells when no live open PR
            # shares the branch; otherwise that PR's own iteration writes them
            # (and `_pr_payload_rank` resolves the card to the open PR).
            if pr.branch not in open_branches:
                clear_branch_pr_cache(pr.branch)
            continue
        write_branch_pr_cache(
            pr.branch,
            state=pr.state,
            is_draft=pr.is_draft,
            review_decision=pr.review_decision,
            number=pr.number,
            title=pr.title,
            ci_glyph=ci_glyph(pr.ci),
            muted=muted_payload(pref),
            comments=pr.unaddressed,
            total=pr.total_from_others,
            author=other_author,
            nudge=pr.nudge_issue,
        )
    # After the live snapshots are on disk, drop any superseded snapshot
    # sharing a branch (reused branch: old merged PR alongside the live one)
    # so branch-keyed flat cells resolve deterministically.
    prune_superseded_pr_caches(ctx.name)
    # Reload after the writes so downstream consumers see this tick's freshly
    # computed `reusedBranch` flags and the post-prune winners (the pre-write
    # snapshot at the top still serves the dry-run early return above).
    ctx.pr_payloads = load_pr_payloads_by_branch(ctx.name)


def _ref_pid(ref: str) -> int:
    """PID embedded in a cmux `workspace:<pid>` ref (the sort key for dedup)."""
    return int(ref.split(":")[1])


def _dedupe_workspaces(ctx: RepoCycle) -> set[str]:
    """Close duplicate cmux workspaces, keeping the lowest-PID per group.
    Returns the surviving refs.

    `ctx.names`/`ctx.cwds` are the GLOBAL cmux workspace state (every repo
    watched, not just this one) — `workspace_state()` is refetched per-repo but
    returns the same whole-machine snapshot every time. A workspace whose cwd
    doesn't resolve under THIS repo (a different repo entirely, or unresolvable)
    is excluded from grouping altogether: it must never fall back to the
    name-key group, or two repos with the same branch label (workspace names
    are bare branch labels, no repo prefix — see `wt.workspace_name`) collide on
    that key and a live, unrelated workspace from the other repo gets closed as
    a "duplicate".

    Group key is the workspace's live feature-worktree path when it sits on one,
    else its name. A workspace correctly rooted on its own worktree is the
    canonical workspace for that worktree — never a duplicate of a same-named
    workspace on a DIFFERENT worktree. `workspace_name` truncates to 30 chars,
    so two long branches sharing a 30-char prefix collide (dependabot
    `json5-1.0.2` vs `json5-and-laravel-mix-…` both → `[beta]
    dependabot-npm-and-yarn-json5-`); name-keying closed one every cycle, the
    matched-PR spawn re-created it next cycle, churning spawn→close→respawn.
    Path-keying stops that while still deduping true duplicates: same-worktree
    double-spawns (same OR different name) share the path key, and workspaces
    with no live worktree (but still rooted somewhere under this repo) fall
    back to the name key.
    """

    def _close_extras(refs_sorted: list[str]) -> None:
        keep_name = ctx.names.get(refs_sorted[0], refs_sorted[0])
        for extra in refs_sorted[1:]:
            extra_name = ctx.names.get(extra, extra)
            print(
                f"  {verb('duplicate')} {extra_name} → {extra}  (keeping {keep_name})",
                flush=True,
            )
            if not ctx.dry:
                cmux_close_workspace_best_effort(extra)

    feature_wt_paths = {wt.path.resolve() for wt in ctx.wts if not wt.is_primary}
    # Root-membership test mirrors `_repo_owned_refs`: repo_path plus every
    # `git worktree list` path for this repo (primary included — that command
    # reports every worktree tied to this repo, even a bare repo's siblings
    # living outside `repo_path`). A cwd under none of these belongs to some
    # other watched repo (or is unresolvable).
    own_roots = {ctx.repo_path.resolve()} | {wt.path.resolve() for wt in ctx.wts}

    def _is_own_repo(cwd: Path) -> bool:
        resolved = cwd.resolve()
        return any(parent in own_roots for parent in (resolved, *resolved.parents))

    def _group_key(ref: str, ws_name: str) -> object:
        cwd = ctx.cwds.get(ref)
        if cwd is not None and cwd.resolve() in feature_wt_paths:
            return cwd.resolve()
        return ws_name

    groups: dict[object, list[str]] = {}
    for ref, ws_name in ctx.names.items():
        cwd = ctx.cwds.get(ref)
        if cwd is None or not _is_own_repo(cwd):
            # Missing cwd, or resolves outside this repo entirely — belongs to
            # another repo (or is unresolvable). Never group it by name, so it
            # can never masquerade as a duplicate of one of ours.
            continue
        groups.setdefault(_group_key(ref, ws_name), []).append(ref)
    keep_refs: set[str] = set()
    for refs in groups.values():
        refs_sorted = sorted(refs, key=_ref_pid)
        keep_refs.add(refs_sorted[0])
        _close_extras(refs_sorted)
    return keep_refs


def _refresh_tracked_pills(
    ctx: RepoCycle, keep_refs: set[str]
) -> tuple[bool, list, list]:
    """Refresh PR-pill state for tracked workspaces, nudge actionable issues.

    Returns (printed_refresh, mine_items, others_items). Items are reused by
    the post-loop summary printer.
    """
    tracked_kept = [
        (ref, pr, wt) for ref, (pr, wt) in ctx.tracked.items() if ref in keep_refs
    ]
    mine_items = sorted(
        (t for t in tracked_kept if t[1].author == ctx.self_user),
        key=lambda t: -t[1].number,
    )
    others_items = sorted(
        (t for t in tracked_kept if t[1].author != ctx.self_user),
        key=lambda t: -t[1].number,
    )

    printed_refresh = False
    for group_label, group in (("mine", mine_items), ("coworkers", others_items)):
        group_header_printed = False
        for ref, pr, wt in group:
            # `label` is the workspace's *current* cmux name; `wt.workspace_name`
            # is the `[<repo>] <branch>` name we re-assert it to.
            label = ctx.names.get(ref, ref)
            if rename_workspace_if_needed(ref, wt.workspace_name, label, dry=ctx.dry):
                if not group_header_printed:
                    print(f"  {dim(group_label)}", flush=True)
                    group_header_printed = True
                print(
                    f"    {verb('renamed')} {cyan(label)} → {cyan(wt.workspace_name)}",
                    flush=True,
                )
                printed_refresh = True
                label = wt.workspace_name  # corrected name for this cycle's log lines
            pref = ctx.prefs.get(pr.number)
            pr_payload = ctx.pr_payloads.get(pr.branch)
            if pr_payload and pr_payload.get("reusedBranch"):
                # Branch reused for new local work after its PR merged/closed —
                # clear the stale merged pills so the card shows no PR, and skip
                # nudging (a merged PR is never actionable). The persisted flag
                # is the daemon's single reused-branch decision (see
                # `_write_pr_caches` / `write_pr_cache`).
                blank: frozenset = frozenset()
                changed = ctx.pill_state.get(ref) != blank
                if changed and not ctx.dry:
                    clear_pr_pills(ref)
                if changed:
                    if not group_header_printed:
                        print(f"  {dim(group_label)}", flush=True)
                        group_header_printed = True
                    print(
                        f"    {verb('suppressed')} {blue(f'#{pr.number}')} → "
                        f"{cyan(label)}  {dim('(branch reused — merged PR hidden)')}",
                        flush=True,
                    )
                    printed_refresh = True
                if changed and not ctx.dry:
                    ctx.pill_state[ref] = blank
                continue
            desired = frozenset(status_pills(pr, wt, ctx.self_user, pref))
            changed = ctx.pill_state.get(ref) != desired
            if changed and not ctx.dry:
                apply_pills(ref, pr, wt, ctx.self_user, pref)
            if changed:
                if not group_header_printed:
                    print(f"  {dim(group_label)}", flush=True)
                    group_header_printed = True
                op = " rebasing" if wt.rebasing else (" merging" if wt.merging else "")
                tag = pr.display_issue + op
                print(
                    f"    {verb('refreshed')} {blue(f'#{pr.number}')} → {cyan(label)}  "
                    f"[{issue_color(pr.display_issue)(tag)}]",
                    flush=True,
                )
                printed_refresh = True
            if changed and not ctx.dry:
                ctx.pill_state[ref] = desired
            # A merged/closed PR is never actionable: its CI, comments, and
            # conflicts can no longer be resolved, so nudging an idle session to
            # "fix CI" on it loops forever (the nudge never stops because the
            # state never changes). A merged PR can still be tracked here when
            # _maybe_autoclose kept its worktree (e.g. merged with red CI) — the
            # per-branch query refreshes any-state PRs into the cache. `nudge_issue`
            # encodes the OPEN-gate so the footer pill still shows the
            # merged-with-red-CI state for inspection, but no nudge fires; it is
            # also the single source the `pr-nudge` flat cell (TUI 🔔) reads, so
            # the bell never disagrees with whether a nudge would fire.
            actionable = bool(pr.nudge_issue)
            if actionable:
                maybe_nudge(
                    ref,
                    f"PR #{pr.number}: {_NUDGE_DESC[pr.display_issue](pr)}.",
                    ctx.dry,
                    label,
                    pr_number=pr.number,
                )
            _track_dev_done(ctx, ref, ctx.linear_blocks.get(pr.branch))
    return printed_refresh, mine_items, others_items


def _print_tracked_summary(
    ctx: RepoCycle, mine_items: list, others_items: list
) -> None:
    for group_label, group in (("mine", mine_items), ("coworkers", others_items)):
        labels = sorted(ctx.names.get(ref, ref) for ref, _, _ in group)
        if labels:
            print(
                f"  {verb('tracked')} {dim(group_label)}: "
                f"{', '.join(cyan(lbl) for lbl in labels)}",
                flush=True,
            )


def _handle_orphans_and_close_stale(ctx: RepoCycle, keep_refs: set[str]) -> None:
    """Apply orphan pills to every surviving workspace whose worktree branch has
    no open PR. Worktrees are never closed here — a merged PR is the only reaper
    (`_maybe_autoclose`), so a research/planning worktree survives until the user
    closes it (TUI `c`). Mine-prefixed branches also get the "open a PR or close"
    nudge; coworker branches (someone else's PR I'm reviewing locally) get the
    pills only — nudging a coworker branch to open a PR makes no sense.
    """
    wt_by_name = {wt.workspace_name: wt for wt in ctx.wts}
    wt_by_path = {wt.path.resolve(): wt for wt in ctx.wts}
    pr_branches = {pr.branch for pr in ctx.prs}
    my_prefix = f"{ctx.self_user}/"
    for ref in keep_refs:
        ws_name = ctx.names.get(ref, "")
        wt_opt = _resolve_wt(ref, ws_name, ctx.cwds, wt_by_path, wt_by_name)
        if (
            wt_opt is None
            or wt_opt.branch in pr_branches
            or wt_opt.branch in MAIN_BRANCHES
        ):
            continue
        wt = wt_opt
        _refresh_orphan(ctx, ref, wt, ws_name, nudge=wt.branch.startswith(my_prefix))


def _refresh_orphan(
    ctx: RepoCycle, ref: str, wt: Worktree, ws_name: str, *, nudge: bool = True
) -> None:
    """Apply orphan/wip/stale pills; nudge to push-or-close when `nudge` is set."""
    if _is_post_merge_stale(wt, ctx.merged_branches):
        print(
            f"  {verb('orphan')} {dim(f'{ws_name} ({wt.branch}) merged — autoclose may handle')}",
            flush=True,
        )
        return
    if rename_workspace_if_needed(ref, wt.workspace_name, ws_name, dry=ctx.dry):
        print(
            f"  {verb('renamed')} {cyan(ws_name)} → {cyan(wt.workspace_name)}",
            flush=True,
        )
        ws_name = wt.workspace_name
    behind_base = ctx.base_distance.get(wt.branch, 0)
    if not ctx.dry:
        cmux(
            "set-status",
            ORPHAN_KEY,
            ORPHAN_ICON,
            "--workspace",
            ref,
            "--color",
            ORANGE,
            check=False,
        )
        apply_wip_pill(ref, wt.dirty_count)
        apply_stale_pill(ref, behind_base)
    orphan_snap, tag = _orphan_snapshot(wt, behind_base)
    changed = ctx.pill_state.get(ref) != orphan_snap
    if changed:
        print(
            f"  {verb('refreshed')} {cyan(ws_name)} → {ref}  [{yellow(tag)}]",
            flush=True,
        )
    if changed and not ctx.dry:
        ctx.pill_state[ref] = orphan_snap
    if nudge:
        grace = orphan_nudge_grace_seconds(ctx.cfg, ctx.repo_entry)
        age = worktree_age_seconds(wt.path)
        if grace > 0 and age < grace:
            reason = (
                f"nudge {ws_name} ({wt.branch}) — worktree {age / 3600:.1f}h old "
                f"< {grace / 3600:.0f}h grace"
            )
            print(f"  {verb('skip')} {dim(reason)}", flush=True)
            return
        maybe_nudge(
            ref,
            f"Worktree {wt.short} on {wt.branch} still has no open PR. "
            f"Push commits and open a PR, or close the worktree if abandoned.",
            ctx.dry,
            ws_name,
        )


_SPAWN_LOG = COCKPIT_HOME / "spawn.log"
# Suppress a re-spawn of the same branch for two slow ticks (default 300s each)
# so a manual SIGUSR1 kick can't double-launch while a `git fetch` +
# worktree add is still in flight. Expires so a failed creation is retried.
_SPAWN_INFLIGHT_TTL_SECONDS = 600


def _bg_spawn_pr(
    ctx: RepoCycle, repo_name: str | None, number: int, branch: str, *, review: bool
) -> None:
    """Fire `cockpit new --pr <n> [--repo <name>] [--review --review-command …]`
    detached so the slow tick never blocks on `git fetch` + worktree add.

    Under `review=True` the per-repo `review_command` (default `/review`,
    e.g. `/pr-review`) rides along so the worktree's first turn runs that review.

    Invoked via module dispatch (`python -m cockpit.cli new …`), NOT `spawn.py`
    by path: a path invocation puts the package dir on `sys.path[0]`, where
    `cockpit.py` shadows the `cockpit` package and the child dies on
    `ModuleNotFoundError: 'cockpit' is not a package` before doing anything.

    The child reuses the exact path `/cockpit:new` walks (create_worktree +
    spawn_pr_workspace), then the new worktree surfaces as cells on a later
    cycle — inventory is derived, not stored (see AGENTS.md). `--repo` is passed
    when the config entry has a name; otherwise the child's cwd-based discovery
    resolves the repo from `ctx.repo_path`. An in-flight guard keyed by branch
    in `pill_state` keeps back-to-back ticks from double-spawning; stderr/stdout
    land in `spawn.log` so detached failures are not silent.
    """
    key = f"spawn:{ctx.owner}/{ctx.name}:{branch}"
    last = ctx.pill_state.get(key)
    now = time.monotonic()
    if isinstance(last, float) and (now - last) < _SPAWN_INFLIGHT_TTL_SECONDS:
        return
    label = "bg-review" if review else "bg-spawn"
    if ctx.dry:
        print(f"  [dry] {label} #{number} branch={branch}", flush=True)
        return
    cmd = [sys.executable, "-m", "cockpit.cli", "new", "--pr", str(number)]
    if repo_name:
        cmd += ["--repo", repo_name]
    if review:
        cmd += ["--review", "--review-command", review_command(ctx.cfg, ctx.repo_entry)]
    logfile: IO[bytes] | None = None
    try:
        logfile = open(_SPAWN_LOG, "ab")  # noqa: SIM115 — handle is passed to a detached Popen and must outlive this scope
    except OSError:
        logfile = None
    sink: IO[bytes] | int = logfile if logfile is not None else subprocess.DEVNULL
    try:
        subprocess.Popen(
            cmd,
            cwd=str(ctx.repo_path),
            stdout=sink,
            stderr=sink,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        print(
            f"  {yellow('warn')} {label} #{number}: failed to launch spawn.py: {e}",
            file=sys.stderr,
            flush=True,
        )
        return
    finally:
        if logfile is not None:
            logfile.close()
    ctx.pill_state[key] = now
    print(
        f"  {verb(label)} {bold(branch)}  #{number}"
        + (f"  {dim('(review)')}" if review else ""),
        flush=True,
    )


def _spawn_missing_workspaces(ctx: RepoCycle, repo_entry: dict) -> None:
    """Spawn/create the workspaces and worktrees a cycle is missing:

    1. PR-matched worktrees that lack a cmux workspace → spawn one.
    2. My open PRs with no local worktree → create worktree + workspace in the
       background (replaces the old "create one with /cockpit:new" warning).
    3. `review_prs`: every other-authored open PR without a worktree → create a
       review worktree (`spawn.py --review`) in the background. Uncapped.
    4. My-prefix orphan worktrees not yet covered by any workspace → spawn one.

    An `in_place` repo (registered via bare `cockpit new`) opts out of all
    auto-spawning: its row still renders from `git worktree list` + the cell
    writers, but the user works in-place on the main worktree and never wants
    cockpit creating PR/orphan worktrees for it, so this returns early.
    """
    if repo_entry.get("in_place"):
        return
    repo_name = repo_entry.get("name")
    matched, skipped_self = match_worktrees(ctx.prs, ctx.wts, ctx.self_user)
    for pr in skipped_self:
        _bg_spawn_pr(ctx, repo_name, pr.number, pr.branch, review=False)
    tracked_pr_numbers = {pr.number for pr, _ in ctx.tracked.values()}
    for pr, wt in matched:
        if pr.number not in tracked_pr_numbers:
            spawn_pr_workspace(
                pr,
                wt,
                self_user=ctx.self_user,
                pref=ctx.prefs.get(pr.number),
                dry=ctx.dry,
            )
    if ctx.review_candidates:
        # Dependabot PRs are coworker-authored, so they'd flow through the
        # review-spawn path — but auto-creating a review worktree per dep bump is
        # noise. Opt in per-repo with `dependabot: true`; default is to skip them.
        allow_dependabot = bool(repo_entry.get("dependabot"))
        existing_branches = {w.branch for w in ctx.wts}
        for cand in ctx.review_candidates:
            if cand.author == ctx.self_user:
                continue  # mine — handled by skipped_self above
            if not allow_dependabot and is_dependabot(cand.author):
                continue  # dependabot PR, `dependabot` flag off — don't spawn
            if cand.branch in existing_branches:
                continue  # already have a worktree — tracked via the matched path
            _bg_spawn_pr(ctx, repo_name, cand.number, cand.branch, review=True)
    pr_branches = {pr.branch for pr in ctx.prs}
    my_prefix = f"{ctx.self_user}/"
    covered_paths = {p.resolve() for p in ctx.cwds.values()}
    # Live workspace names → the existing cwd(s) using each. A same-named
    # workspace rooted at a DIFFERENT, still-existing path is a cross-repo clash
    # (two repos each with a `foo` branch): spawning here would create a
    # duplicate-named workspace that churns every cycle, since cmux allows
    # duplicate names and the path-keyed dedup above never covers this path.
    # Dead-cwd workspaces are excluded — `close_gone_cwd_workspaces` reaps them,
    # so they must not suppress a legitimate spawn.
    name_to_paths: dict[str, set[Path]] = {}
    for ref, ws_name in ctx.names.items():
        cwd = ctx.cwds.get(ref)
        if cwd is None:
            continue
        resolved = cwd.resolve()
        if resolved.exists():
            name_to_paths.setdefault(ws_name, set()).add(resolved)
    for wt in ctx.wts:
        if not wt.branch.startswith(my_prefix) or wt.branch in pr_branches:
            continue
        if wt.path.resolve() in covered_paths:
            continue
        if _is_post_merge_stale(wt, ctx.merged_branches):
            print(
                f"  {verb('skip')} {dim(f'orphan-spawn {wt.short} — branch {wt.branch} has merged PR')}",
                flush=True,
            )
            continue
        clash = name_to_paths.get(wt.workspace_name, set()) - {wt.path.resolve()}
        if clash:
            other = sorted(str(p) for p in clash)[0]
            print(
                f"  {verb('skip')} {dim(f'orphan-spawn {wt.workspace_name} — workspace name already used by {other}')}",
                flush=True,
            )
            continue
        spawn_orphan_workspace(wt, dry=ctx.dry)


def _resolve_skill_prompt(name: str, repo_path: Path) -> str | None:
    """Return the slash-command prompt for a skill, or None if not found.

    Lookup order mirrors `spawn.resolve_skill` (global always wins):
      1. ~/.claude/skills/<name>/skill.md
      2. <repo_path>/.claude/skills/<name>/skill.md

    `repo_path` is the managed repo running the skill — its `.claude/skills/`,
    NOT cockpit's own plugin tree (skills are configured per managed repo and
    run in that repo's worktree).
    """
    rel = Path(".claude") / "skills" / name / "skill.md"
    if (Path.home() / rel).exists():
        return f"/{name}"
    if (repo_path / rel).exists():
        return f"/{name}"
    return None


def _run_repo_skills(repo_entry: dict, *, dry: bool) -> None:
    """Run fast_skills (blocking, non-interactive) and slow_skills (workspace spawn)
    configured on the repo entry.

    fast_skills: `claude -p /<name>` in the repo's main worktree — completes inline.
    slow_skills: cmux new-workspace with `claude /<name>` — idempotent by workspace name.
    """
    repo_path = Path(repo_entry["path"]).expanduser().resolve()

    for skill in repo_entry.get("fast_skills") or []:
        prompt = _resolve_skill_prompt(skill, repo_path)
        if prompt is None:
            print(
                f"  {yellow('skip')} fast_skill {skill!r}: skill.md not found",
                flush=True,
            )
            continue
        if dry:
            print(f"  dry: claude -p {shell_quote(prompt)} in {repo_path}", flush=True)
            continue
        subprocess.run(
            f"claude -p {shell_quote(prompt)}",
            shell=True,
            cwd=repo_path,
        )

    slow_skills = repo_entry.get("slow_skills") or []
    if slow_skills:
        try:
            existing = set(workspace_names().values())
        except CmuxUnavailable:
            return
    for skill in slow_skills:
        prompt = _resolve_skill_prompt(skill, repo_path)
        if prompt is None:
            print(
                f"  {yellow('skip')} slow_skill {skill!r}: skill.md not found",
                flush=True,
            )
            continue
        ws_name = f"skill-{skill}"
        if ws_name in existing:
            continue
        initial, followup = split_prompt_prefix(prompt)
        if dry:
            extra = f" + followup {followup!r}" if followup else ""
            print(
                f"  dry: spawn workspace {ws_name!r} with "
                f"{claude_command(initial)!r}{extra}",
                flush=True,
            )
            continue
        ref = spawn_workspace(ws_name, repo_path, claude_command(initial))
        if ref is not None and followup:
            deliver_followup(ref, followup)


def _repo_owned_refs(ctx: RepoCycle, keep_refs: set[str]) -> list[str]:
    """Surviving workspace refs whose cwd sits inside this repo (its main
    worktree or any feature worktree). Scopes the global workspace list down
    to the repo being cycled — `ctx.cwds` spans every repo's workspaces.
    """
    roots = {ctx.repo_path.resolve()} | {wt.path.resolve() for wt in ctx.wts}
    owned: list[str] = []
    for ref in keep_refs:
        cwd = ctx.cwds.get(ref)
        if cwd is None:
            continue
        resolved = cwd.resolve()
        if any(parent in roots for parent in (resolved, *resolved.parents)):
            owned.append(ref)
    return owned


def _apply_repo_colors(ctx: RepoCycle, repo_entry: dict, keep_refs: set[str]) -> None:
    """Tint this repo's workspace sidebar entries with its `sidebar_color`.

    Optional per-repo `sidebar_color` (a cmux `WORKSPACE_COLORS` name); unset
    → no-op, so repos that don't set it keep cmux's default. Deduped via
    `pill_state` under a `color:<ref>` key so cmux is only touched when a
    workspace's color actually changes, and re-applied once after a daemon
    restart (when `pill_state` is empty). cmux-only — the wrapper no-ops on
    limux. A manual `clear-color` won't be re-tinted until the next restart.

    `sidebar_color` is validated at preflight (`_validate_sidebar_colors`), so
    a value reaching here is already a known cmux color.
    """
    color = repo_entry.get("sidebar_color")
    if not color or ctx.dry:
        return
    for ref in _repo_owned_refs(ctx, keep_refs):
        if ctx.pill_state.get(f"color:{ref}") == color:
            continue
        set_workspace_color(ref, color)
        ctx.pill_state[f"color:{ref}"] = color


def _reconcile_worktree_lifecycle(ctx: RepoCycle, *, dry: bool) -> None:
    """Backend-agnostic worktree teardown: reap merged-clean worktrees + stale
    local branch refs. Runs on every backend — git + a best-effort workspace
    close (`check=False`: real on cmux/limux, no-op on 'none'); its lone pill
    side effect (`apply_devdone_pill`) no-ops on limux.
    """
    _maybe_autoclose(
        ctx.repo_path,
        ctx.name,
        ctx.wts,
        ctx.merged_branches,
        ctx.cwds,
        prs=ctx.prs,
        dry=dry,
    )
    _reap_branch_refs(ctx)


def cycle_repo(
    repo_entry: dict,
    self_user: str,
    *,
    dry: bool,
    pr_cache: dict,
    pill_state: dict,
    cfg: dict,
) -> None:
    ctx = _prepare_cycle(
        repo_entry,
        self_user,
        cfg=cfg,
        pr_cache=pr_cache,
        pill_state=pill_state,
        dry=dry,
    )
    if ctx is None:
        return
    _write_pr_caches(ctx)

    # Per-step backend gating in one fixed order (identical to pre-limux cmux
    # behaviour; non-cmux backends just skip tiers they can't run). Three tiers —
    # full rationale in docs/state-machine.md:
    #   • cmux-only (`not ctx.headless`): pills / colors / dedup (cmux PID refs).
    #   • workspace-capable (`workspaces_ready`): spawn / skills (cmux + limux).
    #   • backend-agnostic (unconditional): Linear / autoclose / reap / ff.
    workspaces_ready = has_workspace_backend() and ctx.workspace_state_ok
    if not ctx.headless:
        keep_refs = _dedupe_workspaces(ctx)
        printed_refresh, mine_items, others_items = _refresh_tracked_pills(
            ctx, keep_refs
        )
        if ctx.tracked and not printed_refresh:
            _print_tracked_summary(ctx, mine_items, others_items)
        _handle_orphans_and_close_stale(ctx, keep_refs)
        _apply_repo_colors(ctx, repo_entry, keep_refs)
    if workspaces_ready:
        _spawn_missing_workspaces(ctx, repo_entry)
    _transition_merged_tickets(ctx)
    _reconcile_worktree_lifecycle(ctx, dry=dry)
    log_ff_advances(
        ff_default_branch_worktrees(
            ctx.repo_path, ctx.wts, default=ctx.default_branch, dry=dry
        ),
        dry=dry,
    )
    if workspaces_ready:
        _run_repo_skills(repo_entry, dry=dry)


def _drain_close_requests(dry: bool) -> None:
    """Process pending close markers (enqueued by the TUI's `c`/`C` actions and
    autoclose) through the shared teardown.

    Refused markers (blockers reappeared between probe and drain) are dropped
    with a log line — the user re-presses `C` in the TUI to force-retry.
    """
    daemon_signal.prune_stale()
    for path, req in daemon_signal.iter_pending():
        ok, blockers = teardown(req, dry=dry)
        if ok:
            if not dry:
                daemon_signal.pop(path)
            continue
        label = req.name or req.ref
        print(
            f"  {verb('refused', color=yellow)} {label}: " + "; ".join(blockers),
            file=sys.stderr,
            flush=True,
        )
        if not dry:
            daemon_signal.pop(path)


def _reap_workspace_orphans(repos: list[dict], self_user: str, *, dry: bool) -> None:
    """Close cockpit-owned workspaces whose worktree no longer exists.

    Ownership is derived from cwd: a workspace is cockpit's iff its cwd
    resolves under a registered repo's path or one of its live worktrees.
    Workspaces outside every registered repo are ignored entirely.

    Within owned workspaces, a stranded one (no matching live worktree by
    cwd or name) is enqueued for tear-down — but only when Claude is idle.
    If Claude is mid-turn the reap is deferred to the next cycle so we
    don't yank the session out from under an active turn. Every owned orphan
    is reaped; the mine-prefix check only gates whether the stale local branch
    ref is also deleted — a coworker-spawned branch ref is left in place.
    """
    all_wts: list[Worktree] = []
    repo_lookup: dict[Path, tuple[str, Path]] = {}
    registered_roots: dict[Path, tuple[str, Path]] = {}
    for entry in repos:
        repo_path = Path(os.path.expanduser(entry["path"]))
        if not repo_path.is_dir():
            continue
        repo_name = entry.get("name") or repo_path.name
        registered_roots[repo_path.resolve()] = (repo_name, repo_path)
        try:
            # Identity only (path/branch) — skip the dirty/unpushed stat forks.
            for wt in worktrees_basic(
                repo_path, entry.get("branch_prefix", ""), entry.get("name", "")
            ):
                all_wts.append(wt)
                repo_lookup[wt.path.resolve()] = (repo_name, repo_path)
        except RuntimeError:
            continue

    wt_by_path = {wt.path.resolve(): wt for wt in all_wts}
    wt_by_name = {wt.workspace_name: wt for wt in all_wts}

    names, cwds = workspace_state()
    my_prefix = f"{self_user}/"

    def _owning_repo(cwd: Path | None) -> tuple[str, Path] | None:
        if cwd is None:
            return None
        for parent in [cwd, *cwd.parents]:
            resolved = parent.resolve()
            hit = repo_lookup.get(resolved) or registered_roots.get(resolved)
            if hit:
                return hit
        return None

    for ref, ws_name in names.items():
        cwd = cwds.get(ref)
        wt_opt = _resolve_wt(ref, ws_name, cwds, wt_by_path, wt_by_name)
        if wt_opt is not None:
            continue
        owner = _owning_repo(cwd)
        if owner is None:
            continue
        repo_name, repo_path = owner
        label = ws_name or ref
        if not workspace_is_idle(ref):
            print(
                f"  {verb('defer')} {dim(f'reap workspace {label} ({ref}) — not idle (Claude mid-turn)')}",
                flush=True,
            )
            continue
        last_known_branch = ws_name if ws_name.startswith(my_prefix) else None
        req = TeardownRequest(
            ref=ref,
            name=ws_name,
            worktree_path=None,
            branch=last_known_branch,
            repo_path=repo_path,
            repo_name=repo_name,
            forced=True,
        )
        action = "[dry] reap" if dry else "reap"
        print(
            f"  {verb(action)} {dim(f'orphan workspace {label} ({ref}) — no matching worktree (cwd={cwd})')}",
            flush=True,
        )
        if not dry:
            daemon_signal.enqueue(req)


# Re-query the install repo for a newer version at most hourly — the running
# version can't change mid-daemon-run, so checking every slow tick (300s) just
# spends a `gh api` call. The per-version log guard below caps noise further.
_UPDATE_CHECK_TTL_SECONDS = 3600


def _check_plugin_update(cfg: dict, pill_state: dict) -> None:
    """Log once when a newer cockpit is published on the install repo's default
    branch. Gated on `check_update` (default true), throttled to one `gh` query
    per `_UPDATE_CHECK_TTL_SECONDS` and one log line per newer version — both
    keyed in `pill_state` like the spawn in-flight guard. Daemon-wide (not
    per-repo), so it runs before the repo loop. Any fetch failure logs nothing
    (see lib.version).
    """
    if not cfg.get("check_update", True):
        return
    now = time.monotonic()
    last = pill_state.get("update-check:ts")
    if isinstance(last, float) and (now - last) < _UPDATE_CHECK_TTL_SECONDS:
        return
    pill_state["update-check:ts"] = now
    running = version.running_version()
    latest = version.latest_version()
    if not latest or not version.is_newer(latest, running):
        return
    if pill_state.get("update-check:warned") == latest:
        return
    pill_state["update-check:warned"] = latest
    ts = datetime.now().isoformat(timespec="seconds")
    print(
        f"[{ts}] {yellow('cockpit:')} update available\n"
        f"  {running} -> {latest} (run /plugin update cockpit)",
        flush=True,
    )


def cycle_all(
    cfg: dict,
    self_user: str,
    *,
    dry: bool,
    pr_cache: dict,
    pill_state: dict,
    on_repo_done: Callable[[], None] | None = None,
    only_repo: str | None = None,
) -> None:
    """Reconcile every managed repo, serially.

    `cycle_repo` writes each repo's cache cells to disk before the next repo is
    fetched, so a renderer reading those cells can surface a finished repo while
    later repos are still doing `gh` round-trips. `on_repo_done`, if given, is
    invoked after each repo (success or caught error) to let the caller republish
    the table incrementally instead of waiting for the whole tick. It is a pure
    read-side hook — it must never write a cache cell (only the daemon does).

    `only_repo` (a repo path) scopes the cycle to the single matching repo — the
    TUI passes it so a row keypress (mute/close/spawn/open) refreshes just that
    row's repo without round-tripping `gh` for every other repo. A scoped run
    still drains the close queue (a `c`/`C` teardown lands there) but skips the
    repo-spanning sweeps (`close_gone_cwd_workspaces`, `_reap_workspace_orphans`)
    and the plugin-update check — those are global housekeeping the next full
    periodic tick handles, not work the keypress is waiting on."""
    ensure_state_dirs()
    repos = cfg.get("repos", [])
    if not repos:
        print(
            f"  {yellow('no managed repos')} — register one via /cockpit:new in a git repo",
            flush=True,
        )
        return
    if only_repo is not None:
        want = Path(os.path.expanduser(only_repo)).resolve()
        repos = [
            e for e in repos if Path(os.path.expanduser(e["path"])).resolve() == want
        ]
        if not repos:
            return  # unknown repo path — nothing scoped to reconcile
    else:
        _check_plugin_update(cfg, pill_state)
    # Worktree teardown drains on every backend — the TUI `c`/`C` close path
    # (which enqueues here) was dead on limux/none until now.
    _drain_close_requests(dry=dry)
    if only_repo is None and has_workspace_backend():
        try:
            close_gone_cwd_workspaces(dry=dry)
        except CmuxUnavailable as e:
            ts = datetime.now().isoformat(timespec="seconds")
            print(
                f"[{ts}] {yellow('skip')} close_gone_cwd_workspaces: cmux unavailable: {e}",
                file=sys.stderr,
                flush=True,
            )
    for repo_entry in repos:
        try:
            cycle_repo(
                repo_entry,
                self_user,
                dry=dry,
                pr_cache=pr_cache,
                pill_state=pill_state,
                cfg=cfg,
            )
        except (RuntimeError, subprocess.SubprocessError, OSError) as e:
            ts = datetime.now().isoformat(timespec="seconds")
            print(
                f"[{ts}] cycle error for {repo_entry.get('name')}: {e}\n"
                f"{traceback.format_exc()}",
                file=sys.stderr,
                flush=True,
            )
        # Surface this repo's freshly-written cells before fetching the next one.
        # A failing callback must not abort the remaining repos.
        if on_repo_done is not None:
            try:
                on_repo_done()
            except Exception as e:  # noqa: BLE001 — a render hiccup can't stop the tick
                ts = datetime.now().isoformat(timespec="seconds")
                print(
                    f"[{ts}] {yellow('skip')} on_repo_done for "
                    f"{repo_entry.get('name')}: {e}",
                    file=sys.stderr,
                    flush=True,
                )
    # Orphan-workspace reap stays cmux-only: its idle-safety gate
    # (`workspace_is_idle`) reads the `idle=` pill that only cmux's Stop hook
    # writes, so on limux it is always absent and the reaper would defer every
    # orphan forever. `close_gone_cwd_workspaces` (cwd-existence, no idle gate)
    # is the limux-safe workspace reaper; this one needs the idle signal.
    if only_repo is None and not _cache_only(cfg):
        try:
            _reap_workspace_orphans(repos, self_user, dry=dry)
        except CmuxUnavailable as e:
            ts = datetime.now().isoformat(timespec="seconds")
            print(
                f"[{ts}] {yellow('skip')} _reap_workspace_orphans: cmux unavailable: {e}",
                file=sys.stderr,
                flush=True,
            )
