# Security Policy

## Reporting a Vulnerability

Report security issues privately using GitHub's [private vulnerability
reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability):
open the repository's **Security** tab and click **Report a vulnerability**.
This opens a private GitHub Security Advisory visible only to maintainers.

Please do not open a public issue for security matters.

## Supported Versions

Cockpit self-updates (`cockpit update`, or `u` in the TUI). Only the latest
released version is supported.

| Version | Supported |
|---|---|
| Latest | Yes |

If you're on an older version, update before reporting — the issue may
already be fixed.

## Security Model

Cockpit auto-spawns Bash-capable Claude agents into git worktrees. The main
risk is untrusted content reaching one of those agents: with `review_prs:
true`, cockpit auto-spawns a review worktree + agent on open PRs from
coworkers, and a PR's title, description, and diff are attacker-controlled
content if the PR comes from outside your team.

`review_external` (per-repo) defaults to `false` and gates whether
auto-review reaches PRs from non-collaborators. Leave it off unless you
trust exposing external PR content to an auto-spawned agent — see the
README's ["Auto-review security
posture"](README.md#auto-review-security-posture) section for the full
gating rules.

Auto-review is dry-run: it never auto-posts comments or submits an
approve/request-changes verdict. A human authorizes any of that.
