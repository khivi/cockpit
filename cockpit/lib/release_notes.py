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


def _subjects(repo: str, limit: int) -> list[str]:
    """First lines of the last `limit` commits that touched plugin.json on the
    default branch — one per merged PR (each squash-bumps the version), newest
    first. `[]` on any gh/network/parse failure."""
    try:
        out = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repo}/commits?path=.claude-plugin/plugin.json&per_page={limit}",
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
    return [
        line.strip()
        for line in out.splitlines()
        if line.strip() and not line.startswith("chore: bump version")
    ]


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
