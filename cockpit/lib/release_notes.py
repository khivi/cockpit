"""Release notes: the squash-merge commit subjects on the install repo's
default branch, scoped to the versions you moved between. Read-only, network,
best-effort — every failure degrades to `""` so the caller shows nothing.

No changelog file, no Action: each merged PR squash-bumps `plugin.json` by one
patch and lands as a single conventional-commit subject on `main`, so those
subjects *are* the changelog. We read them via `gh api` the moment they're
wanted (the `r` key) and never store them — same network-is-fine, never-cache
stance as `version.latest_version`.
"""

from __future__ import annotations

import subprocess
from datetime import date, datetime, timedelta

from cockpit.lib import version

PER_PAGE = 15  # `r` ChangeLog screen: one lazy-loaded page per scroll


def _raw_entries(repo: str, per_page: int, page: int = 1) -> list[tuple[str, str]]:
    """`(subject, ISO-date)` for `per_page` commits (page `page`, 1-indexed) that
    touched plugin.json on the default branch — one per merged PR, newest first.
    Unfiltered. `[]` on any gh/network/parse failure."""
    try:
        out = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repo}/commits?path=.claude-plugin/plugin.json"
                f"&per_page={per_page}&page={page}",
                "--jq",
                r'.[] | [(.commit.message | split("\n")[0]), '
                r".commit.committer.date] | @tsv",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    entries: list[tuple[str, str]] = []
    for line in out.splitlines():
        subject, _, iso = line.partition("\t")
        subject = subject.strip()
        if subject:
            entries.append((subject, iso.strip()))
    return entries


def _bucket(iso_date: str, today: date) -> str:
    """Relative-age group label for a commit's ISO date vs local `today`:
    today / yesterday / this week / last week / earlier. Unparsable → earlier."""
    try:
        d = datetime.fromisoformat(iso_date.replace("Z", "+00:00")).astimezone().date()
    except ValueError:
        return "earlier"
    if d >= today:
        return "today"
    if d == today - timedelta(days=1):
        return "yesterday"
    monday = today - timedelta(days=today.weekday())  # Monday == 0
    if d >= monday:
        return "this week"
    if d >= monday - timedelta(days=7):
        return "last week"
    return "earlier"


def recent_title() -> str:
    """Title for the on-demand ChangeLog screen (no network)."""
    return f"recent changes (v{version.running_version()})"


def recent_page(
    page: int, per_page: int = PER_PAGE
) -> tuple[list[tuple[str, str]], bool]:
    """One page of recent merged-PR `(subject, bucket)` entries for the lazy-scroll
    ChangeLog, plus an `exhausted` flag (True once GitHub returns a short/empty
    page, so the screen stops fetching). `([], True)` on no-repo or any failure.
    The "chore: bump version" auto-bumps are filtered out, but `exhausted` keys
    off the *raw* count so filtering can't fake an early end mid-history."""
    repo = version.install_repo()
    if not repo:
        return [], True
    raw = _raw_entries(repo, per_page, page)
    exhausted = len(raw) < per_page
    today = datetime.now().astimezone().date()
    items = [
        (s, _bucket(iso, today))
        for s, iso in raw
        if not s.startswith("chore: bump version")
    ]
    return items, exhausted
