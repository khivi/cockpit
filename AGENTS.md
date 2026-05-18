# Privacy & Internal References

This is a public repository. Never include the following in commits, PRs, code comments, or documentation:

- Internal ticket IDs (Linear `ENG-123`, Jira `PROJ-456`, etc.)
- Internal GitHub PR/issue URLs from private repos
- Real names of teammates (use roles instead: "the reviewer", "the on-call engineer")
- Internal Slack channels, wiki URLs, or tool links
- Internal hostnames, service names, or infra identifiers
- Customer names or company-specific identifiers

When writing commit messages or PR descriptions:

- Describe *what* changed and *why*, not which ticket tracks it
- Reference public GitHub issues only (`#123` in this repo)
- If context requires an internal ticket, summarize the requirement instead of linking

Before committing, scan for:

- Your team's real ticket prefixes (e.g. `\b(ENG|LIN|PROJ)-\d+\b`). The generic `[A-Z]{2,}-\d+` is too noisy — it matches `ISO-3166`, `HTTP-200`, `RFC-5246`, `UTF-8`, etc.
- `linear.app`, `atlassian.net`, internal company domains
- `@firstname` references that aren't GitHub handles

## Enforcement

The `gitleaks` pre-commit hook (`.gitleaks.toml`) blocks the regex-catchable cases at commit time: hardcoded home paths, Slack IDs, bare UUIDs, plus gitleaks' default credential ruleset. This document covers the judgment calls the regex can't reliably catch.

## Sync

This file is the canonical source. `CLAUDE.md` imports it via `@AGENTS.md`, `.github/copilot-instructions.md` is a symlink to it, and `CONTRIBUTING.md` references it. Edit `AGENTS.md`; the rest stay in sync automatically.
