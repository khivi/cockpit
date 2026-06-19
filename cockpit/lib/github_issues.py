"""GitHub-issue ticket helpers — the `tickets: github` provider.

The GitHub analog of `lib.linear`. Same contract: every call degrades to
None/False/empty — never raises — on a missing issue, a `gh` failure, or a
timeout, so a flaky network can't stall the reconcile.

Two surfaces:

  * Pure parsers — `parse_github_issue_refs` reads the *delivery* signal out of
    a PR body: GitHub's documented closing keywords (`Closes #123`,
    `Fixes owner/repo#45`, `Resolves <issue-url>`). This mirrors
    `linear.parse_linear_footers` — only a closing-keyword reference counts as
    delivered, a bare `#123` mention in prose does not. Plus the spawn-source
    regexes (`GITHUB_ISSUE_URL_RE`, `GITHUB_ISSUE_SHORTHAND_RE`).
  * `gh`-backed reads/writes — `fetch_issue`/`fetch_issues` (state + labels +
    assignees, driving the `devdone=` pill) and the one *write*,
    `close_issue` (`gh issue close`, reached only by the opt-in
    `github_done_on_merge` path in the slow tick).

Unlike Linear (a personal `LINEAR_API_KEY` + raw GraphQL), the transport here
is the already-authenticated `gh` CLI, so there's no API-key env var and no
MCP probe — the spawn fetch prompt just tells Claude to run `gh issue view`.
"""

from __future__ import annotations

import json
import re
import subprocess

# The GitHub-specific fields the `tickets` config block accepts, as
# `(name, kind)` (kind resolved to a validator in `tickets.py`). The provider
# owns its own config surface; this *specification* drives preflight validation
# (common fields like `provider`/`close_on_merge` are added by `tickets.py`).
# Keep in sync with the GitHub readers in `config.py` (`github_dev_done_label`,
# `github_start_label`).
CONFIG_FIELDS: tuple[tuple[str, str], ...] = (
    ("dev_done_label", "str"),
    ("start_label", "str"),
)

# GitHub's documented issue-closing keywords (close/closes/closed, fix/fixes/
# fixed, resolve/resolves/resolved), followed by an issue reference in any of
# the three forms GitHub accepts: a full issues URL, a cross-repo `owner/repo#N`,
# or a same-repo `#N`. Case-insensitive, not line-anchored — GitHub honors the
# keyword anywhere in the body, so we do too (unlike the line-anchored Linear
# footer). The optional `:` after the keyword covers `Closes: #1`.
_CLOSE_KEYWORD = r"close[sd]?|fix(?:e[sd])?|resolve[sd]?"
GITHUB_CLOSE_RE = re.compile(
    rf"\b(?:{_CLOSE_KEYWORD})\b\s*:?\s+"
    r"(?P<ref>"
    r"https?://github\.com/[\w.-]+/[\w.-]+/issues/\d+"  # full issue URL
    r"|[\w.-]+/[\w.-]+#\d+"  # owner/repo#N
    r"|#\d+"  # #N (same repo)
    r")",
    re.IGNORECASE,
)

# A bare GitHub issue URL — the unambiguous spawn source (the PR-URL regex in
# spawn.detect_source matches `/pull/N`, so an `/issues/N` URL would otherwise
# fall through to branch mode). group(1) = owner/repo, group(2) = number.
GITHUB_ISSUE_URL_RE = re.compile(
    r"https?://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)", re.IGNORECASE
)

# Explicit issue shorthand for `cockpit new` — `i#123` / `gh#123`. A bare `#123`
# is left as PR mode (GitHub shares one number space between PRs and issues, so
# `#123` is ambiguous); the prefix disambiguates without a network round-trip.
GITHUB_ISSUE_SHORTHAND_RE = re.compile(r"(?:i|gh)#(\d+)", re.IGNORECASE)

# Bound each `gh` call so a hung CLI can't stall the slow tick (degrades to None,
# exactly like a Linear GraphQL timeout).
_GH_TIMEOUT_SECONDS = 15


def _parse_ref(ref: str, repo_nwo: str | None) -> tuple[str | None, int | None]:
    """Normalize one matched reference to ``(owner/repo, number)``.

    A same-repo `#N` resolves its nwo from `repo_nwo` (the PR's own repo). A URL
    or `owner/repo#N` carries its own nwo. Returns ``(None, None)`` if the number
    can't be parsed.
    """
    url = GITHUB_ISSUE_URL_RE.fullmatch(ref)
    if url:
        return url.group(1), int(url.group(2))
    if "#" in ref:
        left, _, num = ref.partition("#")
        try:
            number = int(num)
        except ValueError:
            return None, None
        return (left or repo_nwo), number
    return None, None


def short_ref(nwo: str | None, number: int, repo_nwo: str | None) -> str:
    """Canonical display ref: ``"#123"`` for the PR's own repo, else
    ``"owner/repo#123"``. Cross-repo refs keep their nwo so the pill and the
    close-on-merge writer target the right repo."""
    if nwo and repo_nwo and nwo.lower() == repo_nwo.lower():
        return f"#{number}"
    if nwo:
        return f"{nwo}#{number}"
    return f"#{number}"


