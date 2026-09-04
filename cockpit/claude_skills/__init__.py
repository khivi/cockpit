"""Claude Code agent skills installed by ``cockpit setup``.

One subdirectory per skill, each holding a ``SKILL.md``, copied verbatim into
``~/.claude/skills/<name>/`` by :func:`cockpit.lib.config.install_claude_skills`.

Sibling of ``cockpit.claude_commands`` and the same rule applies — a skill
documents the ``cockpit`` CLI and never reimplements it. The split is *how it is
reached*: a command is typed (`/cockpit-new`), a skill is matched from its
``description`` when the model is doing something the skill covers. So a gesture
the user asks for by name is a command, and one the agent should reach for on its
own (reading its own diff, collecting review notes left on it) is a skill.
"""
