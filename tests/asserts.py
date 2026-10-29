from __future__ import annotations

import json

from cockpit.lib import action_log, config


def read_actions() -> list[dict]:
    """Parsed lines of this test's isolated `actions.jsonl`."""
    path = config.COCKPIT_RUNTIME_DIR / action_log.LOG_NAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def expected_starship(cockpit_config) -> str:
    """starship.toml after install-time placeholder substitution.

    install_starship_default_config() rewrites two placeholders before writing
    to ~/.config/starship.toml: __COCKPIT_STARSHIP__ → the module-dispatch render
    command (interpreter + `-m cockpit.cli starship`), and __COCKPIT_THEME__ →
    the validated `theme` from config. Assertions about installed content must
    match that substituted output, not the in-repo source.
    """
    return str(
        cockpit_config.STARSHIP_DEFAULT_TOML.read_text()
        .replace(cockpit_config.STARSHIP_PLACEHOLDER, cockpit_config.STARSHIP_CMD)
        .replace(
            cockpit_config.STARSHIP_THEME_PLACEHOLDER,
            cockpit_config.resolve_theme(),
        )
    )
