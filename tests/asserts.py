from __future__ import annotations

import sys


def expected_starship(cockpit_config) -> str:
    """starship.toml after install-time placeholder substitution.

    install_starship_default_config() rewrites three placeholders before writing
    to ~/.config/starship.toml: __COCKPIT_STARSHIP__ → the module-dispatch render
    command (interpreter + `-m cockpit.cli starship`), __COCKPIT_THEME__ → the
    validated `theme` from config, and __COCKPIT_LINE_SEP__ → the line-2 break
    (empty on macOS, a newline elsewhere). Assertions about installed content
    must match that substituted output, not the in-repo source.
    """
    line_sep = "" if sys.platform == "darwin" else "\n"
    return str(
        cockpit_config.STARSHIP_DEFAULT_TOML.read_text()
        .replace(cockpit_config.STARSHIP_PLACEHOLDER, cockpit_config.STARSHIP_CMD)
        .replace(
            cockpit_config.STARSHIP_THEME_PLACEHOLDER,
            cockpit_config.resolve_theme(),
        )
        .replace(cockpit_config.STARSHIP_LINE_SEP_PLACEHOLDER, line_sep)
    )
