"""GitHub (gh CLI + GraphQL) helpers and the PR dataclass."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import run


def gh_json(args: list[str]) -> dict | list:
    return json.loads(run(["gh", *args]))


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


def fetch_merged_branches(repo_path: Path, limit: int = 100) -> set[str]:
    """Head branches of recently merged PRs in `repo_path`. Empty set on gh failure."""
    r = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--json",
            "headRefName",
        ],
        capture_output=True,
        text=True,
        cwd=str(repo_path),
    )
    if r.returncode != 0:
        return set()
    try:
        return {row["headRefName"] for row in json.loads(r.stdout)}
    except (json.JSONDecodeError, KeyError):
        return set()


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
        return json.loads(result.stdout)
    return gh_json(["pr", "view", pr_num, "--json", fields])


def resolve_pr_branch(pr_num: str) -> str:
    """Resolve a PR number to its head branch name via gh CLI (current cwd)."""
    nwo = run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        check=False,
    ).strip()
    if not nwo:
        raise RuntimeError(f"could not resolve repo for PR #{pr_num}")
    out = run(
        [
            "gh",
            "-R",
            nwo,
            "pr",
            "view",
            pr_num,
            "--json",
            "headRefName",
            "-q",
            ".headRefName",
        ],
        check=False,
    ).strip()
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


_PR_FIELDS = """
  number title url isDraft headRefName mergeable reviewDecision updatedAt state
  author { login __typename }
  reviewThreads(first: 100) {
    nodes {
      isResolved
      comments(first: 100) { nodes { author { login __typename } } }
    }
  }
  reviews(first: 100) { nodes { author { login __typename } body } }
  commits(last: 1) {
    nodes { commit { statusCheckRollup {
      contexts(first: 30) { nodes {
        __typename
        ... on CheckRun { status conclusion }
        ... on StatusContext { state }
      } }
    } } }
  }
"""

_PR_LIGHT_FIELDS = "number updatedAt"


def _unaddressed(pr_node: dict, pr_author: str) -> tuple[int, int]:
    """Threads + standalone reviews awaiting the PR author's response.

    Bots (copilot, dependabot, etc.) count as reviewers.
    Returns (unresolved, total).
    """
    total = unresolved = 0
    for t in pr_node["reviewThreads"]["nodes"]:
        authors = [c.get("author") for c in t["comments"]["nodes"]]
        non_self = [
            a for a in authors if a and a.get("login") and a["login"] != pr_author
        ]
        if not non_self:
            continue
        total += 1
        last = authors[-1] if authors else None
        if (
            not t["isResolved"]
            and last
            and last.get("login")
            and last["login"] != pr_author
        ):
            unresolved += 1
    for r in pr_node["reviews"]["nodes"]:
        a = r.get("author") or {}
        login = a.get("login")
        if login and login != pr_author and (r.get("body") or "").strip():
            total += 1
    return unresolved, total


def _pr_from_node(n: dict) -> PR | None:
    author = (n.get("author") or {}).get("login")
    if not author:
        return None
    contexts = (
        (
            ((n["commits"]["nodes"] or [{}])[0].get("commit") or {}).get(
                "statusCheckRollup"
            )
            or {}
        )
        .get("contexts", {})
        .get("nodes", [])
    )
    pending = sum(
        1
        for c in contexts
        if c.get("status") in ("IN_PROGRESS", "QUEUED", "PENDING")
        or c.get("state") == "PENDING"
    )
    failed = sum(
        1
        for c in contexts
        if c.get("conclusion") == "FAILURE" or c.get("state") in ("FAILURE", "ERROR")
    )
    if not contexts:
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
    )


def _relevant_pr_query(
    owner: str, name: str, self_user: str, coworker_branches: list[str], fields: str
) -> str:
    aliases = []
    for i, branch in enumerate(coworker_branches):
        b = branch.replace('"', '\\"')
        aliases.append(
            f'cw{i}: pullRequests(headRefName: "{b}", states: OPEN, first: 1) '
            f"{{ nodes {{ {fields} }} }}"
        )
    repo_block = (
        f'repo: repository(owner: "{owner}", name: "{name}") {{ {" ".join(aliases)} }}'
        if aliases
        else ""
    )
    return f"""query {{
      mine: search(query: "repo:{owner}/{name} is:pr is:open author:{self_user}",
                   first: 30, type: ISSUE) {{
        nodes {{ ... on PullRequest {{ {fields} }} }}
      }}
      {repo_block}
    }}"""


def _collect_nodes(data: dict, n_coworker: int) -> list[dict]:
    nodes: list[dict] = list(data["data"]["mine"]["nodes"])
    repo = data["data"].get("repo") or {}
    for i in range(n_coworker):
        nodes.extend(repo.get(f"cw{i}", {}).get("nodes", []))
    return nodes


def _fetch_light_phase(
    owner: str, name: str, self_user: str, coworker_branches: list[str]
) -> dict[int, str]:
    light_q = _relevant_pr_query(
        owner, name, self_user, coworker_branches, _PR_LIGHT_FIELDS
    )
    light_data = gh_json(["api", "graphql", "-f", f"query={light_q}"])
    light_nodes = _collect_nodes(light_data, len(coworker_branches))
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
    alias_lines = [
        f"pr{i}: pullRequest(number: {n}) {{ {_PR_FIELDS} }}"
        for i, n in enumerate(stale)
    ]
    heavy_q = (
        f'query {{ repository(owner: "{owner}", name: "{name}") '
        f'{{ {" ".join(alias_lines)} }} }}'
    )
    heavy_data = gh_json(["api", "graphql", "-f", f"query={heavy_q}"])
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
    coworker_branches: list[str],
    cache: dict[int, tuple[PR, str]] | None = None,
) -> list[PR]:
    """Mine (by author) + coworker PRs whose head matches a local worktree.

    Two-phase fetch when `cache` is given: a cheap (number, updatedAt) query
    first, then full detail only for PRs whose updatedAt changed (or whose
    cached CI was `pending` — CI updates don't bump updatedAt). Steady-state
    cycles where nothing moved cost one cheap GraphQL call instead of the
    heavy one.
    """
    light_by_number = _fetch_light_phase(owner, name, self_user, coworker_branches)
    if cache is None:
        cache = {}
    stale = _identify_stale(light_by_number, cache)
    if stale:
        _hydrate_stale(owner, name, stale, light_by_number, cache)
    for num in list(cache):
        if num not in light_by_number:
            del cache[num]
    return [cache[num][0] for num in light_by_number if num in cache]
