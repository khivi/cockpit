# Security Policy

## Reporting a Vulnerability

Report security issues privately using GitHub's [private vulnerability
reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability):
open the repository's **Security** tab and click **Report a vulnerability**.
This opens a private GitHub Security Advisory visible only to maintainers.

Please do not open a public issue for security matters.

## Supported Versions

Only the latest release is supported. Cockpit ships as a Homebrew formula and
has no self-update path, so run `brew upgrade cockpit` (or
`pipx upgrade cmux-cockpit`) and re-check before reporting — the issue may
already be fixed.

| Version | Supported |
|---|---|
| Latest | Yes |

## Security Model

Cockpit auto-spawns Bash-capable Claude agents into git worktrees, so the risk
that matters is untrusted content reaching one of them. A PR's title,
description, and diff are attacker-controlled if the PR comes from outside your
team — and `review_prs: true` points an agent at exactly that.

Two per-repo gates guard that, both defaulting to `false`
([`docs/config.md`](docs/config.md#per-repo-fields-repos)):

- `review_prs` — auto-review is off entirely until you turn it on.
- `review_external` — with auto-review on, this decides whether it also
  reaches PRs from non-collaborators. Leave it off unless you accept
  exposing fork-PR content to an auto-spawned agent.

Auto-review is dry-run: it never auto-posts comments or submits an
approve/request-changes verdict. A human authorizes any of that.
