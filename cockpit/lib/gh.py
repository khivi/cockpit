"""GitHub (gh CLI + GraphQL) helpers and the PR dataclass."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import run


def require_gh() -> None:
    """Exit cleanly with a one-liner if the gh CLI is not on PATH.

    Mirrors `lib.cmux.require_workspace_binary`: surfaces a structured
    install hint at startup instead of letting a cryptic FileNotFoundError
    surface deep inside a daemon cycle.
    """
    try:
        subprocess.run(["gh", "--version"], capture_output=True, check=False)
    except FileNotFoundError:
        print(
            "cockpit: `gh` CLI not found on PATH — install from https://cli.github.com",
            file=sys.stderr,
        )
        sys.exit(2)


def gh_json(args: list[str]) -> dict | list:
    data: dict | list = json.loads(run(["gh", *args]))
    return data


def default_branch(repo: Path) -> str:
    """GitHub default branch for `repo`, with git symbolic-ref fallback when offline."""
    res = subprocess.run(
        [
            "gh",
            "repo",
            "view",
            "--json",
            "defaultBranchRef",
            "--jq",
            ".defaultBranchRef.name",
        ],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    out = run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        check=False,
    ).strip()
    return out.removeprefix("origin/") if out else "main"


def gh_self_user() -> str:
    """Resolve the current authenticated GitHub user via `gh api user`.

    Cockpit does not hardcode usernames; cycle() needs it to distinguish
    self-authored PRs from coworker PRs.
    """
    return run(["gh", "api", "user", "--jq", ".login"]).strip()


_MERGED_BRANCHES_QUERY = (
    "query ($search: String!, $cursor: String) {\n"
    "  search(query: $search, type: ISSUE, first: 100, after: $cursor) {\n"
    "    pageInfo { endCursor hasNextPage }\n"
    "    nodes { ... on PullRequest { number headRefName headRefOid } }\n"
    "  }\n"
    "}"
)


def fetch_merged_branches(
    owner: str,
    name: str,
    *,
    cutoff_days: int = 14,
    max_pages: int = 10,
) -> dict[str, str]:
    """Map branch → head SHA at merge for PRs merged in the last `cutoff_days`.

    Empty dict on gh failure. `headRefOid` is the commit the PR pointed at when
    it merged; callers use it to distinguish "branch unchanged since merge"
    from "branch advanced after merge", which `git cherry` cannot do for
    squash-merged PRs (the squash collapses N commits into 1 with a combined
    patch-id that matches none of the originals).

    Paginated server-side via the `merged:>=<date>` search qualifier so the
    window scales with merge cadence — a fixed limit dropped the user's own
    freshly-merged PRs out of the autoclose set on high-cadence repos. The
    `max_pages` cap (10 × 100 = 1 000 PRs) keeps a runaway repo from
    monopolizing the tick.

    When a branch has been reused across multiple merged PRs (e.g. a branch was
    deleted post-merge then re-created for follow-up work), keep the highest PR
    number — that is the most recent merge, and its headRefOid is the only one
    that should gate autoclose.
    """
    cutoff = (datetime.now(UTC) - timedelta(days=cutoff_days)).strftime("%Y-%m-%d")
    search = f"repo:{owner}/{name} is:pr is:merged merged:>={cutoff}"
    latest: dict[str, tuple[int, str]] = {}
    cursor: str | None = None
    for _ in range(max_pages):
        variables: dict[str, str] = {"search": search}
        if cursor:
            variables["cursor"] = cursor
        try:
            data = _graphql(_MERGED_BRANCHES_QUERY, variables)
        except subprocess.CalledProcessError:
            return {}
        try:
            page = data["data"]["search"]
            for node in page["nodes"]:
                if not node:
                    continue
                branch = node["headRefName"]
                num = node["number"]
                oid = node["headRefOid"]
                if branch not in latest or num > latest[branch][0]:
                    latest[branch] = (num, oid)
            info = page["pageInfo"]
            if not info["hasNextPage"]:
                break
            cursor = info["endCursor"]
        except (KeyError, TypeError):
            return {}
    return {branch: oid for branch, (_, oid) in latest.items()}


_OPEN_PR_HEADS_QUERY = (
    "query ($search: String!, $cursor: String) {\n"
    "  search(query: $search, type: ISSUE, first: 100, after: $cursor) {\n"
    "    pageInfo { endCursor hasNextPage }\n"
    "    nodes { ... on PullRequest { number headRefName author { login } } }\n"
    "  }\n"
    "}"
)


@dataclass(frozen=True)
class OpenPRHead:
    """Minimal identity for an open PR: enough to fetch its head and decide
    whether a review worktree already exists. Deliberately lighter than `PR` —
    the `review_prs` spawn decision only needs (number, branch, author).
    """

    number: int
    branch: str
    author: str


def list_open_pr_heads(owner: str, name: str) -> list[OpenPRHead]:
    """Every open PR in the repo as (number, head branch, author login).

    Used only by the per-repo `review_prs` spawn decision in the daemon's slow
    tick — the daemon's normal PR query is `author:self` plus per-worktree
    aliases, so other-authored PRs without a local worktree are invisible to it
    without this. Truly uncapped by intent: the pagination loop runs until
    `hasNextPage` is false. GitHub's search API itself caps a single query at
    1 000 results, so the loop always terminates without a page ceiling.

    A null author (bot/Copilot) is reported as "" so the caller can skip or
    include it explicitly. Empty list on gh failure — review-spawn does nothing
    that cycle rather than aborting the whole reconcile.
    """
    search = f"repo:{owner}/{name} is:pr is:open"
    out: list[OpenPRHead] = []
    cursor: str | None = None
    while True:
        variables: dict[str, str] = {"search": search}
        if cursor:
            variables["cursor"] = cursor
        try:
            data = _graphql(_OPEN_PR_HEADS_QUERY, variables)
        except subprocess.CalledProcessError:
            return []
        try:
            page = data["data"]["search"]
            for node in page["nodes"]:
                if not node:
                    continue
                author = (node.get("author") or {}).get("login") or ""
                out.append(OpenPRHead(node["number"], node["headRefName"], author))
            info = page["pageInfo"]
            if not info["hasNextPage"]:
                break
            cursor = info["endCursor"]
        except (KeyError, TypeError):
            return []
    return out


def pr_for_branch(branch: str, repo_dir: Path) -> dict | None:
    """Return {number,title,author,url} for an open PR on `branch`, else None."""
    res = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "open",
            "--limit",
            "1",
            "--json",
            "number,title,author,url",
        ],
        capture_output=True,
        text=True,
        cwd=str(repo_dir),
    )
    if res.returncode != 0:
        return None
    try:
        rows = json.loads(res.stdout)
    except json.JSONDecodeError:
        return None
    return rows[0] if rows else None


def fetch_pr_info(pr_num: str, repo_dir: Path | None = None) -> dict:
    """Fetch {number, title, author, url, headRefName} for a PR."""
    fields = "number,title,author,url,headRefName"
    if repo_dir:
        result = subprocess.run(
            ["gh", "pr", "view", pr_num, "--json", fields],
            capture_output=True,
            text=True,
            cwd=str(repo_dir),
        )
        if result.returncode != 0:
            raise RuntimeError(f"gh pr view failed: {result.stderr.strip()}")
        pr_data: dict = json.loads(result.stdout)
        return pr_data
    data = gh_json(["pr", "view", pr_num, "--json", fields])
    assert isinstance(data, dict)
    return data


def fetch_run_info(
    run_id: str, repo_dir: Path | None = None, *, nwo: str | None = None
) -> dict:
    """Fetch `{databaseId, headBranch, headSha, workflowName, displayTitle,
    conclusion, status, event, url, jobs[]}` for a GitHub Actions run.

    Pass `nwo` (`<owner>/<repo>`) when calling from outside the repo tree —
    the gh call is then routed with `-R <nwo>` so it works without a cwd.
    Each entry in `jobs[]` carries `{databaseId, name, conclusion, status, url}`.
    """
    fields = (
        "databaseId,headBranch,headSha,workflowName,displayTitle,"
        "conclusion,status,event,url,jobs"
    )
    args = ["run", "view", run_id, "--json", fields]
    if nwo:
        args = ["-R", nwo, *args]
    cwd = str(repo_dir) if repo_dir else None
    result = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"gh run view failed: {result.stderr.strip()}")
    run_data: dict = json.loads(result.stdout)
    return run_data


def resolve_pr_branch(pr_num: str, repo_dir: Path | None = None) -> str:
    """Resolve a PR number to its head branch name via gh CLI.

    When `repo_dir` is given, both gh calls run with that as cwd so --repo
    invocations target the right remote even from outside its tree.
    """
    cwd = str(repo_dir) if repo_dir else None

    def _gh(args: list[str]) -> str:
        res = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=cwd)
        return res.stdout.strip()

    nwo = _gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if not nwo:
        raise RuntimeError(f"could not resolve repo for PR #{pr_num}")
    out = _gh(
        ["-R", nwo, "pr", "view", pr_num, "--json", "headRefName", "-q", ".headRefName"]
    )
    if not out:
        raise RuntimeError(f"could not resolve PR #{pr_num} to a branch via gh")
    return out


def repo_nwo(repo_dir: Path) -> tuple[str, str]:
    """(owner, name) from `gh repo view` run inside repo_dir."""
    out = subprocess.run(
        ["gh", "repo", "view", "--json", "owner,name"],
        capture_output=True,
        text=True,
        cwd=str(repo_dir),
    )
    if out.returncode != 0:
        raise RuntimeError(f"gh repo view failed in {repo_dir}: {out.stderr.strip()}")
    data = json.loads(out.stdout)
    return data["owner"]["login"], data["name"]


# The `display_issue` categories that warrant a nudge (and the TUI 🔔). A PR
# whose issue is none of these — `approved`, `clean`, `changes-requested` — is
# never nudged. Lives here, next to `PR.nudge_issue`, so the model is the single
# authority; `cycle` imports it rather than declaring its own copy.
ACTIONABLE_ISSUES = frozenset({"ci", "comments", "conflicts"})


@dataclass
class PR:
    number: int
    title: str
    branch: str
    url: str
    author: str
    is_draft: bool
    review_decision: str
    mergeable: str
    ci: str
    unaddressed: int
    total_from_others: int
    state: str = "OPEN"
    merged_at: str | None = None
    updated_at: str = ""
    # PR description. Carried for the `Linear:` footer that maps a PR to the
    # ticket(s) it delivers (see lib.linear.parse_linear_footers / the devdone
    # pill). Empty when unfetched — only the relevant-PR query selects it.
    body: str = ""
    head_oid: str | None = None

    @property
    def primary_issue(self) -> str:
        if self.unaddressed > 0 or self.review_decision == "CHANGES_REQUESTED":
            return "comments"
        if self.ci.startswith("failed"):
            return "ci"
        if self.mergeable == "CONFLICTING":
            return "conflicts"
        if self.review_decision == "APPROVED":
            return "approved"
        return "clean"

    @property
    def display_issue(self) -> str:
        if (
            self.primary_issue == "comments"
            and self.unaddressed == 0
            and self.review_decision == "CHANGES_REQUESTED"
        ):
            return "changes-requested"
        return self.primary_issue

    @property
    def nudge_issue(self) -> str:
        """The actionable issue category warranting a nudge, or "" when none.

        Single source for both the slow-tick nudge decision
        (`cycle._refresh_tracked_pills`) and the `pr-nudge` flat cell that drives
        the TUI's 🔔 — so the bell can never disagree with whether a nudge would
        actually fire. A nudge fires only for an OPEN PR whose `display_issue` is
        in `ACTIONABLE_ISSUES`: a merged/closed PR is never actionable (its CI,
        comments, and conflicts can no longer be resolved, so nudging loops
        forever — the same reasoning as the OPEN gate that consumes this).
        """
        if self.state == "OPEN" and self.display_issue in ACTIONABLE_ISSUES:
            return self.display_issue
        return ""


_PR_FIELDS = """
  number title body url isDraft headRefName headRefOid mergeable reviewDecision updatedAt state
  author { login __typename }
  baseRef { branchProtectionRule { requiredStatusChecks { context } } }
  reviewThreads(first: 100) {
    nodes {
      isResolved
      comments(first: 100) { nodes { author { login __typename } } }
    }
  }
  reviews(first: 100) { nodes { author { login __typename } state body } }
  commits(last: 1) {
    nodes { commit {
      checkSuites(first: 20) { nodes {
        checkRuns(first: 100) { nodes { name status conclusion } }
      } }
      status { contexts { context state } }
    } }
  }
