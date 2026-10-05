# Release versioning

The version is **static** in `pyproject.toml`, read at runtime via `importlib.metadata`. There is no self-update path.

**Every merge to `main` that ships user-visible behaviour gets a release** — brew is the only delivery path. The semver bump is derived from the conventional-commit types `pr-title.yml` enforces.

**The release PR writes itself.** `release-please.yml` keeps one rolling `chore(main): release <version>` PR open and maintains `CHANGELOG.md`. **Merging that PR is the only human step** — it bumps `[project] version`, which is what `tag.yml` watches, so tag → tap → PyPI follow. Several merges batch into one release, which matters because PyPI refuses a re-upload.

Five settings there are load-bearing:

- **`skip-github-release: true`** — left to default, release-please pushes the tag itself under a token whose pushes don't trigger workflows, so `release.yml`/`publish.yml` would silently never run.
- **`skip-labeling: true`** — the **required** companion. release-please keeps release state in a **label on the merged release PR**, flipped in exactly the step `skip-github-release` turns off, so every later run finds a still-`pending` merged PR and aborts before proposing the next version. This wedged the pipeline after v1.8.0 and cannot self-heal. **The state is the label, not a GitHub Release** — cutting the missing Release does nothing. If it wedges again, check `gh pr view <release-pr> --json labels` first and clear a stale `autorelease: pending` by hand.
- **`if: "!startsWith(github.event.head_commit.message, 'chore(main): release')"`** — the job must skip the push that *merged* the release PR, since it re-runs on the commit it causes and races `tag.yml`; with no baseline tag yet it treats the repo as never released and regenerates the whole changelog. Nothing is ever releasable on that push, so the guard loses no coverage. It keys off the **commit subject**, so **do not** set `pull-request-title-pattern` without updating the guard.
- **`token: COCKPIT_GITHUB_API_TOKEN`** — a PR opened by the default `GITHUB_TOKEN` doesn't trigger workflows, so the release PR could never satisfy `main`'s required checks.
- **`concurrency: {group: release-please, cancel-in-progress: false}`** — two runs race the one release branch, and the loser dies **after** writing its commit but **before** updating the PR title, leaving the branch at one version while the PR advertises another. Since we squash-merge, an unnoticed merge commits the wrong release subject over the right tree. **`cancel-in-progress` stays `false`** — cancelling kills a run mid-ref-update. If a release PR's title disagrees with its changelog, **fix the title before merging**.

State lives in `release-please-config.json` and `.release-please-manifest.json` (the one file to correct by hand if a release is cut out of band). `CHANGELOG.md` is **release-please's file** — don't hand-edit it; it's excluded from `markdownlint` because MD004 would fail every release PR.

**`include-component-in-tag: false` is required, not cosmetic** — at its default release-please names tags `cockpit-v<version>` while `tag.yml` pushes `v<version>`, so it can't find the previous release and regenerates the changelog from the entire history.

`./cut-release.sh <version>` is the **manual fallback**: bump, commit `chore(release): <version>`, open and `--squash --admin` merge. It refuses a dirty tree, a non-semver argument, `main`/`master`, and a no-op bump. Using it means `.release-please-manifest.json` must be updated to match.

`tag.yml` watches `pyproject.toml` on `main`, pushes `v<version>` at the merge commit, then cuts the Release (both halves idempotent). It pushes with **`COCKPIT_GITHUB_API_TOKEN`, not `GITHUB_TOKEN`** — a ref pushed by the latter doesn't trigger the two downstream workflows. The Release is **presentation only**; it is *not* what keeps release-please unwedged.

The tag must point at a tree whose version equals the tag minus the `v` — both downstream workflows re-read `pyproject.toml` and hard-fail on a mismatch. The `publish.yml` guard matters most, since PyPI refuses a re-upload. `release.yml` then hands the tarball URL to `mislav/bump-homebrew-formula-action`, which **commits the new `url`+`sha256` straight onto the tap's `main`**.

**`create-branch: false` in `release.yml` is what makes that direct commit happen, and removing it re-breaks releases silently** — the tap's `main` is ruleset-protected, so left to default the action branches and then `POST /pulls` 403s *after* pushing a correct-looking branch, which is the v1.5.1/v1.6.0 failure mode where both formulas were right and neither reached the tap. When a release job fails, **check the tap for an orphaned `update-cockpit.rb-*` branch before re-cutting anything**. The formula's `resource` blocks are **not** touched — regenerate them by hand on a dependency bump.

## PyPI is the second tag consumer — trusted publishing

The same tag fires `publish.yml`, uploading as **`cmux-cockpit`** (bare `cockpit` collides with Red Hat's Cockpit; the import package and console script are unchanged). It runs *independently* of `release.yml`, so a green tap PR is not evidence PyPI succeeded — check both.

Auth is Trusted Publishing (OIDC): there is **no** PyPI token in this repo, in GitHub secrets, or in fnox. PyPI matches four claims — Owner `khivi`, Repository `cockpit`, Workflow `publish.yml`, Environment `pypi`. Earlier `invalid-publisher` failures were fixed browser-side, not by editing `publish.yml`; that fails *before* upload, so no version is consumed and `gh run rerun` recovers it. **Do not** touch `publish.yml` — check the pending-publisher claims first. See `docs/pypi-publishing.md`.
