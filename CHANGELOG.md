# Changelog

## [2.23.1](https://github.com/khivi/cockpit/compare/v2.23.0...v2.23.1) (2026-09-03)


### Bug Fixes

* **nudge:** self-heal idle pill when cmux reports no claude_code state ([#460](https://github.com/khivi/cockpit/issues/460)) ([658fa0f](https://github.com/khivi/cockpit/commit/658fa0f15265b367b86a3a3e49d584992eb90e67))

## [2.23.0](https://github.com/khivi/cockpit/compare/v2.22.1...v2.23.0) (2026-09-02)


### Features

* **tickets:** render a Trello card by its card number ([#458](https://github.com/khivi/cockpit/issues/458)) ([ef9829f](https://github.com/khivi/cockpit/commit/ef9829f9840c86b228f5f4cb333067b94caa74a8))

## [2.22.0](https://github.com/khivi/cockpit/compare/v2.21.0...v2.22.0) (2026-09-01)


### Features

* show the review-thread ratio even when every thread is handled ([#450](https://github.com/khivi/cockpit/issues/450)) ([59bc7f8](https://github.com/khivi/cockpit/commit/59bc7f8f442621995401ce5b21a3f1f062cfddc2))

## [2.21.0](https://github.com/khivi/cockpit/compare/v2.20.0...v2.21.0) (2026-09-01)


### Features

* **commands:** ship /cockpit-nudge and hide the config-invoked shims from usage ([#448](https://github.com/khivi/cockpit/issues/448)) ([45a3020](https://github.com/khivi/cockpit/commit/45a3020b5bb19e18d81039d0073be17a834beef0))

## [2.20.0](https://github.com/khivi/cockpit/compare/v2.19.0...v2.20.0) (2026-09-01)


### Features

* key the flat render cells by worktree, and expand {repo} in sidebar_tag ([#446](https://github.com/khivi/cockpit/issues/446)) ([816d22a](https://github.com/khivi/cockpit/commit/816d22aa497498eb2300a0a32821bbdd124b2d95))

## [2.19.0](https://github.com/khivi/cockpit/compare/v2.18.0...v2.19.0) (2026-09-01)


### Features

* name the owning repo in the TUI header and the cmux sidebar ([#444](https://github.com/khivi/cockpit/issues/444)) ([73203d9](https://github.com/khivi/cockpit/commit/73203d989e9e6a4eac29dd165ba396a8ff519fbd))

## [2.18.0](https://github.com/khivi/cockpit/compare/v2.17.1...v2.18.0) (2026-09-01)


### Features

* **broadcast:** scope the fan-out to one repo with --repo ([#442](https://github.com/khivi/cockpit/issues/442)) ([90ad7a4](https://github.com/khivi/cockpit/commit/90ad7a4bfed6549c9d060a764cb1f364af377c6e))

## [2.17.1](https://github.com/khivi/cockpit/compare/v2.17.0...v2.17.1) (2026-08-31)


### Documentation

* prune AGENTS.md to rule + symbol + directives ([#440](https://github.com/khivi/cockpit/issues/440)) ([3268d0d](https://github.com/khivi/cockpit/commit/3268d0df4ebaaf8d5bf820ce8769fd37b1684aa8))

## [2.17.0](https://github.com/khivi/cockpit/compare/v2.16.1...v2.17.0) (2026-08-31)


### Features

* **tui:** open PRs, CI, tickets and authors from the table with a click ([#438](https://github.com/khivi/cockpit/issues/438)) ([bd8e00b](https://github.com/khivi/cockpit/commit/bd8e00b37248ebcf0cea441a9526a3b85f54b0e1))

## [2.16.1](https://github.com/khivi/cockpit/compare/v2.16.0...v2.16.1) (2026-08-31)


### Bug Fixes

* **tui:** drop the first-run welcome toast ([#436](https://github.com/khivi/cockpit/issues/436)) ([96304da](https://github.com/khivi/cockpit/commit/96304da3a1e45aa46bdc0bcef799c17afd3909df))

## [2.16.0](https://github.com/khivi/cockpit/compare/v2.15.0...v2.16.0) (2026-08-31)


### Features

* **tui:** open the ask box on a lead-in when diff comments are pending ([#434](https://github.com/khivi/cockpit/issues/434)) ([8e04d3c](https://github.com/khivi/cockpit/commit/8e04d3c2c2981d155bb990c576ab17ce960735c4))

## [2.15.0](https://github.com/khivi/cockpit/compare/v2.14.1...v2.15.0) (2026-08-31)


### Features

* **cycle:** update stale approved/snoozed PR branches server-side ([#432](https://github.com/khivi/cockpit/issues/432)) ([07d4f7b](https://github.com/khivi/cockpit/commit/07d4f7b9bae766668b9d646c5f43c09b0a6fd742))

## [2.14.1](https://github.com/khivi/cockpit/compare/v2.14.0...v2.14.1) (2026-08-28)


### Bug Fixes

* **cmux:** write the idle pill again, and re-assert it on the fast tick ([#430](https://github.com/khivi/cockpit/issues/430)) ([62032d8](https://github.com/khivi/cockpit/commit/62032d8d5d5b42349bb86d52b8355d68cb4cdcf1))

## [2.14.0](https://github.com/khivi/cockpit/compare/v2.13.2...v2.14.0) (2026-08-28)


### Features

* **tui:** add `A` to ask a repo's snoozed fold ([#428](https://github.com/khivi/cockpit/issues/428)) ([084b8bf](https://github.com/khivi/cockpit/commit/084b8bf44cb312b7447a887ade538025cd68661c))

## [2.13.2](https://github.com/khivi/cockpit/compare/v2.13.1...v2.13.2) (2026-08-28)


### Bug Fixes

* **sidebar:** restore a lost trailing fold on the fast tick ([#426](https://github.com/khivi/cockpit/issues/426)) ([3bd33aa](https://github.com/khivi/cockpit/commit/3bd33aa459b55ae1cc156f02d302c621ffd3abac))

## [2.13.1](https://github.com/khivi/cockpit/compare/v2.13.0...v2.13.1) (2026-08-28)


### Performance Improvements

* **daemon:** fan out the fast tick's per-worktree cell writes ([#424](https://github.com/khivi/cockpit/issues/424)) ([5259ffa](https://github.com/khivi/cockpit/commit/5259ffa8d8517d60add7e27673eec590731b0a7a))

## [2.13.0](https://github.com/khivi/cockpit/compare/v2.12.5...v2.13.0) (2026-08-28)


### Features

* **tui:** deliver the diff viewer's comments into the row's session ([#422](https://github.com/khivi/cockpit/issues/422)) ([56f2c37](https://github.com/khivi/cockpit/commit/56f2c37adba5cd7f528df565660dbaf84a187394))

## [2.12.5](https://github.com/khivi/cockpit/compare/v2.12.4...v2.12.5) (2026-08-27)


### Bug Fixes

* **sidebar:** fold a snoozed stack into the snoozed pile ([#420](https://github.com/khivi/cockpit/issues/420)) ([231268d](https://github.com/khivi/cockpit/commit/231268de608bf54d6653cd687c411177959a1b07))

## [2.12.4](https://github.com/khivi/cockpit/compare/v2.12.3...v2.12.4) (2026-08-27)


### Bug Fixes

* **sidebar:** sink a snoozed stack below the trailing folds ([#418](https://github.com/khivi/cockpit/issues/418)) ([11fa1dd](https://github.com/khivi/cockpit/commit/11fa1dda71ffd54a4a53b8ab02ddcc7225714866))

## [2.12.3](https://github.com/khivi/cockpit/compare/v2.12.2...v2.12.3) (2026-08-27)


### Documentation

* **features:** name the ticket cache TTL where tickets are described ([#416](https://github.com/khivi/cockpit/issues/416)) ([e586e1a](https://github.com/khivi/cockpit/commit/e586e1ad9e09cddc823e40e14380829bc6e16d97))

## [2.12.2](https://github.com/khivi/cockpit/compare/v2.12.1...v2.12.2) (2026-08-27)


### Documentation

* **features:** name the ticket cache as the exception to nothing-is-stored ([#414](https://github.com/khivi/cockpit/issues/414)) ([60b33f3](https://github.com/khivi/cockpit/commit/60b33f3b0e88f22a5dd7b026dafc867274356416))

## [2.12.1](https://github.com/khivi/cockpit/compare/v2.12.0...v2.12.1) (2026-08-27)


### Bug Fixes

* **tui:** name the gate's reason when ask refuses a row ([#412](https://github.com/khivi/cockpit/issues/412)) ([91d8fa1](https://github.com/khivi/cockpit/commit/91d8fa100b75e3c9cf63b47997d9536505a17ad4))

## [2.12.0](https://github.com/khivi/cockpit/compare/v2.11.2...v2.12.0) (2026-08-27)


### Features

* **tui:** bind sync to s, drop it from the menu ([#410](https://github.com/khivi/cockpit/issues/410)) ([8ee6509](https://github.com/khivi/cockpit/commit/8ee65095427120a2c11a4ab1dab0f6ac92e4750a))

## [2.11.2](https://github.com/khivi/cockpit/compare/v2.11.1...v2.11.2) (2026-08-27)


### Bug Fixes

* **gh:** collapse PRs sharing a head branch to one ([#408](https://github.com/khivi/cockpit/issues/408)) ([fd51647](https://github.com/khivi/cockpit/commit/fd516475f768d0d0b117e280c6b0eee33b9ead80))

## [2.11.1](https://github.com/khivi/cockpit/compare/v2.11.0...v2.11.1) (2026-08-26)


### Bug Fixes

* **tui:** strip inherited CMUX_SURFACE_ID from the diff subprocess ([#405](https://github.com/khivi/cockpit/issues/405)) ([6307263](https://github.com/khivi/cockpit/commit/6307263a161b0d2b263983c628a4fde5e64cd41e))


### Documentation

* **features:** add a What it costs section ([#407](https://github.com/khivi/cockpit/issues/407)) ([e798fed](https://github.com/khivi/cockpit/commit/e798feda87dd813542cc8045cc8255aac000ae14))

## [2.11.0](https://github.com/khivi/cockpit/compare/v2.10.0...v2.11.0) (2026-08-26)


### Features

* **cmux:** fold CI into the sidebar PR pill ([#403](https://github.com/khivi/cockpit/issues/403)) ([f987b9f](https://github.com/khivi/cockpit/commit/f987b9f50f1089a1d150d3eccf2bdf2209b64c50))

## [2.10.0](https://github.com/khivi/cockpit/compare/v2.9.0...v2.10.0) (2026-08-26)


### Features

* **tui:** header menu replaces the footer's palette hint, keys explain on hover ([#399](https://github.com/khivi/cockpit/issues/399)) ([f3b69d9](https://github.com/khivi/cockpit/commit/f3b69d9345b337363c24d5627e83161190baaff8))


### Bug Fixes

* **sidebar:** give fold anchors a live shell so cmux stops reaping them ([#401](https://github.com/khivi/cockpit/issues/401)) ([d429269](https://github.com/khivi/cockpit/commit/d42926983c36a53595fbaa7c2df16b86834d36a4))

## [2.9.0](https://github.com/khivi/cockpit/compare/v2.8.0...v2.9.0) (2026-08-26)


### Features

* **tui:** open the PR diff in the row's own workspace ([#398](https://github.com/khivi/cockpit/issues/398)) ([67f308b](https://github.com/khivi/cockpit/commit/67f308b062c61315de5dc326c4824b8ac5e79058))

## [2.8.0](https://github.com/khivi/cockpit/compare/v2.7.0...v2.8.0) (2026-08-26)


### Features

* **tui:** move sync and output to the command palette ([#396](https://github.com/khivi/cockpit/issues/396)) ([0609d27](https://github.com/khivi/cockpit/commit/0609d27852b424d71a43f8b417d6e046411269c1))

## [2.7.0](https://github.com/khivi/cockpit/compare/v2.6.4...v2.7.0) (2026-08-26)


### Features

* **sidebar:** render the PR as a cockpit pill, not cmux's native row ([#394](https://github.com/khivi/cockpit/issues/394)) ([2543379](https://github.com/khivi/cockpit/commit/254337982ee34049fa4ac03261512a0c4e1f7ac1))

## [2.6.4](https://github.com/khivi/cockpit/compare/v2.6.3...v2.6.4) (2026-08-26)


### Bug Fixes

* **tui:** show palette entries on an empty ^P and name the parked-repos count ([#389](https://github.com/khivi/cockpit/issues/389)) ([92d8169](https://github.com/khivi/cockpit/commit/92d81690f229c1afdde9182a4955602eb021c26d))

## [2.6.3](https://github.com/khivi/cockpit/compare/v2.6.2...v2.6.3) (2026-08-26)


### Features

* **dev:** sandboxed dev runs, a --dry daemon mode, and machine-local runtime state ([#391](https://github.com/khivi/cockpit/issues/391)) ([15e2587](https://github.com/khivi/cockpit/commit/15e25876340fb7ebfe65c06cb306b97d1790e9bf))

## [2.6.2](https://github.com/khivi/cockpit/compare/v2.6.1...v2.6.2) (2026-08-25)


### Bug Fixes

* **preflight:** warn on an unset credential for every ticket provider ([#388](https://github.com/khivi/cockpit/issues/388)) ([103e63c](https://github.com/khivi/cockpit/commit/103e63c5192f76e7afc60d867ac8430b087bcd4d))

## [2.6.1](https://github.com/khivi/cockpit/compare/v2.6.0...v2.6.1) (2026-08-25)


### Documentation

* fix stale comment references and pin them with a test ([#387](https://github.com/khivi/cockpit/issues/387)) ([a240abb](https://github.com/khivi/cockpit/commit/a240abbd9052409d235a82239efd2639364e5407))
* record the nudge-gate skip reasons in the state-machine diagram ([#385](https://github.com/khivi/cockpit/issues/385)) ([14c5633](https://github.com/khivi/cockpit/commit/14c5633efb5978aca35cc08d90207c6e6948bd69))

## [2.6.0](https://github.com/khivi/cockpit/compare/v2.5.0...v2.6.0) (2026-08-25)


### Features

* **tui:** add FEATURES.md and make it reachable from the dashboard ([#382](https://github.com/khivi/cockpit/issues/382)) ([3364b11](https://github.com/khivi/cockpit/commit/3364b11191c4780be766296ccd245bf2617d8d00))

## [2.5.0](https://github.com/khivi/cockpit/compare/v2.4.0...v2.5.0) (2026-08-25)


### Features

* **tui:** ask a session or a whole repo with `a`, read PR diffs with `d` ([#378](https://github.com/khivi/cockpit/issues/378)) ([8988860](https://github.com/khivi/cockpit/commit/8988860bd170a7a3257a33f585eb8c019f56b914))


### Bug Fixes

* collapse send text to one line (`cmux.one_line`). `cmux send` ([8988860](https://github.com/khivi/cockpit/commit/8988860bd170a7a3257a33f585eb8c019f56b914))

## [2.4.0](https://github.com/khivi/cockpit/compare/v2.3.5...v2.4.0) (2026-08-25)


### Features

* **tui:** collapse each repo's snoozed rows behind a z-toggled fold ([#380](https://github.com/khivi/cockpit/issues/380)) ([26440c5](https://github.com/khivi/cockpit/commit/26440c593e6a30d653e1c244e107913139ddf414))

## [2.3.5](https://github.com/khivi/cockpit/compare/v2.3.4...v2.3.5) (2026-08-25)


### Bug Fixes

* **capabilities:** require only capability ids that gate a real feature ([#377](https://github.com/khivi/cockpit/issues/377)) ([fda1428](https://github.com/khivi/cockpit/commit/fda142842ec07869666695def3a7a3751f896934))

## [2.3.4](https://github.com/khivi/cockpit/compare/v2.3.3...v2.3.4) (2026-08-25)


### Documentation

* correct the count of installed slash commands ([#375](https://github.com/khivi/cockpit/issues/375)) ([5f80c9e](https://github.com/khivi/cockpit/commit/5f80c9e4f51a7a0b6eacc7c6ac70d08fe1f9aee9))

## [2.3.3](https://github.com/khivi/cockpit/compare/v2.3.2...v2.3.3) (2026-08-25)


### Documentation

* reframe cockpit as the human's join, not an agent orchestrator ([#373](https://github.com/khivi/cockpit/issues/373)) ([8676018](https://github.com/khivi/cockpit/commit/8676018e55f171ace05f9272c400d21e4b6e3563))

## [2.3.2](https://github.com/khivi/cockpit/compare/v2.3.1...v2.3.2) (2026-08-25)


### Bug Fixes

* **tui:** repaint mute/snooze on the keypress instead of at cycle end ([#371](https://github.com/khivi/cockpit/issues/371)) ([690aa29](https://github.com/khivi/cockpit/commit/690aa29b2a3601c535868a855d0c5b6ac34ebc38))

## [2.3.1](https://github.com/khivi/cockpit/compare/v2.3.0...v2.3.1) (2026-08-25)


### Bug Fixes

* **tui:** kick full-cycle on snooze so the sidebar fold lands on the keypress ([#369](https://github.com/khivi/cockpit/issues/369)) ([1918285](https://github.com/khivi/cockpit/commit/19182857c331cac448f61a51eac154857d67d865))

## [2.3.0](https://github.com/khivi/cockpit/compare/v2.2.2...v2.3.0) (2026-08-25)


### Features

* **broadcast:** report why each workspace was skipped ([#366](https://github.com/khivi/cockpit/issues/366)) ([99d2caa](https://github.com/khivi/cockpit/commit/99d2caaaf1266f6eb4cc748e3e7d9538fa85fff5))

## [2.2.2](https://github.com/khivi/cockpit/compare/v2.2.1...v2.2.2) (2026-08-25)


### Bug Fixes

* **spawn:** don't adopt a worktree cockpit new is still setting up ([#364](https://github.com/khivi/cockpit/issues/364)) ([0a68edf](https://github.com/khivi/cockpit/commit/0a68edfc58205a6f1c6d96968a72b5b6888cbc59))

## [2.2.1](https://github.com/khivi/cockpit/compare/v2.2.0...v2.2.1) (2026-08-24)


### Bug Fixes

* **close:** stop counting the base branch's commits as local work ([#362](https://github.com/khivi/cockpit/issues/362)) ([7b85b63](https://github.com/khivi/cockpit/commit/7b85b639d31ca2a6c4b0195a6066ebe6efa3ffde))

## [2.2.0](https://github.com/khivi/cockpit/compare/v2.1.0...v2.2.0) (2026-08-24)


### Features

* **tui:** close the worktree when the cmux sidebar X is clicked ([#360](https://github.com/khivi/cockpit/issues/360)) ([13f06f6](https://github.com/khivi/cockpit/commit/13f06f6c3b316a1fa6401b5b40716f642c6356d1))

## [2.1.0](https://github.com/khivi/cockpit/compare/v2.0.0...v2.1.0) (2026-08-24)


### Features

* **tui:** sink parked repos in the new-workspace picker, un-park on spawn ([#358](https://github.com/khivi/cockpit/issues/358)) ([18cddfb](https://github.com/khivi/cockpit/commit/18cddfbd5893a86644b80721854c6389eea24ae3))

## [2.0.0](https://github.com/khivi/cockpit/compare/v1.21.0...v2.0.0) (2026-08-24)


### ⚠ BREAKING CHANGES

* **spawn:** gate the ticket prompt on the repo provider, drop superseded ticket keys ([#356](https://github.com/khivi/cockpit/issues/356))

### Bug Fixes

* **spawn:** gate the ticket prompt on the repo provider, drop superseded ticket keys ([#356](https://github.com/khivi/cockpit/issues/356)) ([67611fe](https://github.com/khivi/cockpit/commit/67611fe52884c2cc962ed9d43f968f9532453c14))

## [1.21.0](https://github.com/khivi/cockpit/compare/v1.20.2...v1.21.0) (2026-08-24)


### Features

* **config:** unify ticket field names across providers, and correct the docs ([#353](https://github.com/khivi/cockpit/issues/353)) ([e93bdb1](https://github.com/khivi/cockpit/commit/e93bdb1c61b73b211eaa474e396115600cbd7e45))


### Bug Fixes

* **statusline:** raise starship command_timeout so pills stop vanishing ([#355](https://github.com/khivi/cockpit/issues/355)) ([7315c3c](https://github.com/khivi/cockpit/commit/7315c3c66fee331d61ca0eb6d37f4cb4607b3f28))

## [1.20.2](https://github.com/khivi/cockpit/compare/v1.20.1...v1.20.2) (2026-08-21)


### Bug Fixes

* drop the Linear MCP pre-flight and heal a wiped pidfile dir ([#351](https://github.com/khivi/cockpit/issues/351)) ([201acdb](https://github.com/khivi/cockpit/commit/201acdb71af842586ede09768f0e7aa3b9f53b12))

## [1.20.1](https://github.com/khivi/cockpit/compare/v1.20.0...v1.20.1) (2026-08-21)


### Bug Fixes

* **cycle:** keep review folds when a cycle can't reach GitHub ([#349](https://github.com/khivi/cockpit/issues/349)) ([7eb6656](https://github.com/khivi/cockpit/commit/7eb665657c895b3b1e0c744da8b1c9874028552e))

## [1.20.0](https://github.com/khivi/cockpit/compare/v1.19.0...v1.20.0) (2026-08-21)


### Features

* **sidebar:** create the reviews and snoozed folds collapsed ([#347](https://github.com/khivi/cockpit/issues/347)) ([06d048d](https://github.com/khivi/cockpit/commit/06d048d6bf9d53c68975f9b2095405bd0b4ffa09))

## [1.19.0](https://github.com/khivi/cockpit/compare/v1.18.0...v1.19.0) (2026-08-21)


### Features

* **tui:** explain the tick countdowns on hover ([#345](https://github.com/khivi/cockpit/issues/345)) ([3f8158e](https://github.com/khivi/cockpit/commit/3f8158e4d0bed2dc34983962347ef3cd414d06cb))

## [1.18.0](https://github.com/khivi/cockpit/compare/v1.17.1...v1.18.0) (2026-08-21)


### Features

* **tui:** show per-worktree Claude Code spend ([#344](https://github.com/khivi/cockpit/issues/344)) ([07ca7b8](https://github.com/khivi/cockpit/commit/07ca7b8d01905a271b0c0ce7bf55cfc7fededd3d))


### Bug Fixes

* **spawn:** classify Linear and Jira issue URLs as ticket sources ([#341](https://github.com/khivi/cockpit/issues/341)) ([1de2a1d](https://github.com/khivi/cockpit/commit/1de2a1dc5c66b6174391052a7dfa0f726350157c))


### Documentation

* **todo:** queue the two cockpit-app port candidates ([#342](https://github.com/khivi/cockpit/issues/342)) ([15292bb](https://github.com/khivi/cockpit/commit/15292bb596423c815a062027d5035c50d3a363c3))

## [1.17.1](https://github.com/khivi/cockpit/compare/v1.17.0...v1.17.1) (2026-08-21)


### Bug Fixes

* **hooks:** install only the two hooks with a reader ([#339](https://github.com/khivi/cockpit/issues/339)) ([77e4ddd](https://github.com/khivi/cockpit/commit/77e4ddd92fadee1b5eb09bab4f516b48fc5acf92))

## [1.17.0](https://github.com/khivi/cockpit/compare/v1.16.0...v1.17.0) (2026-08-21)


### Features

* **sidebar:** sink a snoozed stacked-PR chain to the bottom ([#337](https://github.com/khivi/cockpit/issues/337)) ([660f64c](https://github.com/khivi/cockpit/commit/660f64c0f32e37f2bf57ca670e85f8dd53c33218))


### Bug Fixes

* **tui:** show the h/Hide hint only on repo rows ([#334](https://github.com/khivi/cockpit/issues/334)) ([f7a49d9](https://github.com/khivi/cockpit/commit/f7a49d9f8a734b1731e9352a5aded75b218882a9))

## [1.16.0](https://github.com/khivi/cockpit/compare/v1.15.0...v1.16.0) (2026-08-20)


### Features

* **spawn:** fold --context-text into an optional-value --context ([#332](https://github.com/khivi/cockpit/issues/332)) ([7580ed4](https://github.com/khivi/cockpit/commit/7580ed4225009f3d6e161e7bf92cb41301ed2ced))

## [1.15.0](https://github.com/khivi/cockpit/compare/v1.14.0...v1.15.0) (2026-08-20)


### Features

* **setup:** ship /cockpit-broadcast as a bundled slash command ([#328](https://github.com/khivi/cockpit/issues/328)) ([a2ae576](https://github.com/khivi/cockpit/commit/a2ae57648bb6f2022bb8b23e9a21fa8723fb06d9))

## [1.14.0](https://github.com/khivi/cockpit/compare/v1.13.0...v1.14.0) (2026-08-20)


### Features

* **tui:** wake the fast tick on cmux workspace events ([#325](https://github.com/khivi/cockpit/issues/325)) ([32205e7](https://github.com/khivi/cockpit/commit/32205e7b56eceba9c9cae0541ed2e2279623b8f0))

## [1.13.0](https://github.com/khivi/cockpit/compare/v1.12.0...v1.13.0) (2026-08-20)


### Features

* **cli:** add cockpit broadcast to send text to every idle session ([#321](https://github.com/khivi/cockpit/issues/321)) ([805fcc8](https://github.com/khivi/cockpit/commit/805fcc858d62bbb837be02ee78064aba4017eb40))
* **preflight:** gate startup on the cmux verbs and capabilities cockpit needs ([#324](https://github.com/khivi/cockpit/issues/324)) ([4cfeb67](https://github.com/khivi/cockpit/commit/4cfeb6767e7a0fb6101ab939f411587224029435))


### Bug Fixes

* **nudge:** key prefs per repo and resolve the snooze payload by nwo ([#323](https://github.com/khivi/cockpit/issues/323)) ([e4fa850](https://github.com/khivi/cockpit/commit/e4fa85047026de640dd4f88aa90fce0c7f8758d7))

## [1.12.0](https://github.com/khivi/cockpit/compare/v1.11.0...v1.12.0) (2026-08-20)


### Features

* **tui:** align row status glyphs and let a snooze supersede a mute ([#319](https://github.com/khivi/cockpit/issues/319)) ([883abc5](https://github.com/khivi/cockpit/commit/883abc5f1b8d4dff76e8dea23c632e967d5b508c))

## [1.11.0](https://github.com/khivi/cockpit/compare/v1.10.1...v1.11.0) (2026-08-20)


### Features

* **tui:** sink reviews and snoozed rows below the active queue ([#317](https://github.com/khivi/cockpit/issues/317)) ([bb7e64c](https://github.com/khivi/cockpit/commit/bb7e64c84698f97ea8cc250820981df5e0415b6c))

## [1.10.1](https://github.com/khivi/cockpit/compare/v1.10.0...v1.10.1) (2026-08-20)


### Bug Fixes

* **tui:** brighten the snoozed-row icon tint ([#315](https://github.com/khivi/cockpit/issues/315)) ([6e7d77a](https://github.com/khivi/cockpit/commit/6e7d77a3966116d8faa5dd1b1a810c71e255937a))

## [1.10.0](https://github.com/khivi/cockpit/compare/v1.9.0...v1.10.0) (2026-08-20)


### Features

* **sidebar:** key the coworker-review fold by org and fold a lone review ([#310](https://github.com/khivi/cockpit/issues/310)) ([a72abdd](https://github.com/khivi/cockpit/commit/a72abddf8d924001dc70670d32d984f05eef0f60))
* **tui:** snooze a PR until someone comments or approves ([#312](https://github.com/khivi/cockpit/issues/312)) ([65fc93a](https://github.com/khivi/cockpit/commit/65fc93a05987178f9a4140aaf62dc984da002c49))

## [1.9.0](https://github.com/khivi/cockpit/compare/v1.8.0...v1.9.0) (2026-08-20)


### Features

* **tickets:** route a ticket to its repo by project/keys/board ([#304](https://github.com/khivi/cockpit/issues/304)) ([6ee4e98](https://github.com/khivi/cockpit/commit/6ee4e985c5eb9faf56a51919d85e27713f9c1e21))
* **tui:** make the repo grouping read as a hierarchy ([#307](https://github.com/khivi/cockpit/issues/307)) ([c9ff131](https://github.com/khivi/cockpit/commit/c9ff131267fc13d3010f51377b44b2ed3662fcf2))


### Documentation

* PyPI trusted-publishing setup + recovery steps ([#271](https://github.com/khivi/cockpit/issues/271)) ([92244e1](https://github.com/khivi/cockpit/commit/92244e1aa4c937c2e4d8a81d783fbe2e77eb8f75))

## [1.8.0](https://github.com/khivi/cockpit/compare/v1.7.1...v1.8.0) (2026-08-13)


### Features

* **tickets:** resolve provider credentials per org, keep them out of spawned sessions ([#299](https://github.com/khivi/cockpit/issues/299)) ([a5c9ea0](https://github.com/khivi/cockpit/commit/a5c9ea0b28b2533ec28a0a825bff7b6f3a9387e5))


### Bug Fixes

* **release:** match release-please's tag format to tag.yml's ([#302](https://github.com/khivi/cockpit/issues/302)) ([0d1649c](https://github.com/khivi/cockpit/commit/0d1649ccf46efc0e7183054818d6e4f1ccf3ecc0))

## Changelog

All notable changes to this project are documented here, in the style of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The version in `pyproject.toml` is bumped and tagged `v<version>` at release
time (the brew formula pins that tag), so a per-version list would mostly be
noise. This file instead records notable, human-readable changes grouped by
kind, not every version bump.

## Recent history

### Added

- Ticket providers for Trello, Jira, and GitHub Issues, alongside Linear, via
  a unified `tickets` config object (#231, #223, replacing per-provider
  flags)
- `review_prs` gating: skip coworker PRs from Dependabot and non-collaborator
  (external/fork) authors by default, opt-in via `dependabot` /
  `review_external` (#232, #242)
- `cockpit close` CLI and `/cockpit:close` command as manual teardown entry
  points alongside the TUI's `c`/`C` keys (#207)
- Configurable `review_command` for auto-spawned review workspaces (#206)
- Startup warning when a repo's configured base branch doesn't resolve
  against `origin` (#244)
- Red `!` indicator in the status column for an unresolved ticket state
  (#243)
- Worktree table rows grouped under per-repo header rows (#233)

### Changed

- Distribution moved from a Claude Code plugin + uv-tool to a Homebrew formula
  (`brew tap khivi/cockpit && brew install cockpit`); `cockpit setup` now writes
  the statusLine **and** the Claude Code hooks into `~/.claude/settings.json`.
  The in-TUI self-update (`u`), the `/cockpit:*` slash commands, and the
  plugin/marketplace are gone — `brew upgrade` handles updates. Existing
  plugin users: see [`MIGRATION.md`](MIGRATION.md).
- `w` (open workspace) folded into `f` (focus), which now spawns a workspace
  first if the row has none; `in_place` config renamed to `use_worktree`
  (inverted polarity); `n` (new workspace) routes per repo type (#245)
- Sidebar workspace names drop the `[repo]` prefix, relying on `sidebar_color`
  tint to convey which repo a workspace belongs to (#235)
- Footer ahead-count is based on the PR's base branch, with a configurable
  remote (#246)
- Ticket-opening is provider-neutral, with a dynamic per-row footer instead
  of a fixed key hint (#203); the key itself moved from `l` to `t` (#204)

### Fixed

- Self-update (`u`) runs in a subprocess, avoiding a TTY hang (#239)
- Workspaces are deduplicated by worktree path instead of by a name that can
  collide (#234)
- Highlighted dashboard row keeps its repo color (#240)
- Branch refs are reaped from a fresh worktree read instead of a stale cycle
  snapshot (#230)
- Manual close recognizes squash and rebase merges, not just fast-forward
  merges (#205)
- A `use_worktree: false` workspace is named after the repo, not `master`
  (#249)
- Cockpit's own workspace is excluded from cwd-based workspace matching
  (#248)
- Cross-session fallback dropped from the statusline context pill, which was
  showing stale data (#198)

## Adding entries

When you land a notable PR, add a line under the matching heading above
(`Added` / `Changed` / `Fixed`). Routine `chore`/`ci`/`build`/`test`/
docs-only commits and automatic version bumps don't need an entry.