"""

_PR_LIGHT_FIELDS = "number updatedAt"


def _is_other(author: dict | None, pr_author: str) -> bool:
    """True when `author` is a reviewer other than the PR author.

    Null authors (e.g. Copilot inline review comments) and bot accounts count
    as "other" — their inline code-review threads are actionable. Bot summary
    reviews (the "I reviewed N files" noise) are filtered out separately in
    the reviews loop of _unaddressed, not here.
    """
    if author is None:
        return True
    login = author.get("login")
    return not login or login != pr_author


def _unaddressed(pr_node: dict, pr_author: str) -> tuple[int, int]:
    """Threads and summary reviews awaiting the PR author's response.

    Returns (unresolved, total).

    An inline review thread is unresolved when it isn't resolved and the last
    comment is from someone other than the author. A reviewer's *summary review*
    (feedback in the review body, no inline thread) is unresolved when their
    most recent review is COMMENTED or CHANGES_REQUESTED with a non-empty body —
    a substantive human review like that should light the comments pill even
    when GitHub's reviewDecision stays REVIEW_REQUIRED (COMMENT-type reviews
    don't flip it to CHANGES_REQUESTED).

    GitHub has no "resolve" button for summary reviews, and commit timestamps
    are unreliable as an addressed-signal (rebases rewrite committedDate;
    pushedDate is frequently null), so the only signal we trust is the
    reviewer's own later review: an APPROVED/DISMISSED most-recent review clears
    their earlier feedback. Bot summary reviews are always excluded — only their
    inline threads (counted above) are actionable.
    """
    total = unresolved = 0
    for t in pr_node["reviewThreads"]["nodes"]:
        authors = [c.get("author") for c in t["comments"]["nodes"]]
        non_self = [a for a in authors if _is_other(a, pr_author)]
        if not non_self:
            continue
        total += 1
        last = authors[-1] if authors else None
        if not t["isResolved"] and _is_other(last, pr_author):
            unresolved += 1
    latest_review: dict[str, dict] = {}
    for r in pr_node["reviews"]["nodes"]:
        a = r.get("author") or {}
        if a.get("__typename") == "Bot":
            continue
        login = a.get("login")
        if not login or login == pr_author:
            continue
        latest_review[login] = r  # API order is chronological → last wins
    # Count each reviewer once, by their most-recent review only. A later
    # APPROVED/DISMISSED clears earlier feedback, so an earlier substantive
    # review must not inflate `total` — counting every bodied review would let
    # one reviewer's repeated reviews push the denominator past `unresolved`.
    for r in latest_review.values():
        if not (r.get("body") or "").strip():
            continue
        total += 1
        if r.get("state") in ("COMMENTED", "CHANGES_REQUESTED"):
            unresolved += 1
    return unresolved, total


def _pr_from_node(n: dict) -> PR | None:
    author = (n.get("author") or {}).get("login")
    if not author:
        return None
    commit = (n["commits"]["nodes"] or [{}])[0].get("commit") or {}
    # `checkSuites` is a non-null connection type in GH's GraphQL schema, so an
    # explicit `null` means the resolver errored (e.g. GH Actions outage) — not
    # "no CI configured" (which returns `{"nodes": []}`). When BOTH check
    # sources come back null, surface ci="unknown" so the sidebar/footer show
    # an explicit error indicator instead of pretending no checks exist.
    suites_field = commit.get("checkSuites")
    status_field = commit.get("status")
    if suites_field is None:
        ci = "unknown"
    else:
        # When branch protection declares required checks, that set is the
        # authoritative filter — anything else is noise (lint bots, optional
        # workflows, the copilot reviewer). Absent a rule (no branch protection,
        # or the current token can't see it — `branchProtectionRule` requires
        # admin/write and returns null otherwise) every check counts.
        bpr = (n.get("baseRef") or {}).get("branchProtectionRule") or {}
        required_names = {
            c["context"]
            for c in (bpr.get("requiredStatusChecks") or [])
            if c.get("context")
        }
        all_runs = [
            r
            for suite in (suites_field or {}).get("nodes", [])
            for r in (suite.get("checkRuns") or {}).get("nodes", [])
        ]
        raw_contexts = (status_field or {}).get("contexts", []) or []
        if required_names:
            check_runs = [r for r in all_runs if r.get("name") in required_names]
            legacy_contexts = [
                c for c in raw_contexts if c.get("context") in required_names
            ]
        else:
            check_runs = all_runs
            legacy_contexts = raw_contexts
        pending = sum(
            1
            for r in check_runs
            if r.get("status") in ("IN_PROGRESS", "QUEUED", "PENDING")
        ) + sum(1 for c in legacy_contexts if c.get("state") == "PENDING")
        failed = sum(1 for r in check_runs if r.get("conclusion") == "FAILURE") + sum(
            1 for c in legacy_contexts if c.get("state") in ("FAILURE", "ERROR")
        )
        if not check_runs and not legacy_contexts:
            ci = "none"
        elif pending:
            ci = "pending"
        elif failed:
            ci = f"failed:{failed}"
        else:
            ci = "passed"
    unresolved, total = _unaddressed(n, author)
    return PR(
        number=n["number"],
        title=n["title"],
        branch=n["headRefName"],
        url=n["url"],
        author=author,
        is_draft=n["isDraft"],
        review_decision=n.get("reviewDecision") or "REVIEW_REQUIRED",
        mergeable=n.get("mergeable") or "UNKNOWN",
        ci=ci,
        unaddressed=unresolved,
        total_from_others=total,
        state=n.get("state") or "OPEN",
        updated_at=n.get("updatedAt") or "",
        body=n.get("body") or "",
        head_oid=n.get("headRefOid"),
    )


def _relevant_pr_query(
    owner: str, name: str, self_user: str, branches: list[str], fields: str
) -> tuple[str, dict[str, str]]:
    """Build the GraphQL query and the variable map for `gh api graphql -f`.

    All string-typed user-influenced inputs (owner, name, self_user, every
    branch) flow through GraphQL variables so a crafted branch name can't
    escape its string context and inject fragments.

    Per-branch alias fetches the newest PR for that head (any state — OPEN,
    MERGED, CLOSED) so the daemon's tick can refresh the per-PR cache after
    OPEN→MERGED / OPEN→CLOSED transitions. Without this, a merged PR drops
    out of the `is:open` search and its cached snapshot (consumed by the
    statusline footer) freezes at the last pre-merge state.
    """
    var_decls = ["$search: String!"]
    variables: dict[str, str] = {
        "search": f"repo:{owner}/{name} is:pr is:open author:{self_user}",
    }
    aliases: list[str] = []
    for i, branch in enumerate(branches):
        key = f"b{i}"
        var_decls.append(f"${key}: String!")
        variables[key] = branch
        aliases.append(
            f"{key}: pullRequests(headRefName: ${key}, "
            f"orderBy: {{field: CREATED_AT, direction: DESC}}, first: 1) "
            f"{{ nodes {{ {fields} }} }}"
        )
    if aliases:
        var_decls = ["$owner: String!", "$name: String!", *var_decls]
        variables["owner"] = owner
        variables["name"] = name
        repo_block = (
            f"repo: repository(owner: $owner, name: $name) {{ {' '.join(aliases)} }}"
        )
    else:
        repo_block = ""
    query = (
        f"query ({', '.join(var_decls)}) {{\n"
        f"  mine: search(query: $search, first: 30, type: ISSUE) {{\n"
        f"    nodes {{ ... on PullRequest {{ {fields} }} }}\n"
        f"  }}\n"
        f"  {repo_block}\n"
        f"}}"
    )
    return query, variables


def _graphql(query: str, variables: dict[str, str]) -> dict:
    args = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        args.extend(["-f", f"{k}={v}"])
    data = gh_json(args)
    assert isinstance(data, dict)
    # Partial-success responses (200 OK with `data` + `errors`) are common
    # during GH Actions outages — checkSuites resolves to null while PR
    # identity (number, title, state) still comes through. Pass them through
    # so `_pr_from_node` can surface ci="unknown" on the affected PRs instead
    # of dropping the whole cycle. Errors-only responses (no data) still fail
    # via gh's non-zero exit handled by `gh_json` → `run(check=True)`.
    return data


def _collect_nodes(data: dict, n_branches: int) -> list[dict]:
    nodes: list[dict] = list(data["data"]["mine"]["nodes"])
    repo = data["data"].get("repo") or {}
    for i in range(n_branches):
        nodes.extend(repo.get(f"b{i}", {}).get("nodes", []))
    return nodes


def _fetch_light_phase(
    owner: str, name: str, self_user: str, branches: list[str]
) -> dict[int, str]:
    query, variables = _relevant_pr_query(
        owner, name, self_user, branches, _PR_LIGHT_FIELDS
    )
    light_data = _graphql(query, variables)
    light_nodes = _collect_nodes(light_data, len(branches))
    light_by_number: dict[int, str] = {}
    for ln in light_nodes:
        if ln.get("number") is not None:
            light_by_number.setdefault(ln["number"], ln.get("updatedAt") or "")
    return light_by_number


def _identify_stale(
    light_by_number: dict[int, str], cache: dict[int, tuple[PR, str]]
) -> list[int]:
    stale: list[int] = []
    for num, updated in light_by_number.items():
        prev = cache.get(num)
        if prev is None or prev[1] != updated or prev[0].ci == "pending":
            stale.append(num)
    return stale


def _hydrate_stale(
    owner: str,
    name: str,
    stale: list[int],
    light_by_number: dict[int, str],
    cache: dict[int, tuple[PR, str]],
) -> None:
    # PR numbers are ints from prior GraphQL responses; safe to interpolate.
    alias_lines = [
        f"pr{i}: pullRequest(number: {n}) {{ {_PR_FIELDS} }}"
        for i, n in enumerate(stale)
    ]
    heavy_q = (
        "query ($owner: String!, $name: String!) "
        f"{{ repository(owner: $owner, name: $name) "
        f"{{ {' '.join(alias_lines)} }} }}"
    )
    heavy_data = _graphql(heavy_q, {"owner": owner, "name": name})
    repo = heavy_data["data"]["repository"]
    for i, num in enumerate(stale):
        node = repo.get(f"pr{i}")
        pr = _pr_from_node(node) if node else None
        if pr:
            cache[num] = (pr, light_by_number.get(num, ""))


def list_relevant_prs(
    owner: str,
    name: str,
    self_user: str,
    branches: list[str],
    cache: dict[int, tuple[PR, str]] | None = None,
) -> list[PR]:
    """My open PRs (by author search) + newest PR for each local worktree
    branch (any state — OPEN, MERGED, or CLOSED).

    The per-branch leg includes non-OPEN states so the daemon's tick can keep
    the per-PR cache fresh after a PR transitions to MERGED or CLOSED. The
    statusline footer renders from that cache; without this it would freeze
    at the last pre-merge snapshot until the worktree is torn down.

    Two-phase fetch when `cache` is given: a cheap (number, updatedAt) query
    first, then full detail only for PRs whose updatedAt changed (or whose
    cached CI was `pending` — CI updates don't bump updatedAt). Steady-state
    cycles where nothing moved cost one cheap GraphQL call instead of the
    heavy one.
    """
    light_by_number = _fetch_light_phase(owner, name, self_user, branches)
    if cache is None:
        cache = {}
    stale = _identify_stale(light_by_number, cache)
    if stale:
        _hydrate_stale(owner, name, stale, light_by_number, cache)
    for num in list(cache):
        if num not in light_by_number:
            del cache[num]
    return [cache[num][0] for num in light_by_number if num in cache]
