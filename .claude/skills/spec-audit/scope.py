#!/usr/bin/env python3
"""Count the ledger and resolve which bullet ids an audit run covers.

Answers the two questions every /spec-audit run opens with — how big is the
ledger, and which of it is in scope — so the numbers come from one parser
rather than from ad-hoc greps that miscount the same thing differently each
time. A bullet's text wraps across continuation lines, so `(untested:` and the
id live on different lines: counting either with `rg -c` counts LINES and
reports a number that is wrong in a way nobody notices.

    scope.py                 # branch: bullets this branch's diff touches
    scope.py all             # every unwaived bullet
    scope.py --pr 539        # the same, scoped to a PR's diff
    scope.py --base <ref>    # compare against something other than origin/main

Reads only. Prints the inventory, then the in-scope ids.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

BULLET = re.compile(r"^- \[([a-z0-9.-]+~\d+)\]")
MARKER = re.compile(r'covers\("([a-z0-9.-]+~\d+)"\)')
WAIVED = "(untested:"

ROOT = Path(__file__).resolve().parents[3]
SPECS = ROOT / "specs"
TESTS = ROOT / "tests"


class Bullet:
    """One ledger entry: its id, the lines it spans, and whether it is waived."""

    def __init__(self, id_: str, path: Path, start: int) -> None:
        self.id = id_
        self.path = path
        self.start = start
        self.end = start
        self.text = ""

    @property
    def waived(self) -> bool:
        return WAIVED in self.text


def parse_bullets() -> list[Bullet]:
    """Every bullet in specs/, each carrying its full wrapped text.

    A bullet owns every line from its `- [id]` up to the next bullet or the
    next heading — which is what makes the waived test correct, since
    `(untested: …)` is usually on a continuation line.
    """
    out: list[Bullet] = []
    for path in sorted(SPECS.glob("*.md")):
        current: Bullet | None = None
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if m := BULLET.match(line):
                current = Bullet(m.group(1), path, n)
                current.text = line
                out.append(current)
            elif current is None or line.startswith("#"):
                current = None
            elif line.strip():
                current.text += " " + line.strip()
                current.end = n
    return out


def claimed_ids() -> set[str]:
    return {
        m.group(1)
        for path in TESTS.rglob("*.py")
        for m in MARKER.finditer(path.read_text())
    }


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True
    ).stdout


def changed_line_numbers(base: str, pr: str | None) -> dict[Path, set[int]]:
    """Post-image line numbers each spec/test file actually ADDED, per file.

    Walks each hunk body and records only `+` lines rather than the hunk's
    declared range. The range is only equal to the change under `-U0`, which
    `git diff` accepts and `gh pr diff` does not — so taking the range would
    quietly pull in whatever marker or bullet sits within three lines of an
    edit, and a PR scope would report ids the PR never touched.
    """
    if pr:
        diff = subprocess.run(
            ["gh", "pr", "diff", pr, "--patch"],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        ).stdout
    else:
        diff = _git("diff", "-U0", base, "--", "specs/", "tests/")

    touched: dict[Path, set[int]] = {}
    path: Path | None = None
    lineno = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            rel = line[6:]
            path = ROOT / rel if rel.startswith(("specs/", "tests/")) else None
        elif line.startswith("@@"):
            if m := re.search(r"\+(\d+)", line):
                lineno = int(m.group(1))
        elif path is None or line.startswith("-"):
            continue
        elif line.startswith("+"):
            touched.setdefault(path, set()).add(lineno)
            lineno += 1
        elif line.startswith(" "):
            lineno += 1
    return touched


def branch_scope(bullets: list[Bullet], touched: dict[Path, set[int]]) -> set[str]:
    """Ids a diff puts in scope: a spec bullet whose own lines moved, plus every
    id a changed test file's markers claim on a changed line."""
    ids: set[str] = set()
    for b in bullets:
        lines = touched.get(b.path, set())
        if lines & set(range(b.start, b.end + 1)):
            ids.add(b.id)
    for path, lines in touched.items():
        if path.suffix != ".py" or not path.exists():
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if n in lines and (m := MARKER.search(line)):
                ids.add(m.group(1))
    return ids


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scope", nargs="?", default="branch", choices=["branch", "all"])
    ap.add_argument("--pr", help="scope to a PR's diff instead of the branch")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--ids-only", action="store_true", help="just the id list")
    args = ap.parse_args()

    bullets = parse_bullets()
    claimed = claimed_ids()
    live = [b for b in bullets if not b.waived]

    if args.scope == "all" and not args.pr:
        in_scope = {b.id for b in live}
    else:
        touched = changed_line_numbers(args.base, args.pr)
        in_scope = branch_scope(bullets, touched) & {b.id for b in live}

    if args.ids_only:
        print("\n".join(sorted(in_scope)))
        return 0

    print(f"{'file':<20} {'bullets':>8} {'waived':>7} {'auditable':>10}")
    for path in sorted(SPECS.glob("*.md")):
        rows = [b for b in bullets if b.path == path]
        w = sum(b.waived for b in rows)
        print(f"{path.name:<20} {len(rows):>8} {w:>7} {len(rows) - w:>10}")
    waived = len(bullets) - len(live)
    print(f"{'TOTAL':<20} {len(bullets):>8} {waived:>7} {len(live):>10}")

    # A bullet with no marker is the coverage gate's failure, not the audit's —
    # reported here only so a zero-scope run is legible.
    if orphans := sorted({b.id for b in live} - claimed):
        print(f"\nunclaimed (the gate's problem, not this skill's): {len(orphans)}")
        print("  " + ", ".join(orphans))

    label = (args.pr and f"PR #{args.pr}") or args.scope
    print(f"\nin scope ({label}): {len(in_scope)}")
    for id_ in sorted(in_scope):
        n = sum(
            1
            for p in TESTS.rglob("*.py")
            for m in MARKER.finditer(p.read_text())
            if m.group(1) == id_
        )
        print(f"  {id_:<40} {n} claiming test{'s' if n != 1 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
