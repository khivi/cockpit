from __future__ import annotations


def expected_starship(cockpit_config) -> str:
    """starship.toml after __COCKPIT_STARSHIP__ placeholder substitution.

    install_starship_default_config() rewrites the placeholder to the
    resolved absolute path of scripts/starship.py before writing to
    ~/.config/starship.toml — assertions about installed content must
    match that substituted output, not the in-repo source.
    """
    return cockpit_config.STARSHIP_DEFAULT_TOML.read_text().replace(
        cockpit_config.STARSHIP_PLACEHOLDER, str(cockpit_config.STARSHIP_PY)
    )