def parse_github_issue_refs(body: str, repo_nwo: str | None) -> list[str]:
    """Return the de-duplicated, order-preserving canonical refs the PR
    *delivers* — every issue named by a GitHub closing keyword in `body`.

    `repo_nwo` (the PR's own `owner/repo`) resolves same-repo `#N` refs and
    decides which refs render short (`#123`) vs cross-repo (`owner/repo#123`).
    Empty when `body` is falsy or carries no closing-keyword reference — the
    GitHub analog of `parse_linear_footers`.
    """
    if not body:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for ref in GITHUB_CLOSE_RE.findall(body):
        nwo, number = _parse_ref(ref, repo_nwo)
        if number is None:
            continue
        canonical = short_ref(nwo, number, repo_nwo)
        if canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    return out


def _gh_json(args: list[str], *, repo_dir: str | None = None) -> dict | list | None:
    """Run `gh <args>` and return parsed JSON, or None on any failure.

    Mirrors `linear._post_graphql`'s degrade-never-raise contract. A missing
    `gh`, non-zero exit, timeout, or unparsable output all collapse to None
    (caller treats it as "state unknown" → pill stays off).
    """
    try:
        res = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            cwd=repo_dir,
            timeout=_GH_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0 or not res.stdout.strip():
        return None
    try:
        data: dict | list = json.loads(res.stdout)
    except ValueError:
        return None
    return data


def fetch_issue(
    ref: str, *, repo_nwo: str | None = None, repo_dir: str | None = None
) -> dict | None:
    """Return one issue's tracker state, or None.

    ``{"ref": "#123", "nwo": "owner/repo", "number": 123, "state": "open"|
    "closed", "labels": [<casefolded names>], "assignees": [<logins>],
    "url": <str>, "title": <str>}``. `state` and `labels` drive the `devdone=`
    pill; `state`/`assignees` drive the close-on-merge eligibility check.

    None — never raises — on an unparsable ref, a missing issue, or a `gh`
    failure. `repo_dir` (a checkout path) lets `gh` infer the repo when no nwo
    is known; an explicit `nwo` (from a cross-repo ref) takes precedence.
    """
    nwo, number = _parse_ref(ref, repo_nwo)
    if number is None:
        return None
    args = [
        "issue",
        "view",
        str(number),
        "--json",
        "number,state,labels,assignees,url,title",
    ]
    if nwo:
        args += ["--repo", nwo]
    data = _gh_json(args, repo_dir=repo_dir)
    if not isinstance(data, dict):
        return None
    return {
        "ref": short_ref(nwo, number, repo_nwo),
        "nwo": nwo,
        "number": number,
        "state": str(data.get("state") or "").lower() or None,
        "labels": [
            str(label.get("name") or "").casefold()
            for label in (data.get("labels") or [])
            if label.get("name")
        ],
        "assignees": [
            str(a.get("login") or "")
            for a in (data.get("assignees") or [])
            if a.get("login")
        ],
        "url": data.get("url"),
        "title": data.get("title"),
    }


def fetch_issues(
    refs: list[str], *, repo_nwo: str | None = None, repo_dir: str | None = None
) -> dict[str, dict | None]:
    """`{canonical_ref: issue_dict_or_None}` for every ref — the batched form of
    `fetch_issue`. Every input ref appears in the result (None when its issue
    can't be read). One `gh issue view` per distinct issue; a single failure is
    isolated to its own ref. Never raises.
    """
    out: dict[str, dict | None] = {}
    for ref in refs:
        if ref in out:
            continue
        out[ref] = fetch_issue(ref, repo_nwo=repo_nwo, repo_dir=repo_dir)
    return out


def viewer_login(*, repo_dir: str | None = None) -> str | None:
    """Return the authenticated `gh` user's login, or None — the GitHub analog
    of `linear.fetch_viewer_id`. The "only close my own issues" gate: a merged
    PR's issue is only auto-closed when the viewer is among its assignees.
    """
    data = _gh_json(["api", "user", "--jq", "{login: .login}"], repo_dir=repo_dir)
    if not isinstance(data, dict):
        return None
    return str(data.get("login") or "") or None


def add_label(
    ref: str,
    label: str,
    *,
    repo_nwo: str | None = None,
    repo_dir: str | None = None,
) -> bool:
    """Add `label` to the issue `ref` (`gh issue edit --add-label`). Used to mark
    an issue "work started" when cockpit spawns a worktree on it (the
    `tickets.start_label` opt-in). True on success; False on an unparsable ref,
    an empty label, or any `gh` failure. Never raises.
    """
    nwo, number = _parse_ref(ref, repo_nwo)
    if number is None or not label.strip():
        return False
    args = ["issue", "edit", str(number), "--add-label", label]
    if nwo:
        args += ["--repo", nwo]
    try:
        res = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            cwd=repo_dir,
            timeout=_GH_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return res.returncode == 0


def close_issue(
    ref: str, *, repo_nwo: str | None = None, repo_dir: str | None = None
) -> bool:
    """Close the issue `ref` (`gh issue close`). The module's one *write*.

    True on success; False on an unparsable ref or any `gh` failure. Never
    raises. The caller (`cycle._transition_merged_tickets`) owns the policy
    (whether/when to close) — this just performs the call.
    """
    nwo, number = _parse_ref(ref, repo_nwo)
    if number is None:
        return False
    args = ["issue", "close", str(number)]
    if nwo:
        args += ["--repo", nwo]
    try:
        res = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            cwd=repo_dir,
            timeout=_GH_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return res.returncode == 0
