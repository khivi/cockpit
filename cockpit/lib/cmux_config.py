"""Turn off cmux's native sidebar PR row from `cockpit setup`; undo it on teardown.

cmux resolves a branch to a PR by name alone, so its row double-renders beside
cockpit's `pr` pill and shows a stale PR on a reused branch. No `cmux config set`
key covers it, so this edits `~/.config/cmux/cmux.json` as text: the file is
JSONC, and a `json` round-trip would drop every comment in it.

Ownership is the trailing `MARKER` comment on the one line written, so teardown
needs no stored state and never touches a setting the user wrote themselves.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

MARKER = "// cockpit setup; `cockpit teardown` removes this"
_WAS_TRUE = " (was true)"

_KEY_TRUE_RE = re.compile(r'^(\s*)"showPullRequests"(\s*):(\s*)true(\s*,?)\s*$')
_SIDEBAR_OPEN_RE = re.compile(r'^(\s*)"sidebar"\s*:\s*\{\s*$')
_TOP_OPEN_RE = re.compile(r"^\s*\{\s*$")
_MARKED_FALSE_RE = re.compile(
    r"false(\s*,?)\s*" + re.escape(MARKER) + re.escape(_WAS_TRUE)
)


def cmux_config_path() -> Path:
    return Path.home() / ".config" / "cmux" / "cmux.json"


def _strip_jsonc(text: str) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def parse_jsonc(text: str) -> dict | None:
    try:
        data = json.loads(_strip_jsonc(text))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _show_pull_requests(data: dict) -> object:
    sidebar = data.get("sidebar")
    return sidebar.get("showPullRequests", True) if isinstance(sidebar, dict) else True


def _with_row_off(text: str, data: dict) -> str | None:
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if m := _KEY_TRUE_RE.match(line.rstrip("\n")):
            indent, sp1, sp2, comma = m.groups()
            lines[i] = (
                f'{indent}"showPullRequests"{sp1}:{sp2}false{comma} {MARKER}{_WAS_TRUE}\n'
            )
            return "".join(lines)
    sidebar = data.get("sidebar")
    if isinstance(sidebar, dict):
        for i, line in enumerate(lines):
            if m := _SIDEBAR_OPEN_RE.match(line.rstrip("\n")):
                comma = "," if sidebar else ""
                lines.insert(
                    i + 1, f'{m.group(1)}  "showPullRequests" : false{comma} {MARKER}\n'
                )
                return "".join(lines)
        return None
    for i, line in enumerate(lines):
        if _TOP_OPEN_RE.match(line.rstrip("\n")):
            comma = "," if data else ""
            lines.insert(
                i + 1,
                f'  "sidebar" : {{ "showPullRequests" : false }}{comma} {MARKER}\n',
            )
            return "".join(lines)
    return None


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.cockpit.{os.getpid()}.tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _reload() -> None:
    if shutil.which("cmux"):
        subprocess.run(["cmux", "reload-config"], capture_output=True, check=False)


def disable_native_pr_row(path: Path | None = None) -> bool:
    """Set `sidebar.showPullRequests: false`. Returns True iff the file changed.

    A file that already has it off is left alone and stays the user's: no marker
    is written, so teardown will not turn it back on. Any shape the line edit
    cannot prove correct is reported and skipped rather than guessed at.
    """
    path = path or cmux_config_path()
    manual = f'set "sidebar": {{"showPullRequests": false}} in {path} by hand'
    text = path.read_text() if path.exists() else "{\n}\n"
    data = parse_jsonc(text)
    if data is None:
        print(f"cmux config not parseable as JSONC; {manual}")
        return False
    if _show_pull_requests(data) is False:
        return False
    new = _with_row_off(text, data)
    after = parse_jsonc(new) if new is not None else None
    if new is None or after is None or _show_pull_requests(after) is not False:
        print(f"could not edit cmux config safely; {manual}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, new)
    _reload()
    print(f"turned off cmux's native sidebar PR row -> {path}")
    return True


def restore_native_pr_row(path: Path | None = None) -> bool:
    """Inverse of `disable_native_pr_row`: undo only the line it marked."""
    path = path or cmux_config_path()
    if not path.exists():
        return False
    text = path.read_text()
    if MARKER not in text:
        return False
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        if MARKER not in line:
            out.append(line)
        elif _WAS_TRUE in line:
            out.append(_MARKED_FALSE_RE.sub(r"true\1", line).rstrip() + "\n")
    new = "".join(out)
    if parse_jsonc(new) is None:
        print(
            f"cmux config would not parse after restore; remove the cockpit line in {path} by hand"
        )
        return False
    _write(path, new)
    _reload()
    print(f"restored cmux's native sidebar PR row -> {path}")
    return True
