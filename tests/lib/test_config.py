"""Static guards on the bundled defaults under cockpit/defaults/ + a
plug-and-play roundtrip that drives the real `cockpit setup` install
helpers against a tmp $XDG_CONFIG_HOME.

These configs ship with the plugin and only get copied to
`~/.config/{cship,starship}.toml` when the user runs `cockpit setup`.
A regression here silently breaks every install on the next plugin
update — long after the test would have flagged it if we had one.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULTS = Path(__file__).resolve().parent.parent.parent / "cockpit" / "defaults"

import cockpit.lib.config as config_mod  # noqa: E402


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


def test_statusline_fields_match_toml_modules():
    """Every `STATUSLINE_FIELDS` entry (the hide/validate whitelist) maps to a
    shipped `[custom.*]` module and vice-versa. A new pill added to starship.toml
    without a matching field can't be hidden or validated — this catches the drift.
    """
    import re

    body = (DEFAULTS / "starship.toml").read_text()
    # toml module names use `_`; field names use `-`, and `ratelimit` → `rate-limit`.
    modules = {
        m.replace("_", "-").replace("ratelimit", "rate-limit")
        for m in re.findall(r"\[custom\.([a-z_]+)\]", body)
    }
    assert modules == set(config_mod.STATUSLINE_FIELDS)


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
        "[custom.branch_identity]",
        "[custom.worktree_status]",
        "[custom.ticket]",
        "[custom.pr_state]",
        "[custom.pr_num]",
        "[custom.pr_comments]",
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
    ticket_block_start = body.index("[custom.ticket]")
    ticket_next = body.index("\n[custom.", ticket_block_start + 1)
    assert (
        "◫" in body[ticket_block_start:ticket_next]
    ), "[custom.ticket].format must include the ◫ icon"


def test_starship_toml_drops_time_pill():
    """The wall-clock pill was removed in favor of more useful session
    state. Guard against accidental reintroduction."""
    body = _strip_comments((DEFAULTS / "starship.toml").read_text())
    assert "${time}" not in body, "${time} pill reintroduced"
    assert "[time]" not in body, "[time] section reintroduced"


def test_starship_toml_pr_identity_on_line_two():
    """PR/ticket identity (ticket + pr_state + pr_num + pr_checks +
    pr_title) lives on line two of the format string so the metric
    strip stays uniform across sessions."""
    # The line-2 break is the `__COCKPIT_LINE_SEP__` token in the default,
    # substituted to a real newline off-macOS at install time. Expand it here
    # to assert the intended (non-macOS) two-line structure.
    body = _strip_comments((DEFAULTS / "starship.toml").read_text()).replace(
        "__COCKPIT_LINE_SEP__", "\n"
    )
    fmt_start = body.index('format = """')
    fmt_end = body.index('"""', fmt_start + 12)
    fmt = body[fmt_start:fmt_end]
    lines = [ln for ln in fmt.replace("\\\n", "").split("\n") if "${custom" in ln]
    assert len(lines) >= 2, f"format should have at least 2 lines, got {lines!r}"
    line_two = lines[1]
    for token in (
        "${custom.ticket}",
        "${custom.pr_state}",
        "${custom.pr_num}",
        "${custom.pr_comments}",
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


def test_starship_toml_declares_theme_palettes():
    """The themed neutral-grey styles must reference the `palette` roles, and
    both palettes must define every role used. A bare `fg:243` creeping back in
    silently un-themes that pill; a missing palette entry makes starship drop
    the style entirely."""
    body = (DEFAULTS / "starship.toml").read_text()
    assert 'palette = "__COCKPIT_THEME__"' in body
    for pal in ("[palettes.dark]", "[palettes.light]"):
        assert pal in body, f"starship.toml missing {pal}"
        block = body[body.index(pal) :]
        block = block[: block.index("\n[", 1)]
        assert "text_primary" in block, f"{pal} missing text_primary"
        assert "text_muted" in block, f"{pal} missing text_muted"
    # The four themed pills point at palette roles, not hardcoded indices.
    for role in ("fg:text_primary", "fg:text_muted"):
        assert role in body, f"no style references {role}"
    # Saturated styles stay literal — they are legible on both backgrounds.
    assert "bold fg:172" in body, "permission_mode style must stay literal fg:172"
    assert "bold fg:91" in body, "linear style must stay literal fg:91"


def test_footer_install_roundtrip_overwrites_stale_user_config(tmp_path, monkeypatch):
    """Plug-and-play: when the user reinstalls the plugin and runs
    `cockpit setup`, the bundled defaults MUST clobber whatever's
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


def test_footer_install_is_idempotent_and_announces_state(
    tmp_path, monkeypatch, capsys
):
    """`cockpit setup` must be verbose AND idempotent: a re-run on
    already-installed defaults rewrites nothing but explicitly reports
    each target as `unchanged, default kept at <path>`. Silent no-ops
    are not acceptable — the user needs confirmation the command ran."""
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

    import importlib

    importlib.reload(config_mod)

    config_mod.install_cship_default_config()
    config_mod.install_starship_default_config()

    cship_path = xdg / "cship.toml"
    starship_path = xdg / "starship.toml"
    cship_mtime = cship_path.stat().st_mtime_ns
    starship_mtime = starship_path.stat().st_mtime_ns
    cship_bytes = cship_path.read_bytes()
    starship_bytes = starship_path.read_bytes()

    capsys.readouterr()  # drain first-install output

    config_mod.install_cship_default_config()
    config_mod.install_starship_default_config()

    out = capsys.readouterr().out
    assert f"cship config unchanged, default kept at {cship_path}" in out
    assert f"starship config unchanged, default kept at {starship_path}" in out
    assert (
        "installed default" not in out
    ), "no-op re-run must not claim it installed anything"

    assert cship_path.stat().st_mtime_ns == cship_mtime
    assert starship_path.stat().st_mtime_ns == starship_mtime
    assert cship_path.read_bytes() == cship_bytes
    assert starship_path.read_bytes() == starship_bytes


def test_theme_substituted_at_install(tmp_path, monkeypatch):
    """`theme: light` in config seeds `palette = "light"` into the installed
    starship.toml; the default config seeds `palette = "dark"`. The placeholder
    must never survive into the installed file."""
    for theme, expected in (("light", "light"), (None, "dark"), ("bogus", "dark")):
        cfg = {"repos": [], "use_cship": True}
        if theme is not None:
            cfg["theme"] = theme
        cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, cfg)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        cockpit_config.install_starship_default_config()
        installed = (tmp_path / "xdg" / "starship.toml").read_text()
        assert f'palette = "{expected}"' in installed, f"theme={theme!r}"
        assert "__COCKPIT_THEME__" not in installed


def test_line_sep_collapses_to_single_line_on_macos(tmp_path, monkeypatch):
    """macOS drops line 2 of a multi-line statusLine (claude-code#35176), so
    `install_starship_default_config()` substitutes the line-break token with
    empty on darwin (one-line footer) and a real newline elsewhere. The
    placeholder must never survive into the installed file either way."""
    import tomllib

    for platform, expect_two_lines in (("darwin", False), ("linux", True)):
        cfg = {"repos": [], "use_cship": True}
        (tmp_path / platform).mkdir()
        cockpit_config = _setup_cockpit_config(tmp_path / platform, monkeypatch, cfg)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / platform / "xdg"))
        monkeypatch.setattr(config_mod.sys, "platform", platform)
        cockpit_config.install_starship_default_config()
        installed = (tmp_path / platform / "xdg" / "starship.toml").read_text()
        assert "__COCKPIT_LINE_SEP__" not in installed, platform
        fmt = tomllib.loads(installed)["format"]
        has_break = "\n" in fmt
        assert has_break is expect_two_lines, f"{platform}: fmt={fmt!r}"
        # The PR pills must still be present regardless of layout.
        assert "${custom.pr_num}" in fmt, platform


def test_resolve_theme_validates():
    """resolve_theme accepts only dark|light, defaulting everything else to
    dark so a typo can never blank out the palette."""
    assert config_mod.resolve_theme({"theme": "dark"}) == "dark"
    assert config_mod.resolve_theme({"theme": "light"}) == "light"
    assert config_mod.resolve_theme({"theme": "neon"}) == "dark"
    assert config_mod.resolve_theme({}) == "dark"


def test_resolve_tui_theme_defaults_and_passthrough():
    """tui_theme passes any non-empty string through (validation against the
    registered Textual themes is the App's job); missing/blank → the default."""
    assert config_mod.resolve_tui_theme({"tui_theme": "nord"}) == "nord"
    assert config_mod.resolve_tui_theme({}) == config_mod.TUI_THEME_DEFAULT
    assert (
        config_mod.resolve_tui_theme({"tui_theme": ""}) == config_mod.TUI_THEME_DEFAULT
    )
    assert (
        config_mod.resolve_tui_theme({"tui_theme": 5}) == config_mod.TUI_THEME_DEFAULT
    )


def test_save_tui_theme_roundtrips_and_preserves_keys(tmp_path, monkeypatch):
    """save_tui_theme writes `tui_theme` atomically, keeps every other key, and
    drops the per-process cache so the next load_config() sees it."""
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [{"name": "a"}], "theme": "light"}
    )
    cockpit_config.save_tui_theme("gruvbox")
    on_disk = json.loads((tmp_path / "config.json").read_text())
    assert on_disk["tui_theme"] == "gruvbox"
    assert on_disk["theme"] == "light"  # untouched
    assert on_disk["repos"] == [{"name": "a"}]
    # Cache was reset → fresh read reflects the write.
    assert cockpit_config.resolve_tui_theme(cockpit_config.load_config()) == "gruvbox"


def test_save_tui_theme_noop_when_unchanged(tmp_path, monkeypatch):
    """An unchanged value doesn't rewrite the file (no churn on the startup
    apply, which sets the theme to whatever is already saved)."""
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "tui_theme": "nord"}
    )
    before = (tmp_path / "config.json").stat().st_mtime_ns
    cockpit_config.save_tui_theme("nord")
    after = (tmp_path / "config.json").stat().st_mtime_ns
    assert before == after


