## Summary

<!-- What changed and why. -->

## PR title

Must follow [Conventional Commits](https://www.conventionalcommits.org/)
(`type(scope): summary`) — we squash-merge, so this title becomes the commit
subject on `main`. Enforced by the `lint-pr-title` check.

## Checklist

- [ ] `pytest` passes
- [ ] `mypy cockpit/` is clean
- [ ] `pre-commit run ruff ruff-format --files <changed paths>` ran on changed files
- [ ] No internal ticket IDs, private URLs, real teammate names, or other
      non-public references ([privacy rules](../AGENTS.md#privacy--internal-references))
