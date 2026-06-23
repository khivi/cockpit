"""Release notes: the squash-merge commit subjects on the install repo's
default branch, scoped to the versions you moved between. Read-only, network,
best-effort — every failure degrades to `""` so the caller shows nothing.

No changelog file, no Action: each merged PR squash-bumps `plugin.json` by one
patch and lands as a single conventional-commit subject on `main`, so those
subjects *are* the changelog. We read them via `gh api` the moment they're
wanted (the `r` key, or once right after a `u` self-update) and never store
them — same network-is-fine, never-cache stance as `version.latest_version`.
"""

from __future__ import annotations

import subprocess

from cockpit.lib import version

_RECENT = 15  # on-demand `r` (no prior version): a recent window
_MAX = 30  # hard cap on rendered lines, whatever the version gap
PER_PAGE = 15  # `r` ChangeLog screen: one lazy-loaded page per scroll


def _raw_subjects(repo: str, per_page: int, page: int = 1) -> list[str]:
    """First lines of `per_page` commits (page `page`, 1-indexed) that touched
    plugin.json on the default branch — one per merged PR, newest first.
    Unfiltered. `[]` on any gh/network/parse failure."""
    try:
        out = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repo}/commits?path=.claude-plugin/plugin.json"
                f"&per_page={per_page}&page={page}",
                "--jq",
                r'.[].commit.message | split("\n")[0]',
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _subjects(repo: str, limit: int) -> list[str]:
    """Filtered (no auto-bump commits) first page of subjects, newest first."""
    return [
        s for s in _raw_subjects(repo, limit) if not s.startswith("chore: bump version")
    ]


def recent_title() -> str:
    """Title for the on-demand ChangeLog screen (no network)."""
    return f"recent changes (v{version.running_version()})"


def recent_page(page: int, per_page: int = PER_PAGE) -> tuple[list[str], bool]:
    """One page of recent merged-PR subjects for the lazy-scroll ChangeLog,
    plus an `exhausted` flag (True once GitHub returns a short/empty page, so
    the screen stops fetching). `([], True)` on no-repo or any failure. The
    "chore: bump version" auto-bumps are filtered out, but `exhausted` keys off
    the *raw* count so filtering can't fake an early end mid-history."""
    repo = version.install_repo()
    if not repo:
        return [], True
    raw = _raw_subjects(repo, per_page, page)
    exhausted = len(raw) < per_page
    subjects = [s for s in raw if not s.startswith("chore: bump version")]
    return subjects, exhausted


def _gap(prev: str, current: str) -> int:
    """How many PRs landed between `prev` and `current`. The pre-push hook bumps
    each PR to main+1 patch, so within one (major, minor) the patch delta is the
    exact PR count; a hand minor/major bump can't be counted this way and falls
    back to the recent window.

    ponytail: patch-delta heuristic — exact for the only bump automation makes;
    widen to a version→SHA walk only if minor/major release notes ever matter.
    """
    p, c = version.parse_version(prev), version.parse_version(current)
    if len(p) >= 3 and len(c) >= 3 and p[:2] == c[:2] and c[2] > p[2]:
        return min(c[2] - p[2], _MAX)
    return _RECENT


def notes(prev_version: str | None = None) -> str:
    """Rendered release-notes body for the `ConfigScreen` modal, or `""` when
    there's nothing to show (no repo, no network, or `prev_version` == current).
    `prev_version` (set after a `u` self-update) scopes to PRs since that
    version; `None` (the `r` key) shows the recent window."""
    repo = version.install_repo()
    if not repo:
        return ""
    current = version.running_version()
    scoped = bool(prev_version and current and prev_version != current)
    limit = _gap(prev_version or "", current) if scoped else _RECENT
    subjects = _subjects(repo, limit)[:limit]
    if not subjects:
        return ""
    title = f"{prev_version} → {current}" if scoped else f"recent changes (v{current})"
    return title + "\n\n" + "\n".join(f"• {s}" for s in subjects)