from tests.asserts import expected_starship as _expected_starship  # noqa: E402
from tests.fixtures import (  # noqa: E402
    make_bin_on_path as _make_bin_on_path,
)
from tests.fixtures import (  # noqa: E402
    setup_cockpit_config as _setup_cockpit_config,
)

_STATUSLINE_CMD = "/path/to/footer.py"


# ── ensure_state_dirs first-run seeding ─────────────────────────────────────


def test_ensure_state_dirs_seeds_empty_repos_on_first_run(tmp_path, monkeypatch):
    # First run must NOT copy config.example.json — its placeholder repos
    # (fake /absolute/path/to/... paths) would error on every daemon tick
    # forever, since registry.register_cwd only appends.
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path / "cockpit"))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    import importlib

    importlib.reload(config_mod)

    config_mod.ensure_state_dirs()

    on_disk = json.loads((tmp_path / "cockpit" / "config.json").read_text())
    assert on_disk == {"repos": []}


def test_ensure_state_dirs_never_overwrites_existing_config(tmp_path, monkeypatch):
    cockpit_home = tmp_path / "cockpit"
    cockpit_home.mkdir()
    (cockpit_home / "config.json").write_text(json.dumps({"repos": [{"name": "a"}]}))
    monkeypatch.setenv("COCKPIT_HOME", str(cockpit_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    import importlib

    importlib.reload(config_mod)

    config_mod.ensure_state_dirs()

    on_disk = json.loads((cockpit_home / "config.json").read_text())
    assert on_disk == {"repos": [{"name": "a"}]}


# ── use_cship gating ────────────────────────────────────────────────────────


def test_use_cship_noop_when_flag_unset(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": False}
    )
    _make_bin_on_path(tmp_path, monkeypatch, "cship")
    cockpit_config.install_cship_statusline_if_configured(_STATUSLINE_CMD)
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_use_cship_raises_when_cship_missing(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    import pytest

    with pytest.raises(cockpit_config.CshipNotInstalledError):
        cockpit_config.install_cship_statusline_if_configured(_STATUSLINE_CMD)
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_use_cship_writes_footer_command(tmp_path, monkeypatch):
    import json as _json

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _make_bin_on_path(tmp_path, monkeypatch, "cship")
    cockpit_config.install_cship_statusline_if_configured(_STATUSLINE_CMD)

    settings = _json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["statusLine"] == {"type": "command", "command": _STATUSLINE_CMD}


def test_use_cship_skips_if_already_set(tmp_path, monkeypatch):
    import json as _json

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _make_bin_on_path(tmp_path, monkeypatch, "cship")

    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        _json.dumps({"statusLine": {"type": "command", "command": _STATUSLINE_CMD}})
    )

    cockpit_config.install_cship_statusline_if_configured(_STATUSLINE_CMD)

    backups = list(claude_dir.glob("settings.json.bak.*"))
    assert backups == []


def test_use_cship_backs_up_existing_statusline(tmp_path, monkeypatch):
    import json as _json

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _make_bin_on_path(tmp_path, monkeypatch, "cship")

    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        _json.dumps({"statusLine": {"type": "command", "command": "/old/statusline"}})
    )

    cockpit_config.install_cship_statusline_if_configured(_STATUSLINE_CMD)

    backups = list(claude_dir.glob("settings.json.bak.*"))
    assert len(backups) == 1
    assert "/old/statusline" in backups[0].read_text()
    new = _json.loads((claude_dir / "settings.json").read_text())
    assert new["statusLine"]["command"] == _STATUSLINE_CMD


# ── default cship.toml seeding ──────────────────────────────────────────────


def test_cship_default_noop_when_flag_unset(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": False}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cockpit_config.install_cship_default_config()
    assert not (tmp_path / "xdg" / "cship.toml").exists()


def test_cship_default_installed_when_missing(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cockpit_config.install_cship_default_config()
    dest = tmp_path / "xdg" / "cship.toml"
    assert dest.exists()
    assert dest.read_text() == cockpit_config.CSHIP_DEFAULT_TOML.read_text()


def test_cship_default_overwrites_existing(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    dest = tmp_path / "xdg" / "cship.toml"
    dest.parent.mkdir(parents=True)
    dest.write_text("# my custom cship config\n[time]\ndisabled = true\n")
    cockpit_config.install_cship_default_config()
    assert dest.read_text() == cockpit_config.CSHIP_DEFAULT_TOML.read_text()


def test_cship_default_missing_package_file_is_soft_fail(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(
        cockpit_config, "CSHIP_DEFAULT_TOML", tmp_path / "does-not-exist.toml"
    )
    cockpit_config.install_cship_default_config()
    assert not (tmp_path / "xdg" / "cship.toml").exists()


def test_cship_default_honors_xdg_config_home(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "elsewhere"))
    cockpit_config.install_cship_default_config()
    assert (tmp_path / "elsewhere" / "cship.toml").exists()
    assert not (tmp_path / ".config" / "cship.toml").exists()


# ── default starship.toml seeding ───────────────────────────────────────────


def test_starship_default_noop_when_flag_unset(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": False}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cockpit_config.install_starship_default_config()
    assert not (tmp_path / "xdg" / "starship.toml").exists()


def test_starship_default_installed_when_missing(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cockpit_config.install_starship_default_config()
    dest = tmp_path / "xdg" / "starship.toml"
    assert dest.exists()
    assert dest.read_text() == _expected_starship(cockpit_config)


def test_starship_default_overwrites_existing(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    dest = tmp_path / "xdg" / "starship.toml"
    dest.parent.mkdir(parents=True)
    dest.write_text("# my custom starship config\nformat = ''\n")
    cockpit_config.install_starship_default_config()
    assert dest.read_text() == _expected_starship(cockpit_config)


def test_starship_default_missing_package_file_is_soft_fail(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(
        cockpit_config, "STARSHIP_DEFAULT_TOML", tmp_path / "does-not-exist.toml"
    )
    cockpit_config.install_starship_default_config()
    assert not (tmp_path / "xdg" / "starship.toml").exists()


def test_starship_default_renders_custom_modules_via_starship_prompt(
    tmp_path, monkeypatch
):
    """The bundled cship.toml must reference $starship_prompt; otherwise cship's
    line renderer ignores [custom.*] and the chain is dead even with both files
    installed. Pin this so the two configs can't silently drift apart again.
    """
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    cship_default = cockpit_config.CSHIP_DEFAULT_TOML.read_text()
    starship_default = cockpit_config.STARSHIP_DEFAULT_TOML.read_text()
    assert "$starship_prompt" in cship_default
    assert "[custom.ticket]" in starship_default
    assert "[custom.pr_state]" in starship_default
    # The eight custom modules whose chain commit 8ab5889 broke.
    for mod in (
        "custom.context",
        "custom.session_time",
        "custom.ratelimit",
        "custom.ticket",
        "custom.pr_state",
        "custom.pr_num",
        "custom.pr_checks",
        "custom.pr_title",
    ):
        assert f"[{mod}]" in starship_default, f"{mod} missing from starship.toml"
        assert (
            mod not in cship_default
        ), f"{mod} still defined in cship.toml — cship cannot render [custom.*]"


# ── symlink-aware seeding ───────────────────────────────────────────────────


def test_seed_replaces_dangling_symlink_with_real_file(tmp_path, monkeypatch):
    """If ~/.config/starship.toml is a dangling symlink (the exact state the
    deleted dotfiles file left behind), --setup must replace it with a real
    file rather than write through to the missing target.
    """
    import os

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    missing_target = tmp_path / "dotfiles" / "starship.toml"  # never created
    dest = xdg / "starship.toml"
    os.symlink(missing_target, dest)
    assert dest.is_symlink()
    assert not dest.exists()  # dangling

    cockpit_config.install_starship_default_config()

    assert dest.exists()
    assert not dest.is_symlink()
    assert dest.read_text() == _expected_starship(cockpit_config)
    # Target never existed, so nothing to back up.
    assert not missing_target.exists()
    assert not (tmp_path / "dotfiles").exists()


def test_seed_backs_up_live_symlink_target(tmp_path, monkeypatch):
    """If the symlink resolves to a real file, --setup backs that file up
    before unlinking the symlink and writing the bundled default."""
    import os

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    target_dir = tmp_path / "dotfiles"
    target_dir.mkdir()
    target = target_dir / "starship.toml"
    target.write_text("# user's existing dotfiles content\n")
    dest = xdg / "starship.toml"
    os.symlink(target, dest)

    cockpit_config.install_starship_default_config()

    assert dest.exists()
    assert not dest.is_symlink()
    assert dest.read_text() == _expected_starship(cockpit_config)
    # Original target moved aside, not deleted.
    assert not target.exists()
    backups = list(target_dir.glob("starship.toml.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "# user's existing dotfiles content\n"


def test_seed_replaces_dangling_cship_symlink(tmp_path, monkeypatch):
    """Same symlink-aware behavior for cship.toml — both installers share the
    same _seed_default_toml helper, but pin cship.toml independently."""
    import os

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    dest = xdg / "cship.toml"
    os.symlink(tmp_path / "nowhere" / "cship.toml", dest)

    cockpit_config.install_cship_default_config()

    assert dest.exists()
    assert not dest.is_symlink()
    assert dest.read_text() == cockpit_config.CSHIP_DEFAULT_TOML.read_text()


# ── tickets reader (replaced the old use_linear bool) ───────────────────────


def test_tickets_defaults_none_when_unset(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.tickets() == "none"


def test_tickets_string_shorthand(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "tickets": "github"}
    )
    assert cockpit_config.tickets() == "github"


def test_tickets_object_provider(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "tickets": {"provider": "github"}}
    )
    assert cockpit_config.tickets() == "github"


def test_tickets_unrecognized_falls_back_to_none(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "tickets": "gitlab"}
    )
    assert cockpit_config.tickets() == "none"


def test_repo_tickets_repo_override_wins(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "tickets": "linear"}
    )
    assert (
        cockpit_config.repo_tickets(repo_entry={"tickets": {"provider": "github"}})
        == "github"
    )


def test_repo_tickets_falls_back_to_global(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "tickets": {"provider": "github"}}
    )
    assert cockpit_config.repo_tickets(repo_entry={}) == "github"


def test_repo_tickets_linear_keys_back_compat(tmp_path, monkeypatch):
    # A repo with linear_keys but no `tickets` anywhere keeps Linear (back-compat).
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.repo_tickets(repo_entry={"linear_keys": ["PE"]}) == "linear"


def test_repo_tickets_defaults_none(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.repo_tickets(repo_entry={}) == "none"


def test_repo_tickets_explicit_provider_wins_over_legacy_linear_keys(
    tmp_path, monkeypatch
):
    """A repo with BOTH the legacy flat `linear_keys` AND an explicit
    `tickets.provider` must resolve to the explicit provider — `repo_tickets`
    only falls back to the `linear_keys` back-compat guess when no provider is
    set anywhere (see its docstring's resolution order)."""
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    re = {"linear_keys": ["PE"], "tickets": {"provider": "github"}}
    assert cockpit_config.repo_tickets(repo_entry=re) == "github"


# ── find_repo_by_nwo (owner/name → registered repo, via origin remote) ──────


def _git_repo_with_remote(tmp_path: Path, monkeypatch, url: str) -> Path:
    import subprocess

    from tests.conftest import _GIT_ENV_LEAKS

    # Strip GIT_DIR etc. from the whole test process — under a pre-push hook
    # they point every git call (ours AND find_repo_by_nwo's) at the OUTER
    # cockpit repo, not the tmp_path repo.
    for var in _GIT_ENV_LEAKS:
        monkeypatch.delenv(var, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", url], check=True)
    return repo


def test_find_repo_by_nwo_matches_ssh_remote(tmp_path, monkeypatch):
    repo = _git_repo_with_remote(tmp_path, monkeypatch, "git@github.com:Owner/Repo.git")
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [{"name": "r", "path": str(repo)}]}
    )
    found = cockpit_config.find_repo_by_nwo("Owner/Repo")
    assert found is not None
    assert found["name"] == "r"


def test_find_repo_by_nwo_matches_https_remote_no_git_suffix(tmp_path, monkeypatch):
    repo = _git_repo_with_remote(tmp_path, monkeypatch, "https://github.com/owner/repo")
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [{"name": "r", "path": str(repo)}]}
    )
    found = cockpit_config.find_repo_by_nwo("owner/repo")
    assert found is not None
    assert found["name"] == "r"


def test_find_repo_by_nwo_case_insensitive(tmp_path, monkeypatch):
    repo = _git_repo_with_remote(
        tmp_path, monkeypatch, "https://github.com/Owner/Repo.git"
    )
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [{"name": "r", "path": str(repo)}]}
    )
    assert cockpit_config.find_repo_by_nwo("owner/REPO") is not None


def test_find_repo_by_nwo_no_match_returns_none(tmp_path, monkeypatch):
    repo = _git_repo_with_remote(
        tmp_path, monkeypatch, "https://github.com/owner/repo.git"
    )
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [{"name": "r", "path": str(repo)}]}
    )
    assert cockpit_config.find_repo_by_nwo("someone/else") is None


