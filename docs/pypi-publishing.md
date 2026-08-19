# PyPI publishing (`cmux-cockpit`)

`brew install cockpit` is the primary install path and does **not** depend on
PyPI. This doc covers the secondary `pipx install cmux-cockpit` path, which
publishes via `.github/workflows/publish.yml` on every `v*` tag.

The distribution name is `cmux-cockpit` (the import package + `cockpit` console
script are unchanged — bare `cockpit` collides with Red Hat's Cockpit web
console).

## How it works — Trusted Publishing (OIDC), tokenless

`publish.yml` uses [PyPI Trusted Publishing]. There is **no** PyPI password or
API token anywhere — not in the repo, not in GitHub secrets, not in fnox.
GitHub mints a short-lived OIDC token at publish time (`id-token: write`) and
`pypa/gh-action-pypi-publish` swaps it for a one-time PyPI upload token.

The workflow asserts these four claims. They must match the publisher
registered on PyPI exactly:

| Claim       | Value         |
|-------------|---------------|
| Owner       | `khivi`       |
| Repository  | `cockpit`     |
| Workflow    | `publish.yml` |
| Environment | `pypi`        |

## One-time setup (done)

1. **GitHub Actions environment** — a repo environment literally named `pypi`
   (Settings → Environments). It can be **empty**; no secrets go in it for OIDC.
   GitHub auto-creates it on first run that references `environment: pypi`, but
   you can add it explicitly.

2. **PyPI pending publisher** — <https://pypi.org/manage/account/publishing/> →
   "Add a new pending publisher", filled with the four claims above and PyPI
   project name `cmux-cockpit`. PyPI creates the project on the first successful
   publish; no manual project creation needed first. **Registered** — the
   publisher is live and became a permanent publisher on the first successful
   publish (v1.8.0).

## Publishing a release

Covered by the tag flow in `AGENTS.md` → *Release versioning*: bump
`pyproject.toml` `version`, commit, tag `v<version>`, push the tag. That fires
both `publish.yml` (PyPI) and `release.yml` (Homebrew tap bump) independently.

## Recovery — how the initial block was cleared

`cmux-cockpit 1.8.0` is now live on PyPI. Earlier this was blocked: the owning
PyPI account was locked out on an unverified email, so the pending publisher
(step 2) couldn't be registered, and every `publish.yml` run failed with
`invalid-publisher` (valid OIDC token, no matching publisher). That failure
happens *before* upload, so no version was ever consumed and the runs re-ran
cleanly once unblocked.

The sequence that cleared it, kept here as the playbook if a re-block ever
happens:

1. Regain PyPI account access (email-verification / password-reset to the
   registered address) — or register a fresh account and adjust the `Owner`
   claim here + in `publish.yml`.
2. Register the pending publisher (setup step 2).
3. Re-run the failed publish: `gh run rerun <run-id>` (or push a fresh tag).

Nothing in this repo changed for the OIDC path — the fix was entirely on PyPI's
side.

[PyPI Trusted Publishing]: https://docs.pypi.org/trusted-publishers/
