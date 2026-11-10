# Lagoon curator onboarding, 10 November 2026

Branch: `add-lagoon-curator-records`.

The preceding audit compared 34 curator identities from Lagoon's public curator
API, the current 58-vault catalogue and the known-vault detail inventory with
298 existing curator profiles. It found four real missing identities and one
test identity, which remains excluded. This change adds the four profiles and
reviews the eight incomplete existing records and corrects DeTrade's related current-product description. It does not approve additional
vaults for listing or change primary co-curator ordering. The official Murmurr
product book also identifies five Morpho lending sleeves, all covered below.

## New identities and sources

| Curator | Identity evidence | News sources | Logo |
| --- | --- | --- | --- |
| Murmurr | [Official website](https://murmurr.xyz/) and its Morpho lending product | No verified social or RSS link on the website or blog; omitted | Official [brand-kit](https://murmurr.xyz/brand) symbol |
| VOLTA90 | [Official website](https://volta90.trade/) and [migration announcement](https://volta90.trade/blog/volta90-ethereum-migration) naming both contracts | `volta90x`, linked from website | Official [brand-kit](https://volta90.trade/brand) symbol |
| NAVI Protocol | [Official documentation](https://docs.naviprotocol.io/) and exact Lagoon curator declaration | No independently verified social/RSS link added | Official Lagoon curator CDN image |
| Stake Capital | [Official website](https://www.stake.capital/) and exact Lagoon co-curator declaration | `stakecapital` and LinkedIn `stake-capital`, linked from website | Website vector favicon |

Exact-address Lagoon requests were repeated successfully on 10 November 2026
for the five initially associated Lagoon contracts and the additional closed
Ethereum VOLTA90 deployment below. Murmurr, NAVI and current VOLTA90 name
the curator explicitly; lvlUSD names Gami and Stake Capital. Historical VOLTA90
is identified through the operator's migration announcement, rather than inferred
from an empty API curator field.

NAVI's homepage and My Seed Advisor's existing homepage returned HTTP 403.
The Chrome/browser fallback is not available in this environment. NAVI's
accessible official docs, app and exact-address metadata establish its identity;
My Seed Advisor's unverified social sources remain unset. No disabled-source
markers were cleared or fabricated.

Original logo provenance is recorded beside each downloaded asset under
`eth_defi/data/vaults/original_logos/`. The existing post-processing script produced
four visually inspected 256×256 RGBA generic PNGs. Murmurr's symbol is green,
VOLTA90's is yellow/black, Stake Capital's is black/grey on white, and NAVI's is
teal. Logos were not synthesised. Nara's exact curator object has no logo URL;
no unrelated brand asset was substituted.

## Existing records reviewed

| Record | Result |
| --- | --- |
| DeTrade | Refresh its current Core product description, remove stale AUM, and distinguish the USDC portfolios currently presented by Murmurr |
| 722 Capital | Add LinkedIn `722-capital`; its [company profile](https://www.linkedin.com/company/722-capital/) links the official domain |
| MoneyFi | Add Twitter `MoneyFiHub`, linked by its [official documentation](https://moneyfi-1.gitbook.io/docs), and LinkedIn `moneyfihub`, whose [company page](https://www.linkedin.com/company/moneyfihub) links the official domain |
| Rho Labs / Rho X | Add `Rho_Labs`, linked from the [official website](https://rho.trading/) |
| TruMarket | Add `trumarket_tech` and LinkedIn `tru-market`, linked from the [official website](https://www.trumarket.tech/) |
| Hub Capital | Reviewed; no independent website or social identity verified. Existing source-bearing profile retained with a dated gap comment |
| Nara Capital | Reviewed; exact GUSDC curator object has empty website, logo and about fields. Existing explicit limitations retained with a dated gap comment |
| My Seed Advisor | Reviewed; no social handle guessed after the homepage 403. Correct the maintained vault destination to its published Trading Strategy page, verified by chain/address |
| Sigma Labs | Reviewed; website reachable but no verified social/news links found. Record the gap without matching unrelated Sigma Labs companies |

722 Capital and MoneyFi LinkedIn identities were verified through their
company profiles and matching official domains, not through homepage footers.
No RSS endpoint was added without a valid, attributable feed. Configuring a
social source does not certify that a collector has successfully fetched posts;
normal feed collection and its source checks still apply.

## Attribution and strategy coverage

Exact chain/address overrides take precedence over stale names, manager fields
and declared curator slugs. Murmurr's deployment was labelled DeTrade Morpho
X-Chain USDC in the earlier published export; the current API and Murmurr site
identify Murmurr. This change corrects the current attribution and does not
claim Murmurr operated the entire historical price series.

| Vault | Chain | Address | Curator outcome | Maintained strategy tags |
| --- | --- | --- | --- | --- |
| Murmurr USDC One | 1 | `0x7d2c2f54792ad72cb834d298f542145b06b703cb` | Murmurr | algorithmic_trading, discretionary_trading, lending, lending_optimisation |
| USDC High Yield | 1 | `0x47491305479635357df29fe0cb6f1af10e51d4da` | NAVI Protocol | Missing (`None`); no strategy text in the exact vault response |
| VOLTA90 current shares | 1 | `0x401550bc1ce4fb0ffdd86015f49142bbaad29ad8` | VOLTA90 | algorithmic_trading, directional_trading, discretionary_trading, perpetual_futures |
| VOLTA90 additional closed Ethereum deployment | 1 | `0x10dbaf26e61db145c9af764fcd3bdd3993e3ce8a` | VOLTA90 operator association from shared roles | Missing (`None`); strategy and closure details undisclosed |
| VOLTA90 closed shares | 42161 | `0x9e6de1f581d63129af453fe1ea7253b280b4dca8` | Historical VOLTA90 | Same supported trading-book classifications; closure remains unchanged |
| Gami lvlUSD | 1 | `0x59b7942f7d2afd085691ce65c152e0d38d4eff22` | Gami remains primary; Stake Capital gains its own profile | lending, liquidity_provider, multistrategy |

The strategy skill was applied to every newly covered deployment. The original onboarding left strategy tags unchanged. The later human-selection
review below adds the missing discretionary classifications.
NAVI's broader Sui lending business does not establish the investment mechanism
of its undocumented Ethereum vault. Stake Capital's addition must not replace
Gami in the single-primary-curator export. Regime Vault/ark2042 and Fundatio are
outside these four verified API curator identities and are not onboarded here.

## Morpho sleeves and unsupported duplicate names

Murmurr's current product book links five exact lending sleeves. All receive
reviewed chain/address curator attribution, including earlier DeTrade-branded names.
Morpho V1 and V2 adapters supply `lending` automatically. The original review
recorded only that default; the later review below adds supported classifications
for the three diversified sleeves.

| Chain | Address | Original strategy tags |
| --- | --- | --- |
| 1 | `0x845339546d90e6e21fdb9638cc7d8d15ebb5e4ac` | lending |
| 1 | `0x29ca54dd3ae9e43abb10880f5e08da439bf25f99` | lending |
| 8453 | `0xcda5dbf58a016158ecd4a226109752cc3a627ec8` | lending |
| 143 | `0x9f209b43559b13b783eaa0a6686921c7b174f269` | lending |
| 999 | `0x3f78bdd48b363ec927dc227d6c80a6e4821c7e10` | lending |

The additional closed Ethereum contract
`0x10dbaf26e61db145c9af764fcd3bdd3993e3ce8a` supplies no curator or strategy text,
but its exact API roles share owner, Safe and valuation manager
`0xe1f52c958bf1048b48c8707fb6c1c4da64bfbfbc`, plus fee receiver
`0xcdbe2de60419398718981276863cb360f1f781ae`, with the current Ethereum product.
It therefore receives an explicit VOLTA90 operator association, rather than
being rejected as a copied name. Its strategy remains missing and its listing
status is not changed. The API shows zero total assets and price per share,
non-zero share supply and a -100% inception performance rate. These numbers
are recorded as disclosure evidence; they do not establish whether investors
lost funds, redeemed elsewhere or migrated. No incident classification is
invented from a closed deployment's zero valuation.

VOLTA90 remains in the existing metadata-only curator set: names alone cannot
identify arbitrary other contracts. The known contracts and exact upstream
manager names resolve normally. Price histories are never spliced.

## DeTrade and Murmurr identity boundary

The [current DeTrade site](https://detrade.fund/) presents the three Core products
on Base. Its [terms](https://detrade.fund/terms) identify DeTrade Fund in Panama,
while [Murmurr's terms](https://murmurr.xyz/legal) name Murmurr Labs LLC in
Saint Vincent and the Grenadines. These are separately presented brands and
contracting identities; this is not evidence that one entire organisation was
renamed. No corporate merger or management-handover date was verified.

Murmurr's current official product links identify USDC One and all five Morpho
sleeves, including contracts with older DeTrade Serenity USDC names. Only those
exact products change current attribution. Other DeTrade products remain with
DeTrade. Both profiles now explain the legacy branding without attributing all
historic results to the present manager. The research keeps the earlier names
and price boundaries visible. The Morpho override tests use the competing
DeTrade title rather than only an unbranded placeholder.

NAVI's curator-level description specifically presents its Ethereum vaults as
access to Sui ecosystem yield without users bridging assets or holding a Sui
wallet. This product objective was considered and included as an aim, but it
does not identify allocations or trade mechanics for the exact Ethereum vault,
which has no individual strategy text and remains untagged. The current
VOLTA90 site also lists Morpho among counterparties without enough information
to establish a particular lending allocation; no extra lending tag was inferred.

## Persistence and historical price gaps

Curator attribution itself requires no numbered migration: identity is resolved during
`export_lifetime_row()` through the current `identify_curator()` resolver,
including the new exact overrides before stale declarations. Feeder metadata
and logos are loaded directly for curator exports. The original attribution change did not rewrite persisted accessor or
strategy-tag fields. The subsequent description work below adds migration 061
for the two stored text fields only. Deployment still needs normal
cleaning/export/upload and curator/feed publication; this work does not perform
production writes.

The available local metadata and hourly price snapshots end in October 2026.
They are not proof of current production gaps. Comparing their detection
first-seen dates with their first price observations produced:

| Deployment | Detection first seen | First local price | Assessment |
| --- | --- | --- | --- |
| Murmurr / earlier DeTrade-labelled contract | 12 February 2026 | 7 March 2026 | About 23 days absent in this snapshot |
| NAVI USDC High Yield | 16 October 2026 | 22 October 2026 | About six days absent |
| VOLTA90 Ethereum | 9 September 2026 | 14 September 2026 | About five days absent |
| VOLTA90 Arbitrum | 12 April 2026 | 19 April 2026 | About seven days absent |
| Gami lvlUSD | 6 May 2025 | 6 May 2025 | No material initial gap |
| VOLTA90 additional closed Ethereum contract | 4 September 2026 | No rows | No local price history; investigate closure and zero valuation before proposing a reconstruction |
| Murmurr Morpho Ethereum `0x8453…e4ac` | 19 August 2026 | 1 September 2026 | About 13 days absent |
| Murmurr Morpho Ethereum `0x29ca…5f99` | 3 February 2026 | 25 February 2026 | About 21 days absent |
| Murmurr Morpho Base `0xcda5…7ec8` | 18 January 2026 | 8 February 2026 | About 21 days absent |
| Murmurr Morpho Monad `0x9f20…f269` | 11 August 2026 | 25 August 2026 | About 14 days absent; historical state may be unavailable |
| Murmurr Morpho HyperEVM `0x3f78…7e10` | 11 August 2026 | 24 August 2026 | About 13 days absent |

If those gaps remain in production, propose a separate fixed-scope numbered
price backfill for the nine recorded initial gaps only, using their reviewed
first-seen blocks and current first-price boundaries. Monad cannot serve arbitrary
historical state: preserve existing rows and backfill only within the current
provider-readable window; the August gap may be unrecoverable. Verify the Murmurr operator
handover before attributing earlier results to its present curator, and preserve
the separate VOLTA90 share histories across migration. These already-known
vaults gain no new scan eligibility here, and this metadata-only task does not
start a funded historical scan or alter reader state.

The additional closed VOLTA90 Ethereum deployment has no local price rows. It
needs a separate production-state and closure investigation before defining a
historical scan; missing rows must not be filled with invented prices or treated
as proof of an investor loss.

## Original onboarding checks

The focused curator, canonical-feeder and onboarding suite passed **118 tests**.
All **302 curator YAML records** passed the shared schema loader. Both modified
Python files passed Ruff formatting and lint checks, and `git diff --check`
passed. The four generic logo PNGs were inspected visually. Public descriptions
were checked against the shared writing style guide; no unknown optional feed
source was guessed, and no existing failure marker was cleared.

Independent grounded Claude CLI review with `claude-opus-5-5` completed, followed
by a focused review of attribution and copy corrections. Final follow-up verdict:
**ready for PR handoff; no blockers**. Remaining low-severity documentation and
wording points were corrected. No production writes, feed collection, price
backfill, PR creation or push were performed during authoring.


## Description style review and manual vault overrides

The user confirmed scope as the **13 curator records in this branch**, rather
than the entire 302-record registry. All 13 profiles were reviewed against the
writing guide. The vault inventory includes **29 Lagoon deployments and five
Morpho portfolios**. All 29 exact Lagoon API reads succeeded on 10 November
2026; name-matching the earlier 641-deployment inventory found no further
associated Lagoon rows. Murmurr's official product book identifies the five
Morpho portfolios. 722 Capital's headline count does not identify additional
verifiable contract addresses.

### Empty descriptions preserve missing coverage

The user's later style correction supersedes the earlier placeholder approach:
**25 vaults receive useful descriptions; nine retain empty short and long fields.**
A statement that no strategy is disclosed is not a description. Curator-level
objectives do not establish the individual NAVI vault's strategy. The additional
VOLTA90 deployment's status and unexplained zero valuation likewise do not
supply useful investment copy. Those facts remain in this research note.

The nine deployments have no handwritten entry, rather than an empty override
that would mask genuine future API text. Their current provider fields are empty;
normal provider fallback remains available. They are excluded from migration 061,
which has not been merged or applied in production. No previous successful
migration was edited. This change does not erase existing provider descriptions.

Every remaining long description was reviewed to remove repetitive checklists of
missing approval rules or cadence. Material gaps beside supported mechanisms
remain, such as unidentified trading venues or an undisclosed currency hedge.
The known Arbitrum VOLTA90 strategy remains in the past tense without saying
“closed vault”. The documented share migration and value of legacy shares remain
because those facts add information beyond the website's status badge.

Primary evidence is preserved in the remaining descriptions' inline links,
the exact-address Lagoon API inventory, the DeTrade product cards, Murmurr's
product book and the VOLTA90 migration announcement. DeTrade's current product
cards describe its Core products more specifically than the older API copy.
MSA's ambiguous French cadence wording is not converted to a guessed schedule.
The earlier interface https://app.722.capital/v/722-usdc remains research
provenance; the public destination uses the verified Trading Strategy page.

The first PR comment retains all 34 inventory rows, with genuinely blank cells
for the nine undescribed vaults. It is generated from the current registry, with
no missing-information placeholder substituted into an empty field.

### Reviewed inventory

All linked Trading Strategy pages below matched the chain and contract in the
published export and returned HTTP 200 with the address present. Missing public
destinations remain unlinked; no slug was guessed.

| Curator | Chain | Address | Vault | Public destination |
| --- | --- | --- | --- | --- |
| VOLTA90 (historical operator) | 1 | `0x10dbaf26e61db145c9af764fcd3bdd3993e3ce8a` | VOLTA90 MULTI ASSET STRATEGY | Not verified; left unlinked |
| σ-Labs, Ellen Capital | 1 | `0x2746f31096f23670caf4043f8b30d8d02405a257` | stETH Redemption Carry | Not verified; left unlinked |
| VOLTA90 | 1 | `0x401550bc1ce4fb0ffdd86015f49142bbaad29ad8` | VOLTA90 MULTI ASSET STRATEGY | [Vault page](https://tradingstrategy.ai/vaults/volta90-multi-asset-strategy-2) |
| NAVI Protocol | 1 | `0x47491305479635357df29fe0cb6f1af10e51d4da` | USDC High Yield | [Vault page](https://tradingstrategy.ai/vaults/usdc-high-yield) |
| Rho X | 1 | `0x4cb280e63251b9ab24a54def74bf5995d82ff398` | Rho X Liquidity Provider | [Vault page](https://tradingstrategy.ai/vaults/rho-x-liquidity-provider) |
| Gami, Stake Capital | 1 | `0x59b7942f7d2afd085691ce65c152e0d38d4eff22` | Gami lvlUSD | [Vault page](https://tradingstrategy.ai/vaults/gami-capital-lvlusd) |
| 722 Capital | 1 | `0x66dcb62da5430d800a1c807822c25be17138fda8` | Unity Trust | [Vault page](https://tradingstrategy.ai/vaults/unity-trust) |
| 722 Capital | 1 | `0x71652d4898de9a7dd35e472a5fe4577ec69d82f2` | Trinity Trust | [Vault page](https://tradingstrategy.ai/vaults/trinity-trust) |
| 722 Capital | 1 | `0x7d798ce99ee2adc183a865d76b112810d5395ea2` | 722 Capital USDC | [Vault page](https://tradingstrategy.ai/vaults/722-capital-usdc) |
| Murmurr | 1 | `0x7d2c2f54792ad72cb834d298f542145b06b703cb` | Murmurr USDC One | [Vault page](https://tradingstrategy.ai/vaults/detrade-morpho-x-chain-usdc) |
| σ-Labs, Ellen Capital | 1 | `0x8595f5ee036e1f7c4f3ca66327ba5f38c21cb7fb` | Stablecoin Market Making | [Vault page](https://tradingstrategy.ai/vaults/stablecoin-market-making) |
| Hub Capital | 1 | `0x9a458370556a68bdc130a80c35b294d0ad947a6a` | Hub Capital crvUSD Vault | [Vault page](https://tradingstrategy.ai/vaults/hub-capital-crvusd-vault) |
| Hub Capital | 1 | `0xca790385506b790554571cbc9da73f0130cdcfd5` | Hub Capital USDC vault | [Vault page](https://tradingstrategy.ai/vaults/hub-capital-usdc-vault) |
| σ-Labs, Ellen Capital | 1 | `0xf02030ab0d7385ce4cc2f7f64b7b44430fb44c89` | Cross-chain USDC Lending | [Vault page](https://tradingstrategy.ai/vaults/cross-chain-usdc-lending) |
| DeTrade | 143 | `0xe95074a86fead8647edff36af2f3f1dd7e8f7adb` | DeTrade Core AUSD | [Vault page](https://tradingstrategy.ai/vaults/detrade-core-ausd) |
| 722 Capital | 999 | `0x7dd73a986f6188da1baaf68541fd6ebd455abd1d` | 722 Capital Hype | Not verified; left unlinked |
| DeTrade | 8453 | `0x0a63471da694fc822f5a0a0b65978dda8c8edbe5` | DeTrade Applefarm USDC | [Vault page](https://tradingstrategy.ai/vaults/detrade-applefarm-usdc-2) |
| MoneyFi | 8453 | `0x4efc07dca8697792119484af33549f33ab11bf3c` | MoneyFi FlowForge Vault | [Vault page](https://tradingstrategy.ai/vaults/moneyfi-flowforge-vault) |
| DeTrade | 8453 | `0x8092ca384d44260ea4feaf7457b629b8dc6f88f0` | DeTrade Core USDC | [Vault page](https://tradingstrategy.ai/vaults/detrade-core-usdc) |
| DeTrade | 8453 | `0x9b97bfdfe44d1b113ecd4bf2f243ed36aca34523` | DeTrade Core ETH | Not verified; left unlinked |
| 722 Capital | 8453 | `0xb09f761cb13baca8ec087ac476647361b6314f98` | 722Capital-USDC | [Vault page](https://tradingstrategy.ai/vaults/722capital-usdc) |
| MSA, Gami | 8453 | `0xb725f2277f3f62ad017a6dc418433b326a9f6334` | MSA - Portefeuille Défensif | [Vault page](https://tradingstrategy.ai/vaults/msa-portefeuille-defensif) |
| TruMarket | 8453 | `0xbe7db44f4ce20dac83b578b94fd35087f66e9754` | TruMarket | [Vault page](https://tradingstrategy.ai/vaults/trumarket-2) |
| DeTrade | 8453 | `0xd4401d8bea82e4e6c40bb26ae3a04d2fb7ca4550` | DeTrade Core EURC | [Vault page](https://tradingstrategy.ai/vaults/detrade-core-eurc) |
| Nara Capital | 8453 | `0xd5c22fa3f7ee979ed7c28e36669b29797ab277e4` | GUSDC | [Vault page](https://tradingstrategy.ai/vaults/gusdc) |
| MSA, Gami | 8453 | `0xf56bfe07b8d6e6d74258cdb6969a633629b06b08` | MSA - Portefeuille Dynamique | [Vault page](https://tradingstrategy.ai/vaults/msa-portefeuille-dynamique) |
| 722 Capital | 8453 | `0xfce2064b4221c54651b21c868064a23695e78f09` | 722Capital-ETH | Not verified; left unlinked |
| 722 Capital | 42161 | `0x99cd0b8b32b15922f0754fddc21323b5278c5261` | Yield Algo Trading | [Vault page](https://tradingstrategy.ai/vaults/yield-algo-trading) |
| VOLTA90 (historical operator) | 42161 | `0x9e6de1f581d63129af453fe1ea7253b280b4dca8` | Volta90 Multi-asset trading strategy | [Vault page](https://tradingstrategy.ai/vaults/volta90-multi-asset-trading-strategy) |
| Murmurr | 1 | `0x845339546d90e6e21fdb9638cc7d8d15ebb5e4ac` | Murmurr USDC Blue-Chip | Not verified; left unlinked |
| Murmurr | 1 | `0x29ca54dd3ae9e43abb10880f5e08da439bf25f99` | Murmurr USDC Frontier | Not verified; left unlinked |
| Murmurr | 8453 | `0xcda5dbf58a016158ecd4a226109752cc3a627ec8` | Murmurr USDC cbAssets | Not verified; left unlinked |
| Murmurr | 143 | `0x9f209b43559b13b783eaa0a6686921c7b174f269` | Murmurr USDC aHYPER | Not verified; left unlinked |
| Murmurr | 999 | `0x3f78bdd48b363ec927dc227d6c80a6e4821c7e10` | Murmurr USDC WHYPE | Not verified; left unlinked |


### Vaults without descriptions

| Curator | Chain | Vault | Address |
| --- | --- | --- | --- |
| VOLTA90 | 1 | VOLTA90 MULTI ASSET STRATEGY | `0x10dbaf26e61db145c9af764fcd3bdd3993e3ce8a` |
| NAVI Protocol | 1 | USDC High Yield | `0x47491305479635357df29fe0cb6f1af10e51d4da` |
| 722 Capital | 1 | Unity Trust | `0x66dcb62da5430d800a1c807822c25be17138fda8` |
| 722 Capital | 1 | Trinity Trust | `0x71652d4898de9a7dd35e472a5fe4577ec69d82f2` |
| 722 Capital | 1 | 722 Capital USDC | `0x7d798ce99ee2adc183a865d76b112810d5395ea2` |
| σ-Labs, Ellen Capital | 1 | Stablecoin Market Making | `0x8595f5ee036e1f7c4f3ca66327ba5f38c21cb7fb` |
| 722 Capital | 999 | 722 Capital Hype | `0x7dd73a986f6188da1baaf68541fd6ebd455abd1d` |
| 722 Capital | 8453 | 722Capital-ETH | `0xfce2064b4221c54651b21c868064a23695e78f09` |
| 722 Capital | 42161 | Yield Algo Trading | `0x99cd0b8b32b15922f0754fddc21323b5278c5261` |


### Publishing the description refresh

Run migration `061-lagoon-curator-descriptions.py` after merge: at most **25
existing rows and 50 fields**, limited to `_description` and `_short_description`.
It reports missing rows, rejects conflicting protocol labels before staging,
and performs no network reads, discovery or historical price scanning. The
nine undescribed rows are untouched. Existing price-gap proposals above remain
separate; no scan eligibility or historical coverage is added here.

Follow the migration docstring and `.claude/docs/migration-scripts.md`: inspect
Compose mounts, build the stamped merged image, stop the scanner and competing
writers before both preview and apply, review the whole pending batch and restart
the same image. The lifetime guard precedes the writer lock. Normal exports and
uploads publish the description changes; this work performs no production writes.

### Checks after the writing-guide correction

The final focused suite passed **188 tests** on 10 November 2026, including the
live public Lagoon API check. All 302 curator YAML records load. Coverage now
includes both `None` and empty-string provider values for all nine undescribed
vaults, subsequent genuine provider copy, empty scanner/individual JSON fields,
and preservation of non-empty provider text on excluded migration control rows.
Ruff formatting and lint checks passed for the changed Python files, together
with `git diff --check`.

Claude CLI with `claude-opus-5-5` completed the focused independent review with
**ready, no blockers**. Its suggested test improvements for empty strings and
non-empty preservation sentinels were added before the final 188-test run.

```shell
source .local-test.env && PYTHONPATH="$PWD" timeout 180s poetry run pytest tests/vault/test_lagoon_curator_descriptions.py tests/erc_4626/vault_protocol/test_lagoon_descriptions.py tests/vault/test_lagoon_curator_onboarding.py tests/vault/test_curator.py tests/feed/test_canonical_feeder.py -q --tb=short
```


## Human investment selection review

All **34 deployments belonging to the 13 reviewed curators** were checked again
on 10 November 2026. All 29 exact Lagoon API requests succeeded. The change adds
`discretionary_trading` to nine vaults, retaining their supported income mechanisms.
The three diversified Morpho sleeves also gain `algorithmic_trading` and
`lending_optimisation`. The other 25 deployments keep their prior classifications.
No tags are removed from the reviewed deployments.

The canonical identifier remains `discretionary_trading`. It covers people
choosing yield investments and credit deals, not only directional trading.
Automation and discretion can coexist. A curator identity, privileged contract
role or permissioned deposits does not by itself establish human selection.

The exact lvlUSD API mandate establishes Gami/Stake co-curation, but
[Gami's team-led curation and rebalancing](https://www.gami.capital/on-chain-strategies/)
is described only at the curator level. Without a product-specific link to human
selection, lvlUSD retains its existing tags. The independent review caught this
evidence boundary before handoff.
The Core product cards and opportunity-selection policy on DeTrade's current site
support its three Base products, not a blanket classification of the Monad AUSD
or Applefarm deployments. 722's Base USDC API mandate describes active positioning;
[722's current strategy](https://722.capital/) describes conviction-led farm rotation.
TruMarket's exact API mandate explicitly assigns sourcing, structuring and
monitoring agricultural deals to its team.

Murmurr's current homepage explicitly retains curator expertise in market
selection. Its [allocator documentation](https://docs.murmurr.xyz/how-it-works/allocator)
identifies Blue-Chip, Frontier and cbAssets as diversified sleeves with market
ranking; the Lagoon product invests through those sleeves. These four products
therefore combine human selection and algorithmic allocation. The Lagoon product
also includes fixed-universe mono sleeves; its published history of sleeve targets
shows changes to the book-level investment policy. The
[hard rules](https://docs.murmurr.xyz/how-it-works/hard-rules) distinguish the mono
WHYPE and aHYPER products: their fixed universe and automated emergency controls
do not establish discretionary investment selection. Their lending default stays.

Empty Lagoon description fields do not automatically invalidate independently
researched tags: Hub USDC and 722 Hype have composition-based evidence in their
existing source comments, while 722 ETH has an explicit upstream market-neutral
classification. None receives a discretionary tag solely from those observations.
Other unchanged products disclose mechanisms or allocation rules without enough
evidence of human choices; the review does not infer manual control from silence.

### Complete tag decisions

`D` = `discretionary_trading`; `A` = `algorithmic_trading`;
`L` = `lending_optimisation`. “Missing” describes an internal research gap, not
public description text. Existing VOLTA90 and MSA discretionary tags are retained.

| Curator | Vault | Chain:address | Tags added | Decision |
| --- | --- | --- | --- | --- |
| VOLTA90 | VOLTA90 MULTI ASSET STRATEGY | `1:0x10dbaf26e61db145c9af764fcd3bdd3993e3ce8a` | — | Missing product-specific decision evidence; unchanged |
| σ-Labs, Ellen Capital | stETH Redemption Carry | `1:0x2746f31096f23670caf4043f8b30d8d02405a257` | — | Mechanism supported; human selection not established |
| VOLTA90 | VOLTA90 MULTI ASSET STRATEGY | `1:0x401550bc1ce4fb0ffdd86015f49142bbaad29ad8` | — | Already discretionary; retained |
| NAVI Protocol | USDC High Yield | `1:0x47491305479635357df29fe0cb6f1af10e51d4da` | — | Missing product-specific decision evidence; unchanged |
| Rho X | Rho X Liquidity Provider | `1:0x4cb280e63251b9ab24a54def74bf5995d82ff398` | — | Mechanism supported; human selection not established |
| Gami, Stake Capital | Gami lvlUSD | `1:0x59b7942f7d2afd085691ce65c152e0d38d4eff22` | — | Co-curation established; product-specific human selection not established |
| 722 Capital | Unity Trust | `1:0x66dcb62da5430d800a1c807822c25be17138fda8` | — | Missing product-specific decision evidence; unchanged |
| 722 Capital | Trinity Trust | `1:0x71652d4898de9a7dd35e472a5fe4577ec69d82f2` | — | Missing product-specific decision evidence; unchanged |
| 722 Capital | 722 Capital USDC | `1:0x7d798ce99ee2adc183a865d76b112810d5395ea2` | — | Missing product-specific decision evidence; unchanged |
| Murmurr | Murmurr USDC One | `1:0x7d2c2f54792ad72cb834d298f542145b06b703cb` | D | Curator selection plus scored lending allocation |
| σ-Labs, Ellen Capital | Stablecoin Market Making | `1:0x8595f5ee036e1f7c4f3ca66327ba5f38c21cb7fb` | — | Missing product-specific decision evidence; unchanged |
| Hub Capital | Hub Capital crvUSD Vault | `1:0x9a458370556a68bdc130a80c35b294d0ad947a6a` | — | Missing product-specific decision evidence; unchanged |
| Hub Capital | Hub Capital USDC vault | `1:0xca790385506b790554571cbc9da73f0130cdcfd5` | — | Mechanism supported; human selection not established |
| σ-Labs, Ellen Capital | Cross-chain USDC Lending | `1:0xf02030ab0d7385ce4cc2f7f64b7b44430fb44c89` | — | Automated allocation documented; human selection not established |
| DeTrade | DeTrade Core AUSD | `143:0xe95074a86fead8647edff36af2f3f1dd7e8f7adb` | — | Automated allocation documented; human selection not established |
| 722 Capital | 722 Capital Hype | `999:0x7dd73a986f6188da1baaf68541fd6ebd455abd1d` | — | Mechanism supported; human selection not established |
| DeTrade | DeTrade Applefarm USDC | `8453:0x0a63471da694fc822f5a0a0b65978dda8c8edbe5` | — | Mechanism supported; human selection not established |
| MoneyFi | MoneyFi FlowForge Vault | `8453:0x4efc07dca8697792119484af33549f33ab11bf3c` | — | Automated allocation documented; human selection not established |
| DeTrade | DeTrade Core USDC | `8453:0x8092ca384d44260ea4feaf7457b629b8dc6f88f0` | D | Exact Core product linked; team selects protocols and incentive programmes |
| DeTrade | DeTrade Core ETH | `8453:0x9b97bfdfe44d1b113ecd4bf2f243ed36aca34523` | D | Exact Core product linked; team selects protocols and incentive programmes |
| 722 Capital | 722Capital-USDC | `8453:0xb09f761cb13baca8ec087ac476647361b6314f98` | D | Mandate describes active positioning in selected emerging protocols |
| MSA, Gami | MSA - Portefeuille Défensif | `8453:0xb725f2277f3f62ad017a6dc418433b326a9f6334` | — | Already discretionary; retained |
| TruMarket | TruMarket | `8453:0xbe7db44f4ce20dac83b578b94fd35087f66e9754` | D | Team sources and structures agricultural trade-finance deals |
| DeTrade | DeTrade Core EURC | `8453:0xd4401d8bea82e4e6c40bb26ae3a04d2fb7ca4550` | D | Exact Core product linked; team selects protocols and incentive programmes |
| Nara Capital | GUSDC | `8453:0xd5c22fa3f7ee979ed7c28e36669b29797ab277e4` | — | Mechanism supported; human selection not established |
| MSA, Gami | MSA - Portefeuille Dynamique | `8453:0xf56bfe07b8d6e6d74258cdb6969a633629b06b08` | — | Already discretionary; retained |
| 722 Capital | 722Capital-ETH | `8453:0xfce2064b4221c54651b21c868064a23695e78f09` | — | Mechanism supported; human selection not established |
| 722 Capital | Yield Algo Trading | `42161:0x99cd0b8b32b15922f0754fddc21323b5278c5261` | — | Missing product-specific decision evidence; unchanged |
| VOLTA90 | Volta90 Multi-asset trading strategy | `42161:0x9e6de1f581d63129af453fe1ea7253b280b4dca8` | — | Already discretionary; retained |
| Murmurr | Murmurr USDC Blue-Chip | `1:0x845339546d90e6e21fdb9638cc7d8d15ebb5e4ac` | A, D, L | Curator selection plus scored lending allocation |
| Murmurr | Murmurr USDC Frontier | `1:0x29ca54dd3ae9e43abb10880f5e08da439bf25f99` | A, D, L | Curator selection plus scored lending allocation |
| Murmurr | Murmurr USDC cbAssets | `8453:0xcda5dbf58a016158ecd4a226109752cc3a627ec8` | A, D, L | Curator selection plus scored lending allocation |
| Murmurr | Murmurr USDC aHYPER | `143:0x9f209b43559b13b783eaa0a6686921c7b174f269` | — | Fixed universe; no evidenced human investment selection |
| Murmurr | Murmurr USDC WHYPE | `999:0x3f78bdd48b363ec927dc227d6c80a6e4821c7e10` | — | Fixed universe; no evidenced human investment selection |

### Persistence

Migration `062-curator-discretionary-strategy-tags.py` freezes these nine changed
deployments and refreshes only `_strategy_tags`, through the shared repair
resolver. Both Morpho generations now pass the chain ID to that resolver's
protocol mapping. Same-address deployments on another chain do not inherit the
new classifications. No successful migration is edited or rerun.

Run 062 after merge in the normal stopped-writer pending batch described in
migration 061. Preview and apply both require the looped scanner stopped, the
same inspected image and persistent mounts, and lifetime guard before writer
lock. At most nine existing rows and nine fields change; missing targets are
reported without insertion. No production migration or historical scan was run.
Previously recorded price gaps remain a separate production-state investigation.

### Review checks

The final focused suite passed **294 tests** locally on 10 November 2026:

```shell
source .local-test.env && PYTHONPATH="$PWD" timeout 180s poetry run pytest tests/vault/test_curator_discretionary_tags.py tests/vault/test_lagoon_curator_onboarding.py tests/erc_4626/vault_protocol/test_lagoon_strategy_review.py tests/erc_4626/vault_protocol/test_lending_strategy_tags.py tests/erc_4626/vault_protocol/test_lagoon_strategy_tags.py tests/erc_4626/test_migrate_vault_strategy_tags.py tests/vault/test_lagoon_strategy_migration.py tests/erc_4626/test_list_vault_strategy_categories.py -q --tb=short
```

This covers all 34 reviewed tag sets through the real adapter and repair paths,
both Morpho versions, wrong-chain isolation, migration preview/apply/repeat,
missing targets and atomic rejection of incompatible detection. The nine-target
fixture changes exactly nine tag fields and preserves unrelated rows and files.
The 29 live Lagoon research requests also succeeded; these were manual public
API reads, not CI integration tests, and required no credentials.

The skill validator, scoped Ruff formatting and `git diff --check` passed.
Ruff reports 32 pre-existing findings in the older touched modules, with no
introduced findings against HEAD. The expanded public category description was
checked against the writing guide; individual vault descriptions were unchanged.
Claude CLI with exact model `claude-opus-5-5` completed an independent review
and a focused follow-up after the Gami correction: **approved; no remaining
material findings**. No changes were pushed or applied to production in this
follow-up task.
