"""Static guards on the bundled defaults under scripts/defaults/ + a
plug-and-play roundtrip that drives the real `cockpit --footer` install
helpers against a tmp $XDG_CONFIG_HOME.

These configs ship with the plugin and only get copied to
`~/.config/{cship,starship}.toml` when the user runs `cockpit --footer`.
A regression here silently breaks every install on the next plugin
update — long after the test would have flagged it if we had one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULTS = Path(__file__).resolve().parent.parent / "scripts" / "defaults"
REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import lib.config as config_mod  # noqa: E402


def _strip_comments(toml_body: str) -> str:
    """Strip `#`-comments so static asserts only see real TOML directives."""
    return "\n".join(line.split("#", 1)[0] for line in toml_body.splitlines())


def test_cship_toml_does_not_duplicate_cwd():
    """`$cship.workspace.current_dir` prints the absolute cwd, duplicating
    Claude Code's own header. Keep it out of the bundled config."""
    body = _strip_comments((DEFAULTS / "cship.toml").read_text())
    assert "current_dir" not in body, (
        "cship.toml reintroduced `$cship.workspace.current_dir` — "
        "Claude Code's header already shows the cwd and cship "
        "renders the unabbreviated absolute path on top of it."
    )


def test_cship_toml_uses_lines_wrapper_schema():
    """cship 1.7.x silently falls back to a no-op renderer if the config
    uses the legacy `format = "..."` top-level layout. The `[cship]/lines`
    wrapper is mandatory for `$starship_prompt` to expand."""
    body = (DEFAULTS / "cship.toml").read_text()
    assert "[cship]" in body
    assert "lines = " in body
    assert "$starship_prompt" in body


def test_starship_toml_declares_expected_pills():
    """All bundled pill modules must stay declared — silent drops of a
    `[custom.*]` section is how Bug B-class regressions sneak in."""
    body = (DEFAULTS / "starship.toml").read_text()
    for name in (
        "[custom.context]",
        "[custom.session_time]",
        "[custom.ratelimit]",
        "[custom.model]",
        "[custom.permission_mode]",
        "[custom.branch_pill]",
        "[custom.linear]",
        "[custom.pr_state]",
        "[custom.pr_num]",
        "[custom.pr_checks]",
        "[custom.pr_title]",
    ):
        assert name in body, f"starship.toml missing {name}"
    assert "[custom.commit_age]" not in body, "commit_age block must be removed"
    assert body.index("[custom.model]") < body.index(
        "[custom.context]"
    ), "[custom.model] must come before [custom.context] in starship.toml"
    model_block_start = body.index("[custom.model]")
    next_block = body.index("\n[custom.", model_block_start + 1)
    assert (
        "🤖" in body[model_block_start:next_block]
    ), "[custom.model].format must include the 🤖 icon"
    linear_block_start = body.index("[custom.linear]")
    linear_next = body.index("\n[custom.", linear_block_start + 1)
    assert (
        "◫" in body[linear_block_start:linear_next]
    ), "[custom.linear].format must include the ◫ icon"


def test_starship_toml_drops_time_pill():
    """The wall-clock pill was removed in favor of more useful session
    state. Guard against accidental reintroduction."""
    body = _strip_comments((DEFAULTS / "starship.toml").read_text())
    assert "${time}" not in body, "${time} pill reintroduced"
    assert "[time]" not in body, "[time] section reintroduced"


def test_starship_toml_pr_identity_on_line_two():
    """PR/Linear identity (linear + pr_state + pr_num + pr_checks +
    pr_title) lives on line two of the format string so the metric
    strip stays uniform across sessions."""
    body = _strip_comments((DEFAULTS / "starship.toml").read_text())
    fmt_start = body.index('format = """')
    fmt_end = body.index('"""', fmt_start + 12)
    fmt = body[fmt_start:fmt_end]
    lines = [ln for ln in fmt.replace("\\\n", "").split("\n") if "${custom" in ln]
    assert len(lines) >= 2, f"format should have at least 2 lines, got {lines!r}"
    line_two = lines[1]
    for token in (
        "${custom.linear}",
        "${custom.pr_state}",
        "${custom.pr_num}",
        "${custom.pr_checks}",
        "${custom.pr_title}",
    ):
        assert token in line_two, f"line 2 missing {token}: {line_two!r}"


def test_starship_toml_uses_placeholder_for_dispatcher_path():
    """`__COCKPIT_STARSHIP__` gets substituted at install time by
    `install_starship_default_config()`. If a hardcoded absolute path
    creeps in, the install on someone else's machine breaks silently."""
    body = (DEFAULTS / "starship.toml").read_text()
    assert "__COCKPIT_STARSHIP__" in body
    assert "/Users/" not in body, "starship.toml has a hardcoded macOS home path"
    assert "/home/" not in body, "starship.toml has a hardcoded linux home path"


def test_footer_install_roundtrip_overwrites_stale_user_config(tmp_path, monkeypatch):
    """Plug-and-play: when the user reinstalls the plugin and runs
    `cockpit --footer`, the bundled defaults MUST clobber whatever's
    already at `~/.config/{cship,starship}.toml` — otherwise the user
    is stuck on stale config (e.g. the pre-fix `current_dir` line) and
    the fix doesn't take effect until they hand-edit. This test drives
    the real install helpers against a tmp $XDG_CONFIG_HOME with stale
    files in place and asserts they're overwritten with the new
    bundled content."""
    xdg = tmp_path / "config"
    xdg.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    cockpit_home = tmp_path / "cockpit"
    cockpit_home.mkdir()
    (cockpit_home / "config.json").write_text(json.dumps({"use_cship": True}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("COCKPIT_HOME", str(cockpit_home))

    # Plant a stale cship.toml containing the pre-fix `current_dir` line —
    # this is the exact regression we want to be sure the install flow
    # blows away.
    stale_cship = xdg / "cship.toml"
    stale_cship.write_text(
        '[cship]\nlines = ["$cship.workspace.current_dir$starship_prompt"]\n'
    )
    stale_starship = xdg / "starship.toml"
    stale_starship.write_text("format = 'STALE'\n")

    # Reload the config module so XDG_CONFIG_HOME / COCKPIT_HOME are picked
    # up by module-level path constants captured at import time.
    import importlib

    importlib.reload(config_mod)

    config_mod.install_cship_default_config()
    config_mod.install_starship_default_config()

    new_cship = (xdg / "cship.toml").read_text()
    new_starship = (xdg / "starship.toml").read_text()

    # Stale `current_dir` is gone; new wrapper schema is in place.
    assert "current_dir" not in _strip_comments(new_cship)
    assert 'lines = ["$starship_prompt"]' in new_cship

    # Stale starship.toml is replaced with the bundled defaults, with the
    # `__COCKPIT_STARSHIP__` placeholder substituted to an absolute path.
    assert "STALE" not in new_starship
    assert (
        "__COCKPIT_STARSHIP__" not in new_starship
    ), "placeholder must be substituted at install time"
    assert "[custom.context]" in new_starship