def test_find_repo_by_nwo_skips_missing_path(tmp_path, monkeypatch):
    """A configured repo whose path no longer exists on disk is skipped rather
    than raising from the `git config` subprocess call."""
    cockpit_config = _setup_cockpit_config(
        tmp_path,
        monkeypatch,
        {"repos": [{"name": "r", "path": str(tmp_path / "gone")}]},
    )
    assert cockpit_config.find_repo_by_nwo("owner/repo") is None


# ── review_command (review_prs first-turn slash command) ────────────────────


def test_review_command_defaults_to_plugin_command(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.review_command() == "/cockpit:review"


def test_review_command_repo_override_wins(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "review_command": "/review"}
    )
    assert (
        cockpit_config.review_command(repo_entry={"review_command": "/pr-review"})
        == "/pr-review"
    )


def test_review_command_falls_back_to_global(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "review_command": "/pr-review"}
    )
    assert cockpit_config.review_command(repo_entry={}) == "/pr-review"


def test_review_command_blank_falls_through_to_default(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert (
        cockpit_config.review_command(repo_entry={"review_command": "  "})
        == "/cockpit:review"
    )


def test_base_remote_defaults_to_origin(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.base_remote() == "origin"


def test_base_remote_repo_override_wins(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "base_remote": "up-global"}
    )
    assert (
        cockpit_config.base_remote(repo_entry={"base_remote": "upstream"}) == "upstream"
    )


def test_base_remote_falls_back_to_global(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "base_remote": "upstream"}
    )
    assert cockpit_config.base_remote(repo_entry={}) == "upstream"


