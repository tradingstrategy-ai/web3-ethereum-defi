# Veranta unified avUSDC vault

- Chain: Base (8453).
- Address: `0x944766f715b51967e56afde5f0aa76ceacc9e7f9`.
- Explorer: [verified proxy](https://basescan.org/address/0x944766f715b51967e56afde5f0aa76ceacc9e7f9).
- Protocol identity: Veranta, formerly known as Avantis. The protocol [renamed itself Veranta in September 2026](https://github.com/Avantis-Labs/avantis_trader_sdk/blob/main/README.md).
- Homepage and app: [Avantis](https://www.avantisfi.com/), [Earn](https://www.avantisfi.com/earn); current brand homepage is [Veranta](https://www.veranta.xyz/).
- Official GitHub organisation: [Avantis-Labs](https://github.com/Avantis-Labs). The historic `avantis-contracts` repository is no longer publicly accessible; use the verified implementation sources rather than claiming a current public contracts repository.
- Documentation: [avUSDC](https://docs.veranta.xyz/liquidity-providers/veranta-lp-vault-avusdc), [withdrawal rules](https://docs.veranta.xyz/liquidity-providers/risk-management).
- DefiLlama: [current Veranta listing](https://defillama.com/protocol/veranta).
- Audits: [protocol index](https://docs.veranta.xyz/security/audits), [Zellic assessment](https://reports.zellic.io/publications/avantis/). Historical reports do not certify later deployed upgrades.
- Social account: [verantaxyz](https://x.com/verantaxyz), as recorded by the current DefiLlama protocol API.
- Strategy tags: `liquidity_provider`, `market_making`, `perpetual_futures`.

## Contract and fee evidence

Researched on 2026-11-04. Etherscan V2 `getsourcecode` resolved the proxy and provided its verified implementation ABI and Solidity source. The source file is `src/AvantisTranche.sol`, contract name `Tranche`.

At Base block 49,030,926, the proxy uses implementation `0xc3769e440e3515ba374372837f76aafdc4fba26d`. The current implementation is `0xe79184763b27fa8bb0eb2d6af1f0123d43af0e89`. Both verified versions were inspected for admission: ordinary ERC-4626 deposits impose no sender/receiver identity approval. Administrative setters and reserve operations have separate governance/manager access controls. Unreviewed implementations remain unknown.

The old `depositCap()` classifier is only a generic compatibility clue; it does not establish Gains Network ownership or epoch withdrawals. The unified vault uses synchronous ERC-4626 deposit and redemption. Retired tranche deployments are outside this integration.

Fees use a 100,000 denominator, controlled by `feesOn()`. The unified implementation's `balancingFee()` returns zero. `collateralHealthFee()` delegates to the vault manager. Exact previews account for rounding upwards, including the fee-inclusive denominator; nominal fee metadata must not replace these quotes. Management and performance fees remain unknown rather than borrowing trading-fee revenue percentages, as required by the approved integration plan. The shared fee-mode registry and adapter both report externalised fees.

At block 49,030,926, fees were enabled, `collateralHealthFee()` returned 500 (0.5%), and `totalAssets()` returned 20,964,270,656,563 raw USDC units. Deposits had a 500,000,000-USDC cap. Post-withdrawal utilisation is checked after payout and fee transfer. The manager simulates the exact redemption and does not fall back from a failed fee-adjusted preview to a gross share-price quote.

## Risk and fee assessment, 4 November 2026

Technical risk is **Severe** (`VaultTechnicalRisk.severe`, numeric 40). This is
an assessment of contract/software transparency and administration, separate
from market risk from trader payouts. The
[technical risk framework](https://tradingstrategy.ai/blog/announcing-vault-technical-risk-framework-beta)
places lack of publicly followable core-contract development in its Severe
category. The original published matrix was inspected as well as its prose.

The official GitHub organisation's public repositories include SDKs, integrations
and an audits repository, but not a maintained `avantis-contracts` repository.
The current documentation's `Veranta-Labs` organisation link did not expose
public repositories either. Public audit reports (PDFs) establish review scope, but they are not public
source snapshots and do not establish ongoing public development history for
the core contracts. The audited Sherlock repository also returned HTTP 404;
verified implementation source is available through Basescan.
This transparency limitation, with mutable governance and upgrades, supports
Severe rather than Low or High. It is not evidence of an active exploit.

The [official audit index](https://docs.veranta.xyz/security/audits) exposes both
historical and newer reports. The August 2026 collaborative audit lists
`src/AvantisTranche.sol` and `src/VaultManager.sol`, audited commit
`8b292813082dac1b2d95416a713389652cd7a601` and final commit
`70f16f3e40d0032e075ae19ed9690be143d817ee`. This establishes published audit
scope, not an exact match of every deployed upgrade to that final commit.
The linked Zellic Trading Contracts report separately describes a v1.5 change
review, so it should not be treated as proof that every v2 deployment is covered.
The index still says the August 2026 v2 reports from Guardian, Sherlock and
Zellic will be added shortly, despite exposing attached PDFs. This mixed
publication state leaves complete audit coverage of the live generation unconfirmed.

Etherscan V2 confirmed verified proxy and implementation sources for the active
vault manager: `0xe9fB8C70aF1b99F2Baaa07Aa926FCf3d237348DD`, implementation
`0xf0207407beb82902186321e3dd84a7050add275a`. At block 53,497,176, vault
manager governance was `0x3775AF82c6C5705140944cD515fE899214Fb0288`. The
vault's ProxyAdmin, `0x2D898e46a20eBFc1424d4BBF69bacD92dc1ae8bb`, was
owned by the same Safe, whose owner threshold was three of five. The proxy,
Safe implementation and ProxyAdmin sources are verified. This does not establish
that Safe modules are absent or that every governance path requires owner signatures.

The main settlement dependency check at block 53,497,384 also confirmed
verified Etherscan proxy and implementation sources for:

- [TradingStorage](https://basescan.org/address/0x8a311D7048c35985aa31C131B9A13e03a5f7422d), implementation `0x7175518a822e04ecb8770343582e76f156d06c64`.
- [TradingRouter](https://basescan.org/address/0x44914408af82bC9983bbb330e3578E1105e11d4e), implementation `0x4a0d70ec00b708879530eeeaef2bb161551fdadc`.
- [Circle USDC](https://basescan.org/address/0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913), implementation `0x2ce6311ddae708829bc0784c967b7d77d19fd779` (`FiatTokenV2_2`).

This checks the principal identified custody and settlement contracts; it is
not an exhaustive security audit of every dynamically configured trading module.

The live diagnostic at block 53,497,070 confirmed:

| Field | Result |
| --- | --- |
| Fee mode | Externalised: withdrawal fees reduce the investor payout |
| Deposit fee | 0% |
| Nominal withdrawal fee | 1.5%; read dynamically, not a hardcoded constant |
| Management / performance / separate protocol fees | Unknown; no separately established investor fee rates |
| Withdrawal fee schedule | 0% to 2.5%, depending on the buffer ratio |
| Account admission | Permissionless for the reviewed implementation |
| Deposit / redemption | Synchronous / synchronous |
| Scheduled lockup | Zero; liquidity/utilisation restrictions still apply |
| Strategy tags | liquidity_provider, market_making, perpetual_futures |
| Share price | 1.400611225950628435280642594 USDC |
| TVL | 9,716,853.550470 USDC |
| Raw maxDeposit(address(0)) | 490,283,146,449,529 |
| Raw maxRedeem(address(0)) | 0; this is an account with no shares, not a global closure |
| Global close reasons / flags / maintained vault notes | None |
| Individual vault short / full description | None; protocol descriptions are supplied by metadata YAML |

The documented LP revenue split is 70% of opening, closing and win fees and
100% of margin fees. This upstream allocation is already reflected in pool
income; it must not be labelled a 30% investor performance fee. Management,
performance and separate protocol fee fields remain unknown as required by the
approved onboarding plan, rather than inventing zero rates from missing accessors.

No additional migration is needed for the risk-only assessment: the metrics
export resolves `vault_metadata.get("_risk") or get_vault_risk(protocol, address)`.
The maintained registry therefore updates ordinary rows during normal export;
manual `_risk` overrides remain untouched. Onboarding migration 030 remains
necessary for identity, fee and admission metadata. No production state was changed.

## Run after merge

The implementation does not run production maintenance. Migration `030-veranta-vault-metadata.py` requires one existing Base metadata row and ordinary `JSON_RPC_BASE` configuration. It changes protocol identity, fees, admission, synchronous capability, strategy tags and withdrawal timing. It preserves manual notes, descriptions and all historical files. No price backfill is required because this existing vault already has history.

From `~/vault-scanner/web3-ethereum-defi`, inspect `docker-compose.yml`, update to the merged revision and build the stamped image with `scripts/build-container.sh`. Preserve the existing persistent mounts. Stop persistent and competing writers before both preview and apply:

```shell
source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
source ../vault-rpc.env && docker compose run --rm --no-deps --entrypoint vault-migrate vault-scanner-oneshot list
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner
```

These commands operate on the entire pending numbered batch. Review that inventory before applying. The runner takes the lifecycle guard before the writer lock and uses the image's stamped git commit. Do not retag/rebuild the image during maintenance.

Expected for migration 030: one inspected row, only the declared metadata fields changed, no historical files or scan boundaries changed. A missing target, conflicting identity or unknown admission is a hard failure. Repeated transformation is idempotent and successful numbered migrations are skipped.

After restart, verify the running image's `GIT_VERSION_HASH.txt` matches the journal's commit. Normal scanner cleaning, exports and uploads publish the change; migration success alone does not. Check the public vault page shows Veranta, Veranta as protocol curator, Severe technical risk, the three strategy tags, permissionless admission, dynamic fees and synchronous deposit/redemption capability. The risk registry matches the Veranta protocol identity, so migration 030 or a normal rescan must establish that identity before export can publish the label. Verify the existing historical price range remains intact.

## Verification record

The guarded real-provider test command is:

```shell
source .local-test.env && timeout 180s poetry run pytest tests/erc_4626/vault_protocol/test_veranta.py -q --log-cli-level=info
```

Local manual run on 2026-11-04: passed all four cases against Base archive forks at blocks 49,030,926 and 53,496,246, using the supplied Goldsky/dRPC fallback configuration. This was a local run, not CI. No credentials were printed and no remote transactions were sent; all transaction mutations occurred on Anvil. The tests prove public factory selection, nominal fee scaling, guarded approval/deposit/full redemption, receipt analysis, net payout, zero remaining shares, guard redirection refusals, preflight capacity refusal and reconstruction of mined redemption analysis. The post-upgrade fixed fork is necessary because the current implementation does not exist at the canonical block; generic guard rejection checks remain on the canonical fork.

Public descriptions were reviewed against `.claude/docs/description-writing-style.md`. Original and processed logos were reviewed for provenance, dimensions, transparency and dark-background legibility.

A separate read-only current-state check on 2026-11-04 succeeded at Base block 53,496,246: permissionless admission, 0% deposit fee and a 1.5% nominal withdrawal fee. This differs from the 0.5% historical fork rate and confirms that the adapter must read the fee dynamically. No transaction was sent to Base.

Final focused verification on 2026-11-04: 150 passed, one optional live test skipped, in 19.84 seconds. The combined run included both Avantis fork generations, quote safety, admission, classification, strategy refresh, numbered migrations, runner preservation, metadata exports and shared admission tests. Ruff formatting, import/undefined-name checks and `git diff --check` passed.

Independent cleanup review: Claude CLI, Claude Opus 5.5, approved local handoff after the focused follow-up. Its remaining small suggestions were applied: explicit custom-error data assertions, decimal shares retained in rebuilt requests and corrected parametrised-fork docstrings. The 150-pass run above includes these fixes. Production migration and CI execution remain separate deployment work.

Risk/fee follow-up verification on 2026-11-04: 37 focused tests passed in 12.86 seconds, including both guarded fork generations and shared fee/risk tests. Ruff and `git diff --check` passed. The independent Claude Opus 5.5 review approved the Severe rating and no-additional-migration rationale, subject to replacing an unsupported public source-snapshot claim. That wording was corrected to public audit reports (PDFs); the audit-publication caveat, precise Safe ownership wording and Severe publication checklist were also added. No public descriptions or fee calculation code changed in this follow-up.


## Protocol curator attribution

Veranta is registered as a protocol curator with slug `veranta`. Its curator
profile aliases `protocols/veranta.yaml`, reusing the existing social feed and
original/formatted protocol artwork. Public copy describes the organisation
and its own USDC liquidity pool, with primary documentation links and the
Veranta rename context.

The reviewed pool is Base `0x944766f715b51967e56afde5f0aa76ceacc9e7f9`.
Its existing `liquidity_provider`, `market_making` and `perpetual_futures` tags
were rechecked and retained. The legacy senior contract is not newly classified
or added to adapter support by this curator change. The Veranta protocol
classification remains scoped to the unified pool.

The shared curator resolver now treats Veranta as protocol-managed and excludes
its name from cross-protocol fuzzy matching. Wrappers branded after Avantis or
avUSDC do not establish protocol curatorship. Curator attribution is computed
at export through `identify_curator()`, so no additional metadata migration is
required and explicit manual curator declarations remain authoritative.

This change creates no vaults or price history gaps. The previously reviewed
local price inventory starts at block 9,781,210 versus first seen at 9,780,531;
this small opening gap does not justify a new backfill for curator attribution.
Onboarding migration 030 and normal export/publication remain the deployment
steps for the existing integration. No production state was changed.

Curator follow-up verification on 2026-11-04: 106 focused curator, canonical-feed and Avantis unit tests passed in 4.65 seconds. The shared schema and inventory load the curator alias, and feed loading produces exactly one Avantis source: the existing protocol Twitter account, verantaxyz. Both formatted logos are reused. Ruff and `git diff --check` passed. Claude Opus 5.5 approved the curator delta; its two public-copy/publication clarifications were applied and the descriptions checked against the repository writing guide.

## Public description format review, 4 November 2026

Reviewed protocol, curator and fee copy against the description writing guide,
vault description formatting rules and the two onboarding skills. Summaries
are single-line plain sentences. Full descriptions retain Markdown paragraphs,
selective emphasis, inline sources and level-four headings where several topics
are covered. The protocol copy explains avUSDC shares, the reserve and payout
risk in plain language; the curator copy describes the organisation and the
absence of a disclosed independent curator. Homepage and documentation links
now use the current Veranta domain with Veranta as the public protocol and curator name.

The official LP and risk-management Markdown pages were fetched again. The
withdrawal schedule remains 0% to 2.5%; unconfirmed management and performance
fees are not described as zero. The full revenue allocation remains in the
research above: the concise public profile describes a share of trading fees
without adding each trading-fee category.

Claude Opus 5.5 approved the format and requested clearer wording about the
purpose of deposits. Both descriptions now say deposits back trading positions,
with the reserve-first payout order explained separately. Its inline rename
source and shorter unknown-fee caveat suggestions were also applied. Three
focused metadata export and YAML tests passed, and direct normalisation checks
confirmed that summaries contain no newlines and full descriptions preserve
their Markdown. These YAML descriptions are read at export; no additional
persisted-metadata repair or historical price backfill is required.

## Final cleanup review

Claude Opus 5.5 found no blocking correctness or money-movement defect. The
follow-up adds a real-contract metadata repair on both forks, links nominal
fees to observed fee-adjusted payouts, and includes requested share quantities
in redemption failures. Documentation links and a fixture docstring were
corrected, and the changelog now has one Avantis entry.

The official LP page above lists cryptocurrency, currency, commodity and index
markets, supporting the shorter public market description. Its prose and
fee-allocation table support the reserve-first explanation. The earlier public
writing-guide review applies to the unchanged descriptions.

Unreviewed upgrades intentionally produce unknown admission metadata; they do
not add a new execution interlock. This follows the shared admission handling
and the approved integration scope. Unknown management and performance fees
also mean investor net returns remain unavailable, rather than assuming zero.
Standard redemption has no minimum-payout protection, and the additional
post-upgrade fork currently relies on archive RPC without a committed seed.

Before building the image used for production preview, record the eventual PR
URL in migration 030. Use that same stamped image for preview and apply. No PR
exists during this local handoff, and none is created by cleanup. The pending
initial migration has not run in production. Its identity and fee repair, then
normal export/publication, remain the operational steps described above.

Before the review fixes, cleanup verification passed 127 tests with one optional skip in 51.43
seconds. All 18 changed Python files passed Ruff formatting. Targeted syntax,
undefined-name and import checks passed; the full default Ruff ruleset reports
style findings in this scope, including existing factory imports and rules
that conflict with repository conventions for naive UTC, positional overrides
and numbered migration filenames. Those broader lint policies were not changed.

After the review fixes, all 21 affected fork, unit and migration tests passed
in 55.39 seconds, including both new real-contract repair cases. Claude Opus
5.5 approved the focused follow-up pending that successful run. Its remaining
documentation timing correction was applied: record PR provenance before
building the preview image. Ruff formatting, targeted syntax/import checks and
`git diff --check` passed. No Sphinx build or production maintenance was run.

PR opened on 2026-11-04: [#1725](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1725).
The migration header and changelog now record this provenance before any
production preview image is built. Earlier references to no PR describe the
local implementation and cleanup stage.

## Canonical Veranta identifiers

The user superseded both the Avantis display-name and stable-slug decisions on
2026-11-04. Protocol and curator names use Veranta, with “formerly known as
Avantis” in both public profiles. The canonical slug is `veranta`, including
feed IDs, metadata and logo directories, ABI storage, adapter modules/classes,
feature flags, repair helpers, tests, documentation and public URLs. Older
Avantis protocol-name input resolves to the current slug, fee mode and risk.
The public vault slug is `veranta-usdc-vault`; fresh exports generate it from
the current protocol identity while preserving the actual onchain token name.
Migration 030 refreshes protocol identity for the existing row; each normal
export then derives the vault slug. The export-only vault slug must not be
persisted by the repair because the shared exporter treats pre-slugged rows
as an already processed collection.
Verified ABI contents, original image bytes, source repository URLs and onchain
token names retain their factual original values. The existing PR branch remains
unchanged.

Migration `030-veranta-vault-metadata.py` is still pending initial onboarding
and has not run in production. Its repair writes Veranta and the `veranta`
slug, accepting both legacy and current display names on idempotent reruns.
Renaming this unshipped initial migration needs no additional production
migration or historical backfill. Earlier review records describe prior local
stages and are superseded by this naming decision.

Identifier-rename verification: the renamed focused suite passed 131 tests
with one optional skip in 74.91 seconds, including both real guarded fork
generations, feed/curator exports, migration discovery and history preservation.
The public-vault slug and repair follow-up passed 18 tests. After removing an
unsafe attempt to persist the export-only vault slug and strengthening the
current-brand curator exclusion cases, all 20 affected checks passed in 2.72
seconds. The verified ABI, SVG and both PNGs are byte-identical to their
pre-rename versions. Ruff formatting, targeted import/error checks and
`git diff --check` passed. Public description wording retains its earlier
writing-guide review.

The website must handle redirects from the previously published
`avantis-usdc-vault` URL and old protocol/curator URLs during publication. That
website route configuration is outside this repository; no production routes
or storage were changed here. No pre-rename branch image has been used to
write production metadata, so no persisted `avantis_like` enum needs repair.

Claude Opus 5.5 approved the identifier-rename follow-up with no remaining code
or test defects. Its requested old-URL rollout limitation is recorded above and
must accompany the next PR-description update before publication.

## Public description source links

On 2026-11-04, the long description gained inline canonical links for
[position sizing](https://docs.veranta.xyz/trading/getting-started/collateral-and-position-sizing),
[trading limits](https://docs.veranta.xyz/trading/limitations-and-safeguards) and
[withdrawal charges](https://docs.veranta.xyz/liquidity-providers/risk-management).
The existing [avUSDC documentation](https://docs.veranta.xyz/liquidity-providers/veranta-lp-vault-avusdc)
explains the pool's trading counterparty role, reserve and fee income.

The [11 March 2026 fee allocation announcement](https://www.veranta.xyz/blog/systematic-avnt-buybacks-and-burns)
and the vault documentation's allocation table agree: LPs receive 70% of
opening, closing and win fees, and 100% of margin fees. The remaining 30% of
opening, closing and win fees funds AVNT buybacks and burns. The documentation's
introductory claim of 100% of all trading fees conflicts with its own table;
the public description uses the table and dated announcement instead.

The [14 September 2026 rename announcement](https://www.veranta.xyz/blog/introducing-veranta)
replaces the SDK README in the protocol long description as the direct source
for the name change. Both official
announcement pages and the four documentation Markdown endpoints returned
HTTP 200. These source and copy updates do not change persisted accessors or
require another migration; normal metadata export publishes the description.

This supersedes the earlier decision to omit individual trading-fee categories
from the public profile. The description now makes the reduction from 100% to
70% explicit and distinguishes the announced conditional proposal for a larger
buyback allocation from the implemented split. Percentages apply to fees
actually collected; the copy does not claim every market charges every fee.

The description passed the writing-guide check and focused metadata-export
test. Independent Claude Opus 5.5 review approved it with recommended edits;
the fee-share history, conditional proposal and research-scope clarifications
above address those recommendations. `git diff --check` passed. No Python
files changed in this copy update, and no PR or production state was updated.

The user's subsequent writing-guide correction on 2026-11-04 supersedes the
historical framing above: public copy now lists current fee allocations in
bullets, with depositors first and the announcement linked inline. Superseded
rates and the conditional buyback roadmap remain research context rather than
public description content. The former name remains linked in the opening
sentence without a separate historical section. The guide now makes this
current-terms-first approach explicit while retaining material loss history
and scheduled changes when relevant to depositor decisions.

For research context, LPs received 100% of trading fees excluding liquidation
fees from the October 2025 launch until 11 March 2026. The announcement's
conditional, unscheduled Milestone 2 targets a buyback allocation of 50% or
more; it is not the current allocation.

Claude Opus 5.5 approved the current-terms rewrite with no blocking defects.
Its recommended terminology correction was applied consistently to both the
guide and description: use fees on profitable trades rather than claiming a
profit-based calculation. The metadata-export test and `git diff --check`
passed. This review supersedes the earlier copy review for the revised text.

## Individual vault description repair, 5 November 2026

The merged PR's long description was published as protocol metadata. The
protocol page and public protocol JSON contained the reviewed copy, but the
individual vault had no "About the vault" because `VerantaVault` inherited
empty description accessors. The original export test covered protocol YAML
and artwork rather than the individual vault's scanner-to-JSON text path.

The adapter now uses handwritten copy scoped to the confirmed Base address.
The short summary is plain text; the full description explains current fee
income, reserve-first payouts, capital losses and withdrawal limits. It omits
the protocol introduction and historical fee comparison. The official LP
documentation and withdrawal-rules Markdown endpoints returned HTTP 200 on
2026-11-05; their fee table still gives 70% of opening, closing and win fees
and 100% of margin fees to depositors, and withdrawal charges of 0% to 2.5%.
The LP page's contradictory introductory 100% claim is not used. Current exit
availability is not established by the copy or this metadata-only repair.

Migration 031 changes at most two fields on one existing row and performs no
RPC reads, backfill or history reset. Migration 030 is unchanged. The production
preview/apply and publication reminder is in
`docs/README-vault-description-formatting.md#veranta-individual-vault-description`.
No production changes are part of this local implementation.

The LP documentation also permits routing idle capital into other investments;
it does not establish current allocations. The vault copy discloses that
possibility and the remaining uncertainty instead of assuming all assets are
used solely for trading. No current routing or asset allocation is asserted.
The full fee breakdown includes liquidation fees retained by the treasury.

Independent Claude Opus 5.5 review found no code or migration-safety defect.
Its requested disclosure of possible external investments and correction of
PR provenance timing were applied: insert the PR URL on the feature branch
before merge, not afterwards during the production build. Known market types,
plain-language withdrawal wording and migration reporting assertions were also
clarified. The existing production source and independent branding descriptions
remain unchanged.

Final local verification on 2026-11-05 passed 49 tests with one optional skip:
Veranta unit coverage, both numbered Veranta repairs, migration batch coverage,
shared scanner fields and the scanner-to-individual-vault JSON regression.
All six changed Python files passed Ruff formatting and targeted syntax/import
checks; `git diff --check` passed. The writing-guide check and independent
Claude Opus 5.5 follow-up approved the revised copy with no blocking issues.
The future PR URL must still replace migration 031's placeholder before merge.
No push, PR creation, Sphinx build or production maintenance was performed.
