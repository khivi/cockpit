from __future__ import annotations


def expected_starship(cockpit_config) -> str:
    """starship.toml after install-time placeholder substitution.

    install_starship_default_config() rewrites two placeholders before writing
    to ~/.config/starship.toml: __COCKPIT_STARSHIP__ → the module-dispatch render
    command (interpreter + `-m cockpit.cli starship`), and __COCKPIT_THEME__ → the
    validated `theme` from config. Assertions about installed content must match
    that substituted output, not the in-repo source.
    """
    return str(
        cockpit_config.STARSHIP_DEFAULT_TOML.read_text()
        .replace(cockpit_config.STARSHIP_PLACEHOLDER, cockpit_config.STARSHIP_CMD)
        .replace(
            cockpit_config.STARSHIP_THEME_PLACEHOLDER,
            cockpit_config.resolve_theme(),
        )
    )
