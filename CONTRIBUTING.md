# Contributing

## Setup

```bash
pre-commit install   # one-time per clone
pre-commit run --all-files
```

Hooks enforced: `trailing-whitespace`, `end-of-file-fixer`, JSON/YAML validity, large-file guard, shebang sanity, `detect-private-key`, `shellcheck`, `shfmt -i 2 -ci`, `ruff --fix`, `black`, and [`gitleaks`](https://github.com/gitleaks/gitleaks) — secret detection plus custom rules in `.gitleaks.toml`.

## Privacy

This is a public repo. Before opening a PR, read [`AGENTS.md`](./AGENTS.md) — it lists what must never be committed (internal ticket IDs, teammate names, internal URLs, etc.) and what the gitleaks hook enforces automatically.
