"""Fail when a doc names a `cockpit` subcommand or flag the CLI no longer accepts.

The valid set comes from the real argparse parsers (`cockpit.cli.parsers`), not
a regex over the source. Only backtick spans and fenced code blocks are read,
since prose mentions a flag without quoting it far less often than it uses the
word "cockpit". `MIGRATION.md` and `CHANGELOG.md` are out of scope: naming a
removed flag is their job.

usage: check_doc_cli_drift.py [FILE...]   (default: the in-scope docs)
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cockpit.cli import parsers  # noqa: E402

DOC_GLOBS = ("README.md", "FEATURES.md", "docs/**/*.md", "specs/**/*.md")
TOP_LEVEL_FLAGS = frozenset({"-h", "--help", "-V", "--version"})
# Every argparse parser and `teardown` answer these; the other shims never see them.
HELP_FLAGS = frozenset({"-h", "--help"})

_SPAN = re.compile(r"`([^`\n]+)`")
_MENTION = re.compile(r"(?<![\w./-])cockpit\s+(.+)")
_WORD = re.compile(r"[a-z][a-z-]*")
_FLAG = re.compile(r"--?[A-Za-z][\w-]*")
_STOP = frozenset({"|", "||", "&&", ";", "#", ">", "2>&1"})


def _subparsers(p: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def check_mention(tokens: list[str], table: dict) -> str | None:
    """The drift in one `cockpit …` token list, or None if the CLI accepts it."""
    if not tokens:
        return None
    first = tokens[0]
    if first.startswith("-"):
        flag = first.split("=")[0]
        return None if flag in TOP_LEVEL_FLAGS else f"unknown top-level flag {flag}"
    if not _WORD.fullmatch(first):
        return None  # a placeholder such as `<sub>`
    if first not in table:
        return f"unknown subcommand {first!r}"
    parser = table[first]
    path = [first]
    allowed = set(HELP_FLAGS)
    if parser is not None:
        allowed |= set(parser._option_string_actions)
    for tok in tokens[1:]:
        if tok in _STOP or tok == "--":
            break
        if parser is not None and tok in (subs := _subparsers(parser)):
            parser = subs[tok]
            path.append(tok)
            allowed |= set(parser._option_string_actions)
            continue
        if _FLAG.fullmatch(flag := tok.split("=")[0]) and flag not in allowed:
            return f"{flag} is not accepted by `cockpit {' '.join(path)}`"
    return None


def _candidates(text: str) -> Iterator[tuple[int, str]]:
    in_fence = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            yield n, line
        else:
            for m in _SPAN.finditer(line):
                yield n, m.group(1)


def check_file(path: Path, table: dict) -> list[str]:
    out = []
    for n, chunk in _candidates(path.read_text()):
        for m in _MENTION.finditer(chunk):
            if err := check_mention(m.group(1).split(), table):
                out.append(f"{path}:{n}: {err}")
    return out


def default_docs() -> list[Path]:
    return sorted({p for g in DOC_GLOBS for p in ROOT.glob(g)})


def main(argv: Iterable[str] | None = None) -> int:
    files = [Path(a) for a in (sys.argv[1:] if argv is None else argv)]
    table = parsers()
    errors = [e for f in files or default_docs() for e in check_file(f, table)]
    for e in errors:
        print(e)
    if errors:
        print(
            f"\n{len(errors)} doc mention(s) of a cockpit subcommand or flag that "
            "cockpit/cli.py no longer accepts. Fix the doc, or the CLI.",
            file=sys.stderr,
        )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
