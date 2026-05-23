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
from pathlib import Path

DEFAULTS = Path(__file__).resolve().parent.parent.parent / "scripts" / "defaults"

import scripts.lib.config as config_mod  # noqa: E402


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
        "[custom.branch_identity]",
        "[custom.worktree_status]",
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


def test_footer_install_is_idempotent_and_announces_state(
    tmp_path, monkeypatch, capsys
):
    """`cockpit --footer` must be verbose AND idempotent: a re-run on
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


from tests.asserts import expected_starship as _expected_starship  # noqa: E402
from tests.fixtures import (  # noqa: E402
    make_bin_on_path as _make_bin_on_path,
    setup_cockpit_config as _setup_cockpit_config,
)

_STATUSLINE_CMD = "/path/to/footer.py"


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
    assert "[custom.linear]" in starship_default
    assert "[custom.pr_state]" in starship_default
    # The eight custom modules whose chain commit 8ab5889 broke.
    for mod in (
        "custom.context",
        "custom.session_time",
        "custom.ratelimit",
        "custom.linear",
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
    deleted dotfiles file left behind), --footer must replace it with a real
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
    """If the symlink resolves to a real file, --footer backs that file up
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