def test_base_remote_blank_falls_through_to_default(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.base_remote(repo_entry={"base_remote": "  "}) == "origin"


# ── statusline_hide ─────────────────────────────────────────────────────────


def test_statusline_hidden_defaults_empty(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.statusline_hidden() == set()


def test_statusline_hidden_reads_list(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path,
        monkeypatch,
        {"repos": [], "statusline_hide": ["cost", "session-time"]},
    )
    assert cockpit_config.statusline_hidden() == {"cost", "session-time"}


def test_statusline_hidden_ignores_blanks_and_non_strings(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "statusline_hide": ["cost", "  ", 7]}
    )
    assert cockpit_config.statusline_hidden() == {"cost"}


def test_statusline_hidden_non_list_is_empty(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "statusline_hide": "cost"}
    )
    assert cockpit_config.statusline_hidden() == set()


# ── tickets object: dev_done labels + close_on_merge ────────────────────────


def test_github_dev_done_label_defaults(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.github_dev_done_label() == "ready for review"


def test_github_dev_done_label_object_override(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path,
        monkeypatch,
        {"repos": [], "tickets": {"provider": "github", "dev_done_label": "qa ok"}},
    )
    assert cockpit_config.github_dev_done_label() == "qa ok"


def test_ticket_close_on_merge_global_default_applies(tmp_path, monkeypatch):
    # A global tickets.close_on_merge applies to a repo whose own block omits it.
    cockpit_config = _setup_cockpit_config(
        tmp_path,
        monkeypatch,
        {"repos": [], "tickets": {"close_on_merge": True}},
    )
    assert (
        cockpit_config.ticket_close_on_merge(
            repo_entry={"tickets": {"provider": "github"}}
        )
        is True
    )


def test_github_start_label_defaults_none(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.github_start_label() is None


def test_github_start_label_from_object(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    re = {"tickets": {"provider": "github", "start_label": "accepted"}}
    assert cockpit_config.github_start_label(repo_entry=re) == "accepted"


def test_jira_readers_defaults(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.jira_site_url() == ""
    assert cockpit_config.jira_email() == ""
    assert cockpit_config.jira_dev_done_status() == "Dev Done"
    assert cockpit_config.jira_merge_done_status() == "Done"


def test_jira_site_url_strips_trailing_slash(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path,
        monkeypatch,
        {
            "repos": [],
            "tickets": {
                "provider": "jira",
                "site_url": "https://acme.atlassian.net/",
            },
        },
    )
    assert cockpit_config.jira_site_url() == "https://acme.atlassian.net"


def test_jira_status_overrides_from_object(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    re = {
        "tickets": {
            "provider": "jira",
            "email": "me@acme.com",
            "dev_done_status": "In Review",
            "merge_done_status": "Closed",
        }
    }
    assert cockpit_config.jira_email(repo_entry=re) == "me@acme.com"
    assert cockpit_config.jira_dev_done_status(repo_entry=re) == "In Review"
    assert cockpit_config.jira_merge_done_status(repo_entry=re) == "Closed"


def test_trello_readers_default_to_empty_string(tmp_path, monkeypatch):
    # No default list name to guess — an unset value means the feature is off.
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.trello_dev_done_list() == ""
    assert cockpit_config.trello_merge_done_list() == ""


def test_trello_readers_repo_override_wins(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path,
        monkeypatch,
        {
            "repos": [],
            "tickets": {
                "provider": "trello",
                "dev_done_list": "Global Ready",
                "merge_done_list": "Global Done",
            },
        },
    )
    re = {
        "tickets": {
            "provider": "trello",
            "dev_done_list": "Ready for Review",
            "merge_done_list": "Shipped",
        }
    }
    assert cockpit_config.trello_dev_done_list(repo_entry=re) == "Ready for Review"
    assert cockpit_config.trello_merge_done_list(repo_entry=re) == "Shipped"


def test_trello_readers_fall_back_to_global(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path,
        monkeypatch,
        {
            "repos": [],
            "tickets": {
                "provider": "trello",
                "dev_done_list": "Global Ready",
                "merge_done_list": "Global Done",
            },
        },
    )
    assert cockpit_config.trello_dev_done_list(repo_entry={}) == "Global Ready"
    assert cockpit_config.trello_merge_done_list(repo_entry={}) == "Global Done"


def test_ticket_close_on_merge_defaults_false(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.ticket_close_on_merge() is False


def test_ticket_close_on_merge_from_object(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert (
        cockpit_config.ticket_close_on_merge(
            repo_entry={"tickets": {"provider": "github", "close_on_merge": True}}
        )
        is True
    )


def test_ticket_close_on_merge_legacy_linear_flat_key(tmp_path, monkeypatch):
    # Existing Linear configs keep working without migrating to the object form.
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "linear_done_on_merge": True}
    )
    assert cockpit_config.ticket_close_on_merge() is True


def test_linear_dev_done_from_object(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    re = {"tickets": {"provider": "linear", "dev_done_state": "In Review"}}
    assert cockpit_config.linear_dev_done_state(repo_entry=re) == "In Review"


def test_linear_merge_done_from_object(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    re = {"tickets": {"provider": "linear", "merge_done_state": "Shipped"}}
    assert cockpit_config.linear_merge_done_state(repo_entry=re) == "Shipped"


def test_linear_team_keys_from_object(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    re = {"tickets": {"provider": "linear", "keys": ["PE", "ENG"]}}
    assert cockpit_config.linear_team_keys(repo_entry=re) == ["PE", "ENG"]


def test_linear_team_keys_legacy_flat_fallback(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.linear_team_keys(repo_entry={"linear_keys": ["PE"]}) == ["PE"]


# ── use_slack reader ─────────────────────────────────────────────────────────


def test_use_slack_defaults_false_when_unset(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.use_slack() is False


def test_use_slack_returns_true_when_set(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_slack": True}
    )
    assert cockpit_config.use_slack() is True


def test_use_slack_returns_false_when_explicitly_false(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_slack": False}
    )
    assert cockpit_config.use_slack() is False


# ── linear_dev_done_state reader ─────────────────────────────────────────────


def test_linear_dev_done_state_defaults(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.linear_dev_done_state() == "Dev Done"


def test_linear_dev_done_state_override(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "linear_dev_done_state": "In Review"}
    )
    assert cockpit_config.linear_dev_done_state() == "In Review"


def test_linear_dev_done_state_uses_passed_cfg_without_disk_read():
    # Passing cfg avoids load_config(); blank/whitespace falls back to default.
    from cockpit.lib import config as cockpit_config

    assert cockpit_config.linear_dev_done_state({"linear_dev_done_state": "QA"}) == "QA"
    assert (
        cockpit_config.linear_dev_done_state({"linear_dev_done_state": "  "})
        == "Dev Done"
    )


# ── linear_merge_done_state reader ───────────────────────────────────────────


def test_linear_merge_done_state_defaults(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(tmp_path, monkeypatch, {"repos": []})
    assert cockpit_config.linear_merge_done_state() == "Done"


def test_linear_merge_done_state_override(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "linear_merge_done_state": "Shipped"}
    )
    assert cockpit_config.linear_merge_done_state() == "Shipped"


def test_linear_merge_done_state_uses_passed_cfg_and_blank_falls_back():
    from cockpit.lib import config as cockpit_config

    assert (
        cockpit_config.linear_merge_done_state({"linear_merge_done_state": "Closed"})
        == "Closed"
    )
    assert (
        cockpit_config.linear_merge_done_state({"linear_merge_done_state": "  "})
        == "Done"
    )


# ── linear_done_on_merge reader (per-repo over global) ───────────────────────


def test_linear_done_on_merge_defaults_false():
    from cockpit.lib import config as cockpit_config

    assert cockpit_config.ticket_close_on_merge({"repos": []}) is False


def test_linear_done_on_merge_global_true():
    from cockpit.lib import config as cockpit_config

    assert cockpit_config.ticket_close_on_merge({"linear_done_on_merge": True}) is True


def test_linear_done_on_merge_repo_overrides_global():
    from cockpit.lib import config as cockpit_config

    cfg = {"linear_done_on_merge": True}
    # Per-repo False wins over a True global.
    assert (
        cockpit_config.ticket_close_on_merge(cfg, {"linear_done_on_merge": False})
        is False
    )
    # And per-repo True wins over a False/absent global.
    assert (
        cockpit_config.ticket_close_on_merge({}, {"linear_done_on_merge": True}) is True
    )


def test_linear_done_on_merge_repo_without_key_falls_back_to_global():
    from cockpit.lib import config as cockpit_config

    cfg = {"linear_done_on_merge": True}
    assert cockpit_config.ticket_close_on_merge(cfg, {"name": "r"}) is True


# ── orphan_nudge_grace_seconds ──────────────────────────────────────────────


def test_orphan_nudge_grace_defaults_to_four_hours():
    from cockpit.lib import config as cockpit_config

    assert cockpit_config.orphan_nudge_grace_seconds({"repos": []}) == 4 * 3600.0


def test_orphan_nudge_grace_global_override():
    from cockpit.lib import config as cockpit_config

    assert (
        cockpit_config.orphan_nudge_grace_seconds({"orphan_nudge_grace_hours": 2})
        == 2 * 3600.0
    )


def test_orphan_nudge_grace_zero_disables():
    from cockpit.lib import config as cockpit_config

    assert (
        cockpit_config.orphan_nudge_grace_seconds({"orphan_nudge_grace_hours": 0})
        == 0.0
    )


def test_orphan_nudge_grace_repo_overrides_global():
    from cockpit.lib import config as cockpit_config

    cfg = {"orphan_nudge_grace_hours": 8}
    # Per-repo 0 (disable) wins over a non-zero global.
    assert (
        cockpit_config.orphan_nudge_grace_seconds(cfg, {"orphan_nudge_grace_hours": 0})
        == 0.0
    )
    # Per-repo value wins over an absent global.
    assert (
        cockpit_config.orphan_nudge_grace_seconds({}, {"orphan_nudge_grace_hours": 1})
        == 3600.0
    )


def test_orphan_nudge_grace_repo_without_key_falls_back_to_global():
    from cockpit.lib import config as cockpit_config

    cfg = {"orphan_nudge_grace_hours": 3}
    assert cockpit_config.orphan_nudge_grace_seconds(cfg, {"name": "r"}) == 3 * 3600.0


def test_orphan_nudge_grace_negative_clamped_to_zero():
    from cockpit.lib import config as cockpit_config

    assert (
        cockpit_config.orphan_nudge_grace_seconds({"orphan_nudge_grace_hours": -5})
        == 0.0
    )


# ── find_repos_by_linear_key ────────────────────────────────────────────────


def _repos_cfg(*repos):
    return {"repos": list(repos)}


def test_find_repos_by_linear_key_single_match(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path,
        monkeypatch,
        _repos_cfg(
            {"name": "alpha", "path": "/a", "linear_keys": ["PE"]},
            {"name": "beta", "path": "/b", "linear_keys": ["ENG"]},
        ),
    )
    matches = cockpit_config.find_repos_by_linear_key("PE-1234")
    assert [r["name"] for r in matches] == ["alpha"]


def test_find_repos_by_linear_key_case_insensitive(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path,
        monkeypatch,
        _repos_cfg({"name": "alpha", "path": "/a", "linear_keys": ["pe"]}),
    )
    assert [r["name"] for r in cockpit_config.find_repos_by_linear_key("PE-1")] == [
        "alpha"
    ]
    assert [r["name"] for r in cockpit_config.find_repos_by_linear_key("pe-1")] == [
        "alpha"
    ]


def test_find_repos_by_linear_key_multiple_matches(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path,
        monkeypatch,
        _repos_cfg(
            {"name": "alpha", "path": "/a", "linear_keys": ["PE"]},
            {"name": "beta", "path": "/b", "linear_keys": ["PE", "ENG"]},
        ),
    )
    names = [r["name"] for r in cockpit_config.find_repos_by_linear_key("PE-1234")]
    assert names == ["alpha", "beta"]


def test_find_repos_by_linear_key_no_match(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path,
        monkeypatch,
        _repos_cfg({"name": "alpha", "path": "/a", "linear_keys": ["ENG"]}),
    )
    assert cockpit_config.find_repos_by_linear_key("PE-1234") == []


def test_find_repos_by_linear_key_missing_field(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path,
        monkeypatch,
        _repos_cfg({"name": "alpha", "path": "/a"}),
    )
    assert cockpit_config.find_repos_by_linear_key("PE-1234") == []


def test_find_repos_by_linear_key_rejects_non_linear_identifier(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path,
        monkeypatch,
        _repos_cfg({"name": "alpha", "path": "/a", "linear_keys": ["PE"]}),
    )
    assert cockpit_config.find_repos_by_linear_key("not-a-key") == []
    assert cockpit_config.find_repos_by_linear_key("PE-") == []
    assert cockpit_config.find_repos_by_linear_key("HTTP-200") == []
