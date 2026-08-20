# Changelog

## [1.11.0](https://github.com/khivi/cockpit/compare/v1.10.0...v1.11.0) (2026-08-20)


### Features

* **/cockpit:new:** --skill, smarter args, PR-for-branch, prompt_prefix ([#41](https://github.com/khivi/cockpit/issues/41)) ([bbcbc69](https://github.com/khivi/cockpit/commit/bbcbc69c01aaa0940d0c24d7f8445de71f156070))
* add Trello as a ticket provider ([#231](https://github.com/khivi/cockpit/issues/231)) ([0ce760b](https://github.com/khivi/cockpit/commit/0ce760b1d7b201114762e05b0424007695b877a8))
* autoclose coworker worktrees when merged and clean ([#31](https://github.com/khivi/cockpit/issues/31)) ([b6191d9](https://github.com/khivi/cockpit/commit/b6191d96426dc19e1131b050a874a7069f6caebe))
* base footer ahead-count on the PR base branch + configurable remote ([#246](https://github.com/khivi/cockpit/issues/246)) ([d3e922c](https://github.com/khivi/cockpit/commit/d3e922cbaf4f4b58c63f0483750b656c7977f2e4))
* **cache:** add provider-neutral ticket title, rename linear cache key to ticket ([#252](https://github.com/khivi/cockpit/issues/252)) ([3eb70ce](https://github.com/khivi/cockpit/commit/3eb70ce671a973f09201e451bc37acfda5110741))
* **ci:** always-on ci pill, visible pending dot, ci error indicator ([#96](https://github.com/khivi/cockpit/issues/96)) ([bd72442](https://github.com/khivi/cockpit/commit/bd7244230ff45d85672255e3b48de79608ef1d4e))
* **ci:** skip configurable check names from failure count ([#114](https://github.com/khivi/cockpit/issues/114)) ([14bbbfd](https://github.com/khivi/cockpit/commit/14bbbfdf6bc98cb2a64c2eaa87e96f76a280e9ac))
* **cli:** default bare `cockpit` to `watch` ([#170](https://github.com/khivi/cockpit/issues/170)) ([6cb78fa](https://github.com/khivi/cockpit/commit/6cb78fa66bf8434cbd7f6f53523143ab59b18b09))
* **close:** --force can close a teammate's pushed-but-unmerged PR worktree ([#122](https://github.com/khivi/cockpit/issues/122)) ([c1e5294](https://github.com/khivi/cockpit/commit/c1e5294f180e183bb2b29018578d6ea252505e5e))
* **close:** restore `cockpit close` CLI + /cockpit:close command ([#207](https://github.com/khivi/cockpit/issues/207)) ([6a66126](https://github.com/khivi/cockpit/commit/6a661262badfc24645e9f149c3b923b016e0b3d5))
* **cmux:** fold coworker reviews into one bottom sidebar group ([#284](https://github.com/khivi/cockpit/issues/284)) ([d6b6267](https://github.com/khivi/cockpit/commit/d6b6267f7395a77c7a1f1732cc0a09a169efb6f2))
* **cmux:** fold stacked PRs into one sidebar group ([#281](https://github.com/khivi/cockpit/issues/281)) ([f3c38ea](https://github.com/khivi/cockpit/commit/f3c38ea0444acbe4e87ab481d639dbc2dfb45891))
* **cmux:** per-repo sidebar_color for workspace entries + cycle log ([#126](https://github.com/khivi/cockpit/issues/126)) ([973797d](https://github.com/khivi/cockpit/commit/973797d1b0559d8613dc0ba543569d913f5fd663))
* **cockpit:** own cship.toml + add --footer flag ([#56](https://github.com/khivi/cockpit/issues/56)) ([2de4fca](https://github.com/khivi/cockpit/commit/2de4fcae58cf80c1a2e84e8638a211be81cfe75e))
* **cockpit:** reap stranded workspaces + centralize teardown ([#60](https://github.com/khivi/cockpit/issues/60)) ([7e3f91a](https://github.com/khivi/cockpit/commit/7e3f91aea592c489168a1f34fa0a9fd9866e68bf))
* **cockpit:** side_bar_from_file flag for headless/Linux mode ([#55](https://github.com/khivi/cockpit/issues/55)) ([35387c4](https://github.com/khivi/cockpit/commit/35387c4f66aea0b811748584baf0d8ae653b3de9))
* **config:** orgs — a load-time defaults layer for many-small-repos setups ([#293](https://github.com/khivi/cockpit/issues/293)) ([8dd8b2b](https://github.com/khivi/cockpit/commit/8dd8b2bcc1c1c8c8858faed32336ca9f81d22601))
* **cycle:** per-repo fast_skills and slow_skills config ([#111](https://github.com/khivi/cockpit/issues/111)) ([4a6ce2b](https://github.com/khivi/cockpit/commit/4a6ce2b72ebd22c327a4e16821d3807bd8cc54c9))
* **cycle:** prune stale worktree metadata + label dirty main-siblings ([#138](https://github.com/khivi/cockpit/issues/138)) ([211304c](https://github.com/khivi/cockpit/commit/211304c183ef8fb6286c5fe960630cad2811c759))
* **daemon:** auto-create worktrees for my PRs + review_prs for coworker PRs ([#129](https://github.com/khivi/cockpit/issues/129)) ([eac588c](https://github.com/khivi/cockpit/commit/eac588ca6d0fe61f6ae98246602cde36402a5cd3))
* **daemon:** SIGUSR1 also runs fast tick; per-tick locking ([#107](https://github.com/khivi/cockpit/issues/107)) ([cd84e99](https://github.com/khivi/cockpit/commit/cd84e990ff5415ec3f877ea3688de6561f37c83a))
* default new-workspace modal to the cursor row's repo on header rows ([#238](https://github.com/khivi/cockpit/issues/238)) ([4f2c10c](https://github.com/khivi/cockpit/commit/4f2c10cf674581068d264c3216ad8e0e6f50a6f5))
* **devdone:** Linear "dev done" sidebar pill, footer-resolved + cached ([#158](https://github.com/khivi/cockpit/issues/158)) ([6f8c2ee](https://github.com/khivi/cockpit/commit/6f8c2ee2b452c61c86c41896b70001a092fc3408))
* **devdone:** swap dev-done pill icon to finish-line ([#166](https://github.com/khivi/cockpit/issues/166)) ([3185f28](https://github.com/khivi/cockpit/commit/3185f28a6cb085038588250d2acc4c85baf226f4))
* drop [repo] prefix from sidebar workspace names ([#235](https://github.com/khivi/cockpit/issues/235)) ([88552bd](https://github.com/khivi/cockpit/commit/88552bd67e9822e1599e56c14a7edfb4d8c5dfa0))
* **footer:** add ↗N ahead-of-base segment, daemon-cached ([#70](https://github.com/khivi/cockpit/issues/70)) ([3e54001](https://github.com/khivi/cockpit/commit/3e5400149648d5d43cbe4f62e402951904f0aea7))
* **footer:** add ↻N rebase-staleness segment + orphan pill ([#68](https://github.com/khivi/cockpit/issues/68)) ([a726ea7](https://github.com/khivi/cockpit/commit/a726ea778a7020f6ca1bee10922279ffa12389dc))
* **footer:** delegate statusline to cship binary via use_cship flag ([#53](https://github.com/khivi/cockpit/issues/53)) ([44443bc](https://github.com/khivi/cockpit/commit/44443bcea2c4d30c410ee91560f41868866520f4))
* **footer:** idempotent --footer install + verbose state lines ([#73](https://github.com/khivi/cockpit/issues/73)) ([713e8ee](https://github.com/khivi/cockpit/commit/713e8ee929878c266e975b7712176649b2b5f37e))
* **footer:** line 1 reorder + per-segment status + per-state colors ([#66](https://github.com/khivi/cockpit/issues/66)) ([e839779](https://github.com/khivi/cockpit/commit/e8397793673d438bb94b8568c5a6418fab6a902c))
* **footer:** pack line 1 with model/mode/branch/age, move PR identity to line 2 ([#65](https://github.com/khivi/cockpit/issues/65)) ([345b343](https://github.com/khivi/cockpit/commit/345b343c1f60b2e72dc87edd3011013397367e86))
* **footer:** pill decisions in cache + opt-in cship-style statusline ([#52](https://github.com/khivi/cockpit/issues/52)) ([a23190a](https://github.com/khivi/cockpit/commit/a23190a5824a4349a05531f3654b71d42b2c19f0))
* **footer:** pr-comments pill + fix lowercase Linear ticket extraction ([#110](https://github.com/khivi/cockpit/issues/110)) ([a2fda9d](https://github.com/khivi/cockpit/commit/a2fda9df0bd7395417a9d1ef3bb2ff8c9633cd93))
* **footer:** restore [custom.*] rendering via $starship_prompt ([#58](https://github.com/khivi/cockpit/issues/58)) ([d40761f](https://github.com/khivi/cockpit/commit/d40761fb1e1f12af63d51b2dc40cefc2b69dcd89))
* **footer:** theme:dark|light config + statusline cost/context pills ([#125](https://github.com/khivi/cockpit/issues/125)) ([d8a2f72](https://github.com/khivi/cockpit/commit/d8a2f724f5fb5245a4c43906b26fb70ad6ae735a))
* gate review_prs to collaborators, harden daemon/TUI, document backends ([#242](https://github.com/khivi/cockpit/issues/242)) ([2cacbab](https://github.com/khivi/cockpit/commit/2cacbab0fb20091ef005d765a173098a931b0dbf))
* **gh:** respect branch protection required checks, skip-list as fallback ([#115](https://github.com/khivi/cockpit/issues/115)) ([15ef88c](https://github.com/khivi/cockpit/commit/15ef88c6af2c1cc3afcded622276e0241fa211e7))
* group worktree table rows under per-repo header rows ([#233](https://github.com/khivi/cockpit/issues/233)) ([2a66e6b](https://github.com/khivi/cockpit/commit/2a66e6b76597984c858cfd8a52364a8ba9c133fd))
* **hooks:** plugin owns loop=🔄 pill with transcript-accurate Stop ([#57](https://github.com/khivi/cockpit/issues/57)) ([eab9e02](https://github.com/khivi/cockpit/commit/eab9e02bcdcf396ec12d362764cf51f109204a8b))
* **keep:** protect user-spawned PR worktrees from auto-reap on merge ([#134](https://github.com/khivi/cockpit/issues/134)) ([3a4beeb](https://github.com/khivi/cockpit/commit/3a4beeb36f031c4f3e5a865585eae8432fc5382f))
* **label:** derive workspace sidebar name from branch, not dir ([#167](https://github.com/khivi/cockpit/issues/167)) ([ca468f6](https://github.com/khivi/cockpit/commit/ca468f65fbb7255968644e2122ee0f4531510e0d))
* **limux:** run autoclose + backend-agnostic reconcile on limux, not just cmux ([#200](https://github.com/khivi/cockpit/issues/200)) ([cc7337e](https://github.com/khivi/cockpit/commit/cc7337e40c3feffbdc6800d0a85255ceb6c8815e))
* **linear:** route positional Linear keys to the matching repo ([#97](https://github.com/khivi/cockpit/issues/97)) ([124436e](https://github.com/khivi/cockpit/commit/124436e64e40f0e52226dd946d196bae72518eeb))
* **linear:** transition linked tickets to Done on PR merge (opt-in) ([#177](https://github.com/khivi/cockpit/issues/177)) ([a6c6c60](https://github.com/khivi/cockpit/commit/a6c6c60c82078b22adc92a6b92529b676e3c9a39))
* **logs:** uniform dim-padded verb prefixes; split colors module ([#82](https://github.com/khivi/cockpit/issues/82)) ([9bc1baa](https://github.com/khivi/cockpit/commit/9bc1baa2e69e827b0b1040375c3b1dfc9036f521))
* **nudge:** grace period before nudging a fresh no-PR worktree ([#193](https://github.com/khivi/cockpit/issues/193)) ([9f7b4cc](https://github.com/khivi/cockpit/commit/9f7b4cc5c88852a714a96243efa12320e7f4add5))
* **nudge:** muted pill on sidebar and footer ([#94](https://github.com/khivi/cockpit/issues/94)) ([a19a373](https://github.com/khivi/cockpit/commit/a19a3735f58be8af6b62a879a02e121968397b89))
* **pills:** cluster ahead counts next to branch, add powerline separator ([#78](https://github.com/khivi/cockpit/issues/78)) ([c71ff7f](https://github.com/khivi/cockpit/commit/c71ff7f73d0fcf631ed4c3b7ad100a6e2c24ab94))
* **pills:** coworker owner pill; group refreshed log by ownership ([#83](https://github.com/khivi/cockpit/issues/83)) ([cde2274](https://github.com/khivi/cockpit/commit/cde22744751cb27bd067fb444cdf87cdb6a071db))
* **pills:** emit ci_passed sentinel when sidebar would otherwise be empty ([#67](https://github.com/khivi/cockpit/issues/67)) ([651a32b](https://github.com/khivi/cockpit/commit/651a32bc0bee711ec472f3801b37b4dfc44daa6c))
* **pills:** swap dirty-marker glyphs for self-evident read ([#77](https://github.com/khivi/cockpit/issues/77)) ([f34227e](https://github.com/khivi/cockpit/commit/f34227eb7ed3d97d956ce02aa2ad3b469531193e))
* **reaper:** reap stale local branches with no worktree ([#156](https://github.com/khivi/cockpit/issues/156)) ([1d43ff3](https://github.com/khivi/cockpit/commit/1d43ff3f790f37d7418861b8b10ac6f915d2b1f9))
* reconcile workspace sidebar colour on the fast tick ([#254](https://github.com/khivi/cockpit/issues/254)) ([83b469e](https://github.com/khivi/cockpit/commit/83b469e80a9c516468fc7568677a7b4b7e157512))
* **rename:** auto-reconcile workspace names to the worktree dir ([#163](https://github.com/khivi/cockpit/issues/163)) ([a2426b0](https://github.com/khivi/cockpit/commit/a2426b0e09a088bf993d251a212b521d7cb9bd34))
* **review:** configurable review_command + /cockpit:review default for review_prs ([#206](https://github.com/khivi/cockpit/issues/206)) ([2dada45](https://github.com/khivi/cockpit/commit/2dada4569eee968aac7dadbcd8b450650f60724e))
* **semver:** idempotent parent-relative bump hook + drop CI gate ([#72](https://github.com/khivi/cockpit/issues/72)) ([1537dc8](https://github.com/khivi/cockpit/commit/1537dc8511b3a28b786878522e9e027ec1c8906d))
* show owning repo in workspace name and statusline footer ([#228](https://github.com/khivi/cockpit/issues/228)) ([5d9c4c4](https://github.com/khivi/cockpit/commit/5d9c4c4cc6b4abbb70db5a5a09e8bb97d499bd56))
* **sidebar:** key the coworker-review fold by org and fold a lone review ([#310](https://github.com/khivi/cockpit/issues/310)) ([a72abdd](https://github.com/khivi/cockpit/commit/a72abddf8d924001dc70670d32d984f05eef0f60))
* skip dependabot PRs in review-spawn unless dependabot=true ([#232](https://github.com/khivi/cockpit/issues/232)) ([92e6d9e](https://github.com/khivi/cockpit/commit/92e6d9e0e4888b25448530039a291c6ecc12a6f9))
* **spawn:** /cockpit:new &lt;linear-id&gt; via MCP, gated by use_linear ([#90](https://github.com/khivi/cockpit/issues/90)) ([c5f40a9](https://github.com/khivi/cockpit/commit/c5f40a9e7b830e64b1221acecf41fbec505b7f19))
* **spawn:** accept GitHub Actions run/job URL ([#102](https://github.com/khivi/cockpit/issues/102)) ([f90ef84](https://github.com/khivi/cockpit/commit/f90ef842640aa200704ece8d86f98ae94c025388))
* **spawn:** bare `cockpit new` auto-registers cwd repo (in_place) + in-place workspace ([#213](https://github.com/khivi/cockpit/issues/213)) ([c067d79](https://github.com/khivi/cockpit/commit/c067d7914e26895dc4d27f4d6701dcf993fcb491))
* **spawn:** deduplicate workspaces by path when name slug differs ([#155](https://github.com/khivi/cockpit/issues/155)) ([bdd1182](https://github.com/khivi/cockpit/commit/bdd1182678c31350ee3e51547cb79c554b4106e6))
* **spawn:** deliver prompt_prefix as its own turn, task as a separate send ([#211](https://github.com/khivi/cockpit/issues/211)) ([9378808](https://github.com/khivi/cockpit/commit/93788082578096734c7d223ea471685abc4593bf))
* **spawn:** re-attach existing prefixed branch; harden --force close; uv bootstrap ([#87](https://github.com/khivi/cockpit/issues/87)) ([6aabf91](https://github.com/khivi/cockpit/commit/6aabf914d50cea4385392ddd7105b7aca779ce71))
* **spawn:** seed plan-only only when there's context ([#128](https://github.com/khivi/cockpit/issues/128)) ([cb79f4a](https://github.com/khivi/cockpit/commit/cb79f4abc01a8a1b78fe33a0ce4cb7f738823f3d))
* **spawn:** Slack thread source — codename branch + MCP-delegated fetch ([#188](https://github.com/khivi/cockpit/issues/188)) ([5e82fa0](https://github.com/khivi/cockpit/commit/5e82fa01319912a66822107a1ba4494be8be3e02))
* **stacks:** head a stack with its tip, nest it one level ([#290](https://github.com/khivi/cockpit/issues/290)) ([1bb1921](https://github.com/khivi/cockpit/commit/1bb1921fd502f34f63117dbae7f0ba49d3bb7ee1))
* **startup:** guard against missing git/gh on PATH at startup ([#137](https://github.com/khivi/cockpit/issues/137)) ([0d02d7e](https://github.com/khivi/cockpit/commit/0d02d7ee2fc727e12ce39c49f6b71aca81b5a180))
* **statusline:** add statusline_hide config + rename linear field to ticket ([#266](https://github.com/khivi/cockpit/issues/266)) ([9edbc2a](https://github.com/khivi/cockpit/commit/9edbc2a1af5ae728e2c5985c92c2cf2278822555))
* **teardown:** delete local branch after worktree removal ([#143](https://github.com/khivi/cockpit/issues/143)) ([fe65a6d](https://github.com/khivi/cockpit/commit/fe65a6ddc3aa36a6bd41d833bc1c65526a2b8948))
* **teardown:** tear down the branch when closing a worktree-less repo on a feature branch ([#262](https://github.com/khivi/cockpit/issues/262)) ([92fe58e](https://github.com/khivi/cockpit/commit/92fe58e69b9a14f098536fd026b6a2a52d0d8adb))
* **tickets:** add Jira ticket provider ([#223](https://github.com/khivi/cockpit/issues/223)) ([8569abb](https://github.com/khivi/cockpit/commit/8569abb4b9a5b89e7d3153c8e35bcbaff9709477))
* **tickets:** GitHub-issue provider + tickets config object (replaces use_linear) ([#201](https://github.com/khivi/cockpit/issues/201)) ([cf2d1cc](https://github.com/khivi/cockpit/commit/cf2d1cca2ac8f3fba4e32ea244802b8eb35105a1))
* **tickets:** resolve provider credentials per org, keep them out of spawned sessions ([#299](https://github.com/khivi/cockpit/issues/299)) ([a5c9ea0](https://github.com/khivi/cockpit/commit/a5c9ea0b28b2533ec28a0a825bff7b6f3a9387e5))
* **tickets:** route a ticket to its repo by project/keys/board ([#304](https://github.com/khivi/cockpit/issues/304)) ([6ee4e98](https://github.com/khivi/cockpit/commit/6ee4e985c5eb9faf56a51919d85e27713f9c1e21))
* **tui:** `w` key to open/focus a worktree's workspace (limux-aware) ([#185](https://github.com/khivi/cockpit/issues/185)) ([f0baafa](https://github.com/khivi/cockpit/commit/f0baafadc28e3a47371e6a74893552f1be989374))
* **tui:** 🔔 glyph when a PR has an actionable, unmuted nudge ([#189](https://github.com/khivi/cockpit/issues/189)) ([37c6d1c](https://github.com/khivi/cockpit/commit/37c6d1cdb2607d90f85d95aefa727dad4bfd764e))
* **tui:** convert the watch daemon into an installable Textual TUI ([#169](https://github.com/khivi/cockpit/issues/169)) ([2e5eb0e](https://github.com/khivi/cockpit/commit/2e5eb0edad2917a290cf4f2912103ecd3673e801))
* **tui:** distinct PR/Linear status icons + adjacent columns ([#186](https://github.com/khivi/cockpit/issues/186)) ([079706f](https://github.com/khivi/cockpit/commit/079706f226b1d9649c215091499aa9480304cc84))
* **tui:** double-click a repo header row to open the new-workspace modal ([#251](https://github.com/khivi/cockpit/issues/251)) ([f4f5622](https://github.com/khivi/cockpit/commit/f4f56227c35f7371252850987b89f8b0a00f845f))
* **tui:** drop the command-palette hint from the footer ([#202](https://github.com/khivi/cockpit/issues/202)) ([083c2fd](https://github.com/khivi/cockpit/commit/083c2fd21bdc8f42ec93a4fc5125a5cb11a3e59a))
* **tui:** flag an unresolved ticket state with a red ! in the Status column ([#243](https://github.com/khivi/cockpit/issues/243)) ([dad300d](https://github.com/khivi/cockpit/commit/dad300de8cefc2c5adfa48c49aef4b92efdf9077))
* **tui:** fold `w` into `f`, invert in_place→use_worktree, route `n` per repo type ([#245](https://github.com/khivi/cockpit/issues/245)) ([2452a85](https://github.com/khivi/cockpit/commit/2452a85a284a03b6aabcf23f84d79305d73a4ed2))
* **tui:** fold H into h — the hidden row expands, unhide is one key ([#279](https://github.com/khivi/cockpit/issues/279)) ([9e4a246](https://github.com/khivi/cockpit/commit/9e4a24658c0f0953e9e9c180721e97fefcdb7fdc))
* **tui:** group ChangeLog by relative-age, share renderer with update modal ([#222](https://github.com/khivi/cockpit/issues/222)) ([f9453b8](https://github.com/khivi/cockpit/commit/f9453b8eaa5a1222a265f0f4fd6e2ebedf06210d))
* **tui:** group table columns by domain and add hover tooltips ([#260](https://github.com/khivi/cockpit/issues/260)) ([2cfda74](https://github.com/khivi/cockpit/commit/2cfda74b479078f8d3bcdb9db17a375a29c60c33))
* **tui:** icon-ify the Linear Status column header and values ([#175](https://github.com/khivi/cockpit/issues/175)) ([af53810](https://github.com/khivi/cockpit/commit/af53810ac2149def315acf90880aea8d8f3953c7))
* **tui:** indent stacked PRs; keep the cmux group header its own row ([#285](https://github.com/khivi/cockpit/issues/285)) ([fdd260d](https://github.com/khivi/cockpit/commit/fdd260d60e2d3e850abfb2e2aa68028cd613fb36))
* **tui:** lazy-scroll ChangeLog, rename from Notes ([#221](https://github.com/khivi/cockpit/issues/221)) ([30229ea](https://github.com/khivi/cockpit/commit/30229eaeb810e252f89aceff828663ed7525ad13))
* **tui:** make the repo grouping read as a hierarchy ([#307](https://github.com/khivi/cockpit/issues/307)) ([c9ff131](https://github.com/khivi/cockpit/commit/c9ff131267fc13d3010f51377b44b2ed3662fcf2))
* **tui:** move the dirty column next to PR ([#263](https://github.com/khivi/cockpit/issues/263)) ([225f8af](https://github.com/khivi/cockpit/commit/225f8af0951cd725e3916aa4e5a6383d0032c21e))
* **tui:** park repos with h/H — dormant polling, one summary row, sidebar cleared ([#278](https://github.com/khivi/cockpit/issues/278)) ([59ada9d](https://github.com/khivi/cockpit/commit/59ada9dd78b44c9d1e0294bd8118627a6cd07926))
* **tui:** prefix worktree label with repo on cross-repo collision ([#216](https://github.com/khivi/cockpit/issues/216)) ([856ac55](https://github.com/khivi/cockpit/commit/856ac5514d289c5f80cd1963a73958998c691d3c))
* **tui:** provider-neutral ticket open + dynamic per-row footer ([#203](https://github.com/khivi/cockpit/issues/203)) ([68d4ee4](https://github.com/khivi/cockpit/commit/68d4ee43ec611283cbdff5a1c3cc0a316e03c3ad))
* **tui:** rebind Open-ticket from l to t ([#204](https://github.com/khivi/cockpit/issues/204)) ([6ff6f23](https://github.com/khivi/cockpit/commit/6ff6f23a4a203c3f5df06a2578db5fd8be407305))
* **tui:** release notes via `r` key + auto-show after self-update ([#219](https://github.com/khivi/cockpit/issues/219)) ([fab5986](https://github.com/khivi/cockpit/commit/fab5986f204d1f7735c767ee3cb904f82acd3fe1))
* **tui:** reorder worktree-table header, icon-ify Dirty column ([#173](https://github.com/khivi/cockpit/issues/173)) ([3c2a280](https://github.com/khivi/cockpit/commit/3c2a28075a20dd008b04ccc50dfeb24990ad3d07))
* **tui:** repo picker in the new-workspace modal ([#171](https://github.com/khivi/cockpit/issues/171)) ([676c556](https://github.com/khivi/cockpit/commit/676c556612ed2c247e40cbcb74dd9f05b1ac4c01))
* **tui:** republish the worktree table per-repo during the slow tick ([#190](https://github.com/khivi/cockpit/issues/190)) ([ae3f38c](https://github.com/khivi/cockpit/commit/ae3f38c86757f8749832e3c9c1fbdcf8397ce8f3))
* **tui:** run the self-update check on every slow tick, not hourly ([#187](https://github.com/khivi/cockpit/issues/187)) ([5cef30f](https://github.com/khivi/cockpit/commit/5cef30fb65f59796a4e3c809d3e8cfb26361caac))
* **tui:** show PR author in the worktree table for coworker PRs ([#178](https://github.com/khivi/cockpit/issues/178)) ([3e0bf33](https://github.com/khivi/cockpit/commit/3e0bf338d2287f34af4795a4e40464ea8de225cc))
* **tui:** show running cockpit version in the header top-left ([#174](https://github.com/khivi/cockpit/issues/174)) ([f3210ea](https://github.com/khivi/cockpit/commit/f3210eaa7316f47c8386622576048e58a63e689f))
* **tui:** show unaddressed/total ratio in the worktree table 💬 column ([#180](https://github.com/khivi/cockpit/issues/180)) ([b226b78](https://github.com/khivi/cockpit/commit/b226b78dfec92b3ef11120be19273960df12f299))
* **tui:** snooze a PR until someone comments or approves ([#312](https://github.com/khivi/cockpit/issues/312)) ([65fc93a](https://github.com/khivi/cockpit/commit/65fc93a05987178f9a4140aaf62dc984da002c49))
* **update:** replace shell supervisor with `cockpit update` + Python self-re-exec ([#194](https://github.com/khivi/cockpit/issues/194)) ([67ddcce](https://github.com/khivi/cockpit/commit/67ddcce9459bc1514944daa0eb9f25abb464852b))
* **update:** sync the uv-tool daemon from the plugin cache on SessionStart ([#256](https://github.com/khivi/cockpit/issues/256)) ([2a27b7c](https://github.com/khivi/cockpit/commit/2a27b7c0f63651316427f14c8de100d577169977))
* warn at startup when a repo's origin/{base} doesn't resolve ([#244](https://github.com/khivi/cockpit/issues/244)) ([0dfacd1](https://github.com/khivi/cockpit/commit/0dfacd1da6464c33abe1095b0b6085a26743a457))


### Bug Fixes

* **/cockpit:new:** --name &lt;slug&gt; always creates a fresh prefixed branch ([#50](https://github.com/khivi/cockpit/issues/50)) ([8648b33](https://github.com/khivi/cockpit/commit/8648b33dd3ce1c736e7f881a53413e861254ef57))
* **/cockpit:new:** --repo overrides cwd for global --skill; prefix-aware idempotency ([#44](https://github.com/khivi/cockpit/issues/44)) ([1804fe1](https://github.com/khivi/cockpit/commit/1804fe1d9c7d5ec4d2b60e1f162b038bd3a6006e))
* **/cockpit:new:** --repo with --skill falls back to global skills ([#42](https://github.com/khivi/cockpit/issues/42)) ([41591bd](https://github.com/khivi/cockpit/commit/41591bdd35f60da7bdaf6237434f5481a5391b47))
* **/cockpit:new:** error on unknown --repo; add /cockpit:repos; force slash-exec ([#49](https://github.com/khivi/cockpit/issues/49)) ([9200d49](https://github.com/khivi/cockpit/commit/9200d4947b9d21b01d1c306ea1b929512e8dceb1))
* **/cockpit:new:** kick daemon after spawn so pills/cache appear immediately ([#48](https://github.com/khivi/cockpit/issues/48)) ([f2d926b](https://github.com/khivi/cockpit/commit/f2d926bdf73a3b04276538a5b8ff696e4f500a89))
* **/cockpit:new:** prefer global skills over repo-local for --skill ([#43](https://github.com/khivi/cockpit/issues/43)) ([c3813f8](https://github.com/khivi/cockpit/commit/c3813f82163bad7ee0d93149248e25f45caf52ee))
* autoclose and orphan-spawn handle reused branches ([#46](https://github.com/khivi/cockpit/issues/46)) ([c5790bf](https://github.com/khivi/cockpit/commit/c5790bf170e6b9048ab1ca10664bd6bc11174bba))
* autoclose squash-merged PRs via merge-head SHA ([#39](https://github.com/khivi/cockpit/issues/39)) ([a6a2348](https://github.com/khivi/cockpit/commit/a6a2348cbe2e739c4b0f92d7797e125acd3248ef))
* **autoclose:** gate teardown on merge-head reachability, not branch-name presence ([#132](https://github.com/khivi/cockpit/issues/132)) ([4a9e58f](https://github.com/khivi/cockpit/commit/4a9e58f5b53159c142538be9611eb0c08ed8a884))
* **autoclose:** paginate merged-PR fetch with a date window ([#103](https://github.com/khivi/cockpit/issues/103)) ([4630820](https://github.com/khivi/cockpit/commit/4630820d31e3b739f3aa1ab814faf36eea45379d))
* **autoclose:** sweep stranded main siblings + feat(spawn): -- prompt addendum ([#99](https://github.com/khivi/cockpit/issues/99)) ([217d6bf](https://github.com/khivi/cockpit/commit/217d6bf74b0b2598c9f91910bed09a686ecaaab5))
* **autoclose:** trust gh merge state, smart-skip on PR signals ([#98](https://github.com/khivi/cockpit/issues/98)) ([535b782](https://github.com/khivi/cockpit/commit/535b78292e94e3dfcf6ad0de5bacf9e4d10878fd))
* **cache:** deterministic PR snapshot selection for reused branches ([#127](https://github.com/khivi/cockpit/issues/127)) ([f9b43d6](https://github.com/khivi/cockpit/commit/f9b43d6528c0a10fe38f0cf32e2db9b057f31945))
* **card:** suppress merged PR after its branch is reused for new work ([#159](https://github.com/khivi/cockpit/issues/159)) ([1fe525c](https://github.com/khivi/cockpit/commit/1fe525c4435e545130668802a557c40ae9fddff5))
* close cmux workspace by cwd-matching, not directory name ([#38](https://github.com/khivi/cockpit/issues/38)) ([3e9012a](https://github.com/khivi/cockpit/commit/3e9012aff340137a28b951a807804c9be5b6a111))
* **close:** squash-merged PR worktree no longer hard-blocks close ([#133](https://github.com/khivi/cockpit/issues/133)) ([#154](https://github.com/khivi/cockpit/issues/154)) ([c0291eb](https://github.com/khivi/cockpit/commit/c0291eb0be13e1ac39ad05ee8f2bb6d43ac53d3a))
* **cmux:** devdone pill shows ticket title, not the id ([#258](https://github.com/khivi/cockpit/issues/258)) ([245cbd3](https://github.com/khivi/cockpit/commit/245cbd32ce3743af7e77bbc8a85176735f56ab8f))
* **cmux:** exempt any main-branch worktree from rename, not just the primary ([#195](https://github.com/khivi/cockpit/issues/195)) ([537b902](https://github.com/khivi/cockpit/commit/537b902ecd21a2d1e56d1da64a0e9ada6b99a9f6))
* **cmux:** limux backend support for /cockpit:close, /cockpit:list, /cockpit:focus, /cockpit:new ([#116](https://github.com/khivi/cockpit/issues/116)) ([4375d6e](https://github.com/khivi/cockpit/commit/4375d6ed34f20cdba6cc19dde75f0edd2f862bb0))
* **cmux:** park a lone coworker review at the bottom of the sidebar ([#286](https://github.com/khivi/cockpit/issues/286)) ([a9b0603](https://github.com/khivi/cockpit/commit/a9b060332112ab3493446380ac035e8e6008ee4d))
* **cockpit:** close cmux workspace before removing worktree on autoclose ([#51](https://github.com/khivi/cockpit/issues/51)) ([a075412](https://github.com/khivi/cockpit/commit/a0754122fbb69af363870402abb4939cf6198a92))
* **commands:** use $ARGUMENTS not "$@" for slash-command arg passing ([#140](https://github.com/khivi/cockpit/issues/140)) ([ba182e4](https://github.com/khivi/cockpit/commit/ba182e49a8e14bdf031a5ce74449588b3991af53))
* **config:** ship + validate config.example.json; resync state-machine diagrams ([#209](https://github.com/khivi/cockpit/issues/209)) ([06ccf4f](https://github.com/khivi/cockpit/commit/06ccf4ff2ec53bcf446fd5dc4cab716d8506708b))
* **cship:** switch bundled config to [cship]/lines wrapper schema ([#62](https://github.com/khivi/cockpit/issues/62)) ([a75b1e4](https://github.com/khivi/cockpit/commit/a75b1e464fd693cf3c8af192198979eba9a80812))
* **cycle:** never autoclose a worktree whose branch has an OPEN PR ([#294](https://github.com/khivi/cockpit/issues/294)) ([ce6429f](https://github.com/khivi/cockpit/commit/ce6429f17be84d78573a26f07d1fd473dd618f58))
* **daemon,idle-pill:** SIGHUP cleanup + skip dead cmux workspaces ([#112](https://github.com/khivi/cockpit/issues/112)) ([06133ec](https://github.com/khivi/cockpit/commit/06133ec6232d34c3e90fd309cb0ba7dc6f66886a))
* **daemon:** re-assert the pidfile each fast tick so a mid-run loss self-heals ([#264](https://github.com/khivi/cockpit/issues/264)) ([b19cc24](https://github.com/khivi/cockpit/commit/b19cc24a02c1edff4c0f90782498d80078aed987))
* dedupe workspaces by worktree path, not colliding name ([#234](https://github.com/khivi/cockpit/issues/234)) ([97d7068](https://github.com/khivi/cockpit/commit/97d706881a6834d04088f07390e7b9ed584b83fa))
* **footer:** drop cwd duplication; survive fresh sessions + ISO resets_at ([#64](https://github.com/khivi/cockpit/issues/64)) ([d1bcfbe](https://github.com/khivi/cockpit/commit/d1bcfbebd7c48284a9668ee4063268f6d9b86dab))
* **footer:** refresh PR cache after OPEN→MERGED/CLOSED ([#93](https://github.com/khivi/cockpit/issues/93)) ([4443448](https://github.com/khivi/cockpit/commit/4443448107459ac36e826ce5eab755b68f7f5e2d))
* **footer:** render a single-line statusline on macOS ([#261](https://github.com/khivi/cockpit/issues/261)) ([87a41f9](https://github.com/khivi/cockpit/commit/87a41f961cbaeb9b1c40c7c01b9cb63690a62531))
* **footer:** shim STARSHIP_SHELL=unknown → sh so [custom.*] modules render ([#63](https://github.com/khivi/cockpit/issues/63)) ([84b2876](https://github.com/khivi/cockpit/commit/84b2876319ad06204834946e82e0810ecc91609a))
* **gh:** aggregate all check runs, not just the first 30 ([#95](https://github.com/khivi/cockpit/issues/95)) ([2fc0134](https://github.com/khivi/cockpit/commit/2fc0134d3916f08d03aa96e911282b854e87dbe9))
* **gh:** count bot inline review threads; skip bot summary reviews only ([#139](https://github.com/khivi/cockpit/issues/139)) ([18d3a93](https://github.com/khivi/cockpit/commit/18d3a930236b9143edc710a400ed2465b0f0f31a))
* **gh:** count human summary reviews as unaddressed comments ([#141](https://github.com/khivi/cockpit/issues/141)) ([a5ec7c6](https://github.com/khivi/cockpit/commit/a5ec7c6f2228b692e982a8fe7c8683a9a8c87338))
* **gh:** count null-author review threads (GitHub Copilot) as unaddressed ([#113](https://github.com/khivi/cockpit/issues/113)) ([2f566ca](https://github.com/khivi/cockpit/commit/2f566ca46fd330b583784904f93bb6b42c80ed38))
* **gh:** exclude bot and null authors from unaddressed thread counts ([#135](https://github.com/khivi/cockpit/issues/135)) ([24a5da5](https://github.com/khivi/cockpit/commit/24a5da5c868fd18060edb1c5b15b0822410057ab))
* **gh:** only declare $owner/$name when coworker aliases reference them ([#59](https://github.com/khivi/cockpit/issues/59)) ([2f60e9f](https://github.com/khivi/cockpit/commit/2f60e9f6af5674bdf0969968eff02eade845e75f))
* **hooks:** idle pill needs non-empty value + log cmux stderr ([#106](https://github.com/khivi/cockpit/issues/106)) ([c7eaef1](https://github.com/khivi/cockpit/commit/c7eaef186f8e8b87034654ce5e1320bd850ec404))
* **label:** drop leading base-branch segment from workspace label ([#168](https://github.com/khivi/cockpit/issues/168)) ([d32ad3e](https://github.com/khivi/cockpit/commit/d32ad3eb01357789e160d0dbcea3b0736aef051b))
* **linear:** bump mcp-list pre-flight timeout to 15s for managed connector ([#147](https://github.com/khivi/cockpit/issues/147)) ([6336a79](https://github.com/khivi/cockpit/commit/6336a790cd208b60a96ddf3c1f7ab6d88850b34d))
* **linear:** match PR-body footers case-insensitively ([#184](https://github.com/khivi/cockpit/issues/184)) ([6faa1d4](https://github.com/khivi/cockpit/commit/6faa1d4e89ca3be29e60e419c3963acccb78790c))
* **new:** deliver prompt on workspace attach + add --context ([#117](https://github.com/khivi/cockpit/issues/117)) ([20b56dc](https://github.com/khivi/cockpit/commit/20b56dcf34ff2cdb1227631c1cdd57e3eb844e54))
* **nudge:** make idle gate authoritative + add stale-running escape hatch ([#142](https://github.com/khivi/cockpit/issues/142)) ([b56199e](https://github.com/khivi/cockpit/commit/b56199eed919c963ac79e8df75f1f9c3daed91a5))
* **nudge:** never nudge a merged/closed PR (stops infinite CI-fail loop) ([#151](https://github.com/khivi/cockpit/issues/151)) ([71b57aa](https://github.com/khivi/cockpit/commit/71b57aac0683fe6fddae9816b76dd7abca726aee))
* **nudge:** surface cmux send errors and include message snippet in nudge log ([#108](https://github.com/khivi/cockpit/issues/108)) ([2b8be4b](https://github.com/khivi/cockpit/commit/2b8be4baa773b46a0aa678ccdbb5bb9a302f6624))
* **pills:** add space between `stale` and `↻N` in worktree pill ([#75](https://github.com/khivi/cockpit/issues/75)) ([e667a55](https://github.com/khivi/cockpit/commit/e667a555263aa966d0fe7a25fa95b19f9938bacd))
* reap branch refs from a fresh worktree read, not the stale cycle snapshot ([#230](https://github.com/khivi/cockpit/issues/230)) ([4e774f9](https://github.com/khivi/cockpit/commit/4e774f9105167781738fb8d515a33020d59e9885))
* **reaper:** double-force git worktree remove to override locked trees ([#76](https://github.com/khivi/cockpit/issues/76)) ([67b1d8a](https://github.com/khivi/cockpit/commit/67b1d8af104d6cdb67dc27449fbab85e62c07d59))
* **release:** match release-please's tag format to tag.yml's ([#302](https://github.com/khivi/cockpit/issues/302)) ([0d1649c](https://github.com/khivi/cockpit/commit/0d1649ccf46efc0e7183054818d6e4f1ccf3ecc0))
* **rename:** exempt primary checkout from workspace name reconcile ([#165](https://github.com/khivi/cockpit/issues/165)) ([d5d04bb](https://github.com/khivi/cockpit/commit/d5d04bbbbf77efa228673a8f2d312cf7fcd3a670))
* resolve unpushed check via origin/HEAD instead of @{upstream} ([#35](https://github.com/khivi/cockpit/issues/35)) ([1ce84e7](https://github.com/khivi/cockpit/commit/1ce84e731ddce261b278e8087614f74b1d6d377b))
* run u self-update in a subprocess to avoid a TTY hang ([#239](https://github.com/khivi/cockpit/issues/239)) ([b270edc](https://github.com/khivi/cockpit/commit/b270edcff4c34bd5b58733e875ce06f210fb970d))
* **spawn:** Actions URL always spawns fresh ci-&lt;name&gt; worktree ([#104](https://github.com/khivi/cockpit/issues/104)) ([e3524be](https://github.com/khivi/cockpit/commit/e3524be3a8a6166e9d83648fdace29e73299a68f))
* **spawn:** drop shell sleep backoff from Linear retry prompt ([#160](https://github.com/khivi/cockpit/issues/160)) ([618a524](https://github.com/khivi/cockpit/commit/618a524d07694fbd36ba386d27a9bc74a5c5ed11))
* **spawn:** give trunk-headed PRs (head=main/master) a synthesized worktree branch ([#273](https://github.com/khivi/cockpit/issues/273)) ([b18deee](https://github.com/khivi/cockpit/commit/b18deee858a2e8b9427e86bca57d83be3c60295b))
* **spawn:** name-clash gate on orphan-workspace spawn ([#164](https://github.com/khivi/cockpit/issues/164)) ([b50c9bc](https://github.com/khivi/cockpit/commit/b50c9bce7a77a54468c2cce42b2776142f20c1a0))
* **spawn:** parse --keep when it follows a -- separator ([#144](https://github.com/khivi/cockpit/issues/144)) ([e6d0861](https://github.com/khivi/cockpit/commit/e6d0861d3339ba6dc8b770118231ee3d333703c8))
* **spawn:** retry Linear MCP call once on connection delay ([#136](https://github.com/khivi/cockpit/issues/136)) ([e2e9832](https://github.com/khivi/cockpit/commit/e2e9832bfc2e0600858155b9ebbefe70aa28295e))
* **stacks:** stop duplicating and mis-anchoring the cmux stack group ([#297](https://github.com/khivi/cockpit/issues/297)) ([de8ab76](https://github.com/khivi/cockpit/commit/de8ab7606035ae8e6579a00735d1093b23080640))
* **statusline:** drop cross-session fallback for the context pill ([#197](https://github.com/khivi/cockpit/issues/197)) ([#198](https://github.com/khivi/cockpit/issues/198)) ([c8c83e7](https://github.com/khivi/cockpit/commit/c8c83e7dd225da4b4fa4f3d1e7ca076cfbd83f0d))
* **statusline:** populate the ticket pill from the PR footer, not just the Linear branch regex ([#267](https://github.com/khivi/cockpit/issues/267)) ([d158967](https://github.com/khivi/cockpit/commit/d158967f9ccff01c700d7fcbb496d7d9309680b9))
* **statusline:** render the Trello ticket pill as the card title, not the short link ([#268](https://github.com/khivi/cockpit/issues/268)) ([9f37267](https://github.com/khivi/cockpit/commit/9f3726763025ecfedf24f34e44f10a59de93aa23))
* **stuck:** clear stuck= pill when PR merges or branch orphans ([#146](https://github.com/khivi/cockpit/issues/146)) ([3a5c4d6](https://github.com/khivi/cockpit/commit/3a5c4d6fd9fe44645c2a1d2f6f4468f31160d5f6))
* **teardown:** fast-forward default-branch worktree after close ([#84](https://github.com/khivi/cockpit/issues/84)) ([1eaf795](https://github.com/khivi/cockpit/commit/1eaf795e5f67d6615eea774d9e37fa035b34be40))
* **teardown:** recognize squash/rebase merges on manual close ([#205](https://github.com/khivi/cockpit/issues/205)) ([3200fd7](https://github.com/khivi/cockpit/commit/3200fd77d0755bdf36ba43a109a51431d9b60150))
* **test:** deflake idle-pill stop tests racing the loop/idle pill line ([#148](https://github.com/khivi/cockpit/issues/148)) ([1a646c0](https://github.com/khivi/cockpit/commit/1a646c055732fcbb243ec16c0661e3e04345ff3a))
* **tests:** mock _resolve_tool so headless short-circuit does not bypass cmux_unavailable mock ([#92](https://github.com/khivi/cockpit/issues/92)) ([2c8a3cb](https://github.com/khivi/cockpit/commit/2c8a3cb8165638d08924bdc6699a9a00c700a8ba))
* **tickets:** only Trello renders the card title; others keep the id ([#259](https://github.com/khivi/cockpit/issues/259)) ([64f62fb](https://github.com/khivi/cockpit/commit/64f62fb7e44c898b3d95988fe44f94f6116b5ec2))
* **tui:** exclude cockpit's own workspace from cwd matching ([#248](https://github.com/khivi/cockpit/issues/248)) ([6ebe872](https://github.com/khivi/cockpit/commit/6ebe872d61e4ee26e04eaf0b87c41192d474e12e))
* **tui:** expand the hidden row on a single click and on Enter ([#280](https://github.com/khivi/cockpit/issues/280)) ([9ae6714](https://github.com/khivi/cockpit/commit/9ae671425fb18cb558e4348150e6874aedf9bf88))
* **tui:** hard-exit on quit so a mid-tick gh call can't hang the process ([#253](https://github.com/khivi/cockpit/issues/253)) ([b64e703](https://github.com/khivi/cockpit/commit/b64e703f14daa6bb28bb5117b77c22ab8e7786fc))
* **tui:** keep repo color on the highlighted dashboard row ([#240](https://github.com/khivi/cockpit/issues/240)) ([71c51a8](https://github.com/khivi/cockpit/commit/71c51a87b31ba34cccbb9073ea90d1c549a97e3c))
* **tui:** key PR cache by git nwo, not the config name label ([#257](https://github.com/khivi/cockpit/issues/257)) ([4a831f7](https://github.com/khivi/cockpit/commit/4a831f74be3090f468c499546a62d0f634a48f2e))
* **tui:** name a use_worktree:false workspace after the repo, not master ([#249](https://github.com/khivi/cockpit/issues/249)) ([2526ec2](https://github.com/khivi/cockpit/commit/2526ec2d828b542e88bdf2c7857dc3a1a329284c))
* **tui:** run the TUI on an owned asyncio loop so quit can't hang on a blocked worker ([#255](https://github.com/khivi/cockpit/issues/255)) ([3a9ceef](https://github.com/khivi/cockpit/commit/3a9ceefc38121dc25bc11364a321896091c16d6f))
* **tui:** show Trello card title in ticket-status hover, not the short link ([#265](https://github.com/khivi/cockpit/issues/265)) ([3e65595](https://github.com/khivi/cockpit/commit/3e65595d0d04339b3e964d41796a9453e9dd5e72))
* **update:** pass --no-cache so a version-only bump actually reinstalls ([#181](https://github.com/khivi/cockpit/issues/181)) ([8d45850](https://github.com/khivi/cockpit/commit/8d45850134fba067daa8c86c251e20fd67b2c6cd))
* **update:** qualify plugin id, never abort before the uv reinstall ([#176](https://github.com/khivi/cockpit/issues/176)) ([ec4972e](https://github.com/khivi/cockpit/commit/ec4972e7b473b41766b90e93c17f51da3caf71b5))
* **update:** re-pin footer config via cockpit setup after install ([#183](https://github.com/khivi/cockpit/issues/183)) ([1bd9c03](https://github.com/khivi/cockpit/commit/1bd9c0365f59d1a69ef35ceff9ea522821087784))
* **update:** ship plugin manifests in the wheel so the update check works ([#172](https://github.com/khivi/cockpit/issues/172)) ([4bfc47e](https://github.com/khivi/cockpit/commit/4bfc47e77835b8f7a34c8a57bea1747cd88210c5))
* use git cherry to detect squash-merged commits ([#37](https://github.com/khivi/cockpit/issues/37)) ([471aa13](https://github.com/khivi/cockpit/commit/471aa13b101a517e00c0e41806bcfbff81fd81d2))


### Performance Improvements

* **cycle:** skip base-distance fetch when no feature worktrees ([#100](https://github.com/khivi/cockpit/issues/100)) ([f21bb16](https://github.com/khivi/cockpit/commit/f21bb168e41038269d50dbab8c8819c10290c51b))
* **daemon:** two-tier loop + daemon-only cache writes ([#101](https://github.com/khivi/cockpit/issues/101)) ([9b5b2a4](https://github.com/khivi/cockpit/commit/9b5b2a4d318fc5985dac55de3918cbca020ce35e))
* **linear:** batch slow-tick ticket-state reads + cache identity across ticks ([#191](https://github.com/khivi/cockpit/issues/191)) ([3d316aa](https://github.com/khivi/cockpit/commit/3d316aa7253ead5ef6f2055c4a59771d1880182b))
* **tui:** scope row-action slow kick to the row's repo ([#199](https://github.com/khivi/cockpit/issues/199)) ([c41c726](https://github.com/khivi/cockpit/commit/c41c726b32896b6ff2b0cd3b7389176bbe7fdb73))


### Documentation

* **/cockpit:new:** compact argument-hint so it fits one line ([#47](https://github.com/khivi/cockpit/issues/47)) ([03fbcf3](https://github.com/khivi/cockpit/commit/03fbcf370f8870b319f8db1b1754e2e6c6a32bef))
* add contributor and community docs plus open-source metadata ([#250](https://github.com/khivi/cockpit/issues/250)) ([0ae1bf0](https://github.com/khivi/cockpit/commit/0ae1bf0ba544f8684d7d539a8d8a8f0f6ab9479e))
* **agents:** always use a git worktree for code changes ([#109](https://github.com/khivi/cockpit/issues/109)) ([5787078](https://github.com/khivi/cockpit/commit/57870787d6dac2961965b457bdde0f6c4ddf7bcb))
* **agents:** fix master → main in worktree discipline rule ([#131](https://github.com/khivi/cockpit/issues/131)) ([c70f8f9](https://github.com/khivi/cockpit/commit/c70f8f9480801618aa5fe24fe942de5a64a7617e))
* **agents:** make worktree-discipline negative case explicit ([#208](https://github.com/khivi/cockpit/issues/208)) ([fc244ca](https://github.com/khivi/cockpit/commit/fc244cae02a002bdc9171c27a80da67f4d2cca29))
* backend-agnostic wording for workspace messages ([#119](https://github.com/khivi/cockpit/issues/119)) ([46e24d1](https://github.com/khivi/cockpit/commit/46e24d19115eb7e0b1bf79998d4ad80e4f1d2383))
* **config:** show GitHub tickets provider in config.example.json ([#212](https://github.com/khivi/cockpit/issues/212)) ([b5a3a1c](https://github.com/khivi/cockpit/commit/b5a3a1cd5fbfe4291eaacae1e2f667e360a81b93))
* fix footer.py references (was cockpit.py --footer) ([#22](https://github.com/khivi/cockpit/issues/22)) ([8d74f3a](https://github.com/khivi/cockpit/commit/8d74f3a7b139b43fd5772707194bf9c9b3efa4fc))
* fix install command in README (marketplace is khivi-cockpit, not khivi) ([#40](https://github.com/khivi/cockpit/issues/40)) ([dc67025](https://github.com/khivi/cockpit/commit/dc670251cb3bf65e1f490614b0e2be9d56c103a3))
* **migration:** add brew trust, Linux prereq, daemon stop, setup re-seed ([#272](https://github.com/khivi/cockpit/issues/272)) ([303f026](https://github.com/khivi/cockpit/commit/303f0264d3abaf7af56e45f61581275d73204dcf))
* PyPI trusted-publishing setup + recovery steps ([#271](https://github.com/khivi/cockpit/issues/271)) ([92244e1](https://github.com/khivi/cockpit/commit/92244e1aa4c937c2e4d8a81d783fbe2e77eb8f75))
* README/AGENTS overhaul + nudge-category, comment-ratio, slugify fixes ([#182](https://github.com/khivi/cockpit/issues/182)) ([04840d2](https://github.com/khivi/cockpit/commit/04840d2b7e4a4abd3acef7d89b2e7d83f8d81a70))
* **readme:** document the TUI keybindings ([#210](https://github.com/khivi/cockpit/issues/210)) ([1540a84](https://github.com/khivi/cockpit/commit/1540a841002268e98543ccf3f69f606d74a9faa1))
* state-machine diagrams for PR/Claude/cmux state handling ([#145](https://github.com/khivi/cockpit/issues/145)) ([2e1dc5d](https://github.com/khivi/cockpit/commit/2e1dc5dcc033f2c0aad6decc4e6f327b28c675b4))
* **todo:** stage memory-promotion candidates ([#81](https://github.com/khivi/cockpit/issues/81)) ([dcb5798](https://github.com/khivi/cockpit/commit/dcb5798b8924e62bea35af04de2c16219422c9be))

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
