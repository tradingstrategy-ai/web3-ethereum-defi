# Nova Markets Conviction Pool

Nova Markets operates the Arbitrum Conviction Pool at
`0x9ca80193712f372e2ff33a00127292884f6f8598` (chain 42161). The
[official contract disclosures](https://docs.nova.markets/#/contracts)
identify this exact address as Nova's independently administered Gains fork.
The hardcoded classifier returns only `nova_markets_like`, before generic
Gains probes. `NovaMarketsVault` supplies the independent protocol name, public
copy, app link and strategy tags while reusing the compatible epoch reader and
deposit/redemption manager. No new protocol-wide probes or ABIs are needed.

The [Conviction Pool documentation](https://docs.nova.markets/#/conviction-pool)
describes USDC counterparty liquidity, trading and borrowing fees, liquidations,
trader PnL and Conviction Points. It is a different product from the
[Lagoon-based NLP vault](https://docs.nova.markets/#/nlp-vault) on HyperEVM;
that deployment keeps its Lagoon integration. No separately dated launch
announcement for the Conviction Pool was verified as of 2026-10-28.

Nova NLP is deployed at `0xeeed7bb939d65938fe8f40dd898cd5942e32f09e`
on HyperEVM (chain 999). The exact chain/address curator override attributes
it to `nova-markets`, including cached rows without a manager name, while its
protocol remains `Lagoon Finance` (`lagoon-finance`). Nova is registered in
`ALL_PROTOCOL_CURATOR_SLUGS`, so both its curator profile and the NLP vault's
public JSON row carry `protocol_curator: true`. Its maintained Lagoon
strategy tags describe order-book market making, perpetual futures and the
documented idle-capital lending sleeve. No name-only `Nova` alias is needed.
The existing [Lagoon vault app](https://app.lagoon.finance/vault/999/0xEeEd7BB939d65938Fe8f40dd898Cd5942E32f09E)
remains its product destination.

On 2026-10-28, both the official NLP documentation and the
[Lagoon vault API](https://app.lagoon.finance/api/vault?chainId=999&address=0xeeed7bb939d65938fe8f40dd898cd5942e32f09e)
returned HTTP 200 and confirmed the exact HyperEVM address. The API describes
the market-neutral strategy, market-making fee/PnL income and optional Morpho
allocation, supporting the existing tags. Its `state.curators` list was empty,
which explains why API-derived manager metadata alone did not attribute it.

## Public links and artwork

The [official pool app](https://app.nova.markets/earn/liquidity-pools) returned
HTTP 200 with title `Liquidity Pools · Nova` on 2026-10-28. Nova's documentation
links this route and identifies the pool's actual chain; the app itself requires
client-side state or sign-in to display the pools. The docs also directly link
`https://x.com/novadotmarkets`, now the canonical protocol feeder. The existing
Nova curator delegates to this feeder without duplicate collection sources.

Reused the existing official vector brand mark and its 256x256 transparent
`light.png`, already shared with the curator under the `nova-markets` slug.
The artwork was visually checked and the metadata exporter detects it.
See the [logo source record](../../../data/vaults/original_logos/nova-markets/README.md).
No independent Nova audit report or public contracts repository was found;
upstream Gains audits must not be represented as audits of Nova's deployment
or administrative configuration.

## Direct pool fee review

The direct Conviction Pool uses `VaultFeeMode.feeless`: management,
performance, deposit, withdrawal and depositor protocol fees are zero in the
reviewed implementation. Trading-fee income and trader PnL are already reflected
in share value. The documented 15% trading-fee allocation is pool revenue, not
a depositor performance charge. Nova's
[funding documentation](https://docs.nova.markets/#/get-started) places the flat
0.20 USDC charge in the trading-balance funding flow. It is not charged by the
pool's direct `deposit()` or redemption paths. App routing, bridging and gas
costs are outside this contract-level fee schedule.

On 2026-10-28, Etherscan's verified proxy source and an onchain EIP-1967 storage
read identified implementation `0xa17decd03257b84bf3724c0425268c02f344db33`.
The implementation itself had no verified source on Etherscan or Sourcify.
Its compiler metadata points to IPFS CID
`Qmda26tBoiG7evRk3MSudwfFtFTnTQZQQR4RZxWpe6crJR`; public IPFS gateways
returned HTTP 429 or timed out. Explorer HTML was inaccessible and no connected
browser tool was available, so the review used the configured Etherscan API
and Arbitrum RPC providers.

The fee conclusion is supported by a reproducible executable-source match:

1. Fetch the [verified Gains GToken implementation source](https://arbiscan.io/address/0xeb754588eff264793bb80be65866d11bc8d6cbdd#code)
   and all its dependencies through Etherscan v2.
2. Recompile it with Solidity `0.8.23+commit.f704f362`, optimiser enabled,
   10 runs and Paris EVM target. The complete runtime, including metadata,
   matches that verified baseline exactly.
3. Add two branches to `ChainUtils.getWrappedNativeToken()`: chains 4326 and
   6343 return `0x4200000000000000000000000000000000000006`.
   Keep GToken, the ERC-4626 dependency and all other sources unchanged.
4. Recompile with the same settings. The resulting 24,514 executable bytes
   match Nova's live implementation byte-for-byte. Only the length-delimited
   trailing Solidity compiler metadata is excluded. This proves executable
   equivalence; it does not claim Nova published the reconstructed source.

The reviewed source uses ordinary ERC-4626 previews and transfers for
`deposit`, `mint`, `withdraw` and `redeem`, with no entry/exit deduction or fee
recipient. `distributeReward()` transfers rewards into the pool and increases
`accRewardsPerToken`, then updates `shareToAssetsPrice`; it does not mint fee
shares or skim a depositor charge. `updateAccPnlPerTokenUsed()` updates share
value for trader PnL. There is no management- or performance-fee mechanism in
this matched runtime. The extra chain-helper branches do not affect Arbitrum.

Manual source-reproduction run on 2026-10-28: **passed**, Arbitrum block
519063197, using Etherscan v2 and the supplied Goldsky/dRPC/Alchemy provider
environment with credentials omitted. The full runtime hash was
`0x0c471a563ecf2eca1efcf04f58b7daa873099b35b6973210932eaf445e1f0fcf`;
the executable hash excluding compiler metadata was
`0xeb7542360e4260c63f57dd011b2ec4f93be8c639e0aa4c51ae9471015d07ea0f`.
The initial compiler binary came from the official
[Solidity binary distribution](https://github.com/ethereum/solc-bin/tree/gh-pages/linux-amd64)
and its SHA-256 was checked against that distribution's manifest.

To reproduce, supply that compiler through `SOLC_BINARY`, plus
`JSON_RPC_ARBITRUM` and `ETHERSCAN_API_KEY` (or `ARBISCAN_API_KEY`):

```shell
source .local-test.env && PYTHONPATH="$PWD" timeout 180s poetry run python scripts/erc-4626/verify-nova-markets-fees.py
```

The script reads one current block, verifies the baseline compilation, applies
only the two helper branches, and fails if the executable no longer matches.
It sends no transactions and writes no scanner state. This is a manual
provider-backed check, not a CI result. The fixed-block fork test additionally
pins the implementation and runtime hash and checks the exported fee schedule;
its guarded lifecycle redeems 100 USDC less one raw-unit rounding difference.
Reassess after any implementation upgrade. This fee review assigns no risk
rating.

Cached fee records with `fee_mode=None` acquire `feeless` through the shared
export's current matrix lookup after the protocol identity repair. No price
rescan is required; fresh scans also persist the explicit zero-fee schedule.

## Security and development review

Reviewed on 2026-10-28. The scope is `nova.markets`, its Arbitrum Conviction
Pool and HyperEVM NLP vault. Similarly named forex brokers and unrelated
GitHub repositories were excluded. This is an evidence review, not a security
audit or a curator whitelist decision.

### Audit coverage

Nova's contract documentation links upstream Gains reports from Halborn and
CertiK, rather than a report commissioned for Nova's deployment. The Halborn
URL returned a client-rendered report shell, so its detailed scope and findings
were not independently inspected in this review. No Nova-specific report was
located in the official site/docs or targeted audit searches.
[CertiK's own Nova profile](https://skynet.certik.com/zh-CN/projects/nova-markets)
listed neither a CertiK nor a third-party audit, and no bug bounty; absence from
that directory alone does not prove that no private report exists.

The [Lagoon audit index](https://docs.lagoon.finance/resources/audits) lists
v0.6.0 reports dated 11 May 2026 (Trail of Bits) and 25 May 2026 (Nethermind).
NLP's current API identifies its vault as v0.6.0. These are infrastructure
reports; their existence does not establish coverage of Nova's deployed
configuration, market maker, valuation process, MPC policies or offchain
Hyperliquid activity. The earlier executable-source reproduction of the
Conviction Pool is also not an independent audit.

### Public development history

Neither Nova's homepage, application nor current documentation linked an
attributable public GitHub development repository. GitHub repository searches
for `nova.markets`, `novadotmarkets`, and `"nova.markets" in:readme` returned
unrelated projects or no results. No Nova commit history, contributor record,
release history, CI or public security policy could therefore be verified.
This is a bounded search result, not a claim that Nova has no private codebase.

[Lagoon's public repository](https://github.com/hopperlabsxyz/lagoon-v0)
does expose infrastructure development history: the GitHub API reported
creation on 12 June 2024 and latest default-branch commit
`c39fe2ba60e78790558c271f38e01ed300144abd` on 2 October 2026.
That history belongs to Hopper Labs/Lagoon, not Nova. Likewise, Gains source,
ABIs and documentation do not establish Nova's own development track record.
The Conviction Pool implementation remains unverified on the explorers checked,
despite the locally reproducible executable match documented above.

### Privileged controls

Manual reads at Arbitrum block 519069694 confirmed:

| Control | Observed value | Implication |
| --- | --- | --- |
| Governance Safe | `0xba86498b08a2fac8e987eeab5eeb5930689b293f`, threshold 2, three owners | Two keys can authorise governance; signer identities and organisational independence were not established. |
| Pool owner | `0x35016c7cf14ba1080d486143a98fcb3fa9bf1ea4` | Pool owner operations use the 14-day timelock. |
| Pool manager | `0xc2a8e52b78dea6675f924690e6e53556652bab01` | Manager operations use the three-day timelock. |
| Pool admin | Governance Safe | The admin address is distinct from the timelocked owner and manager. |
| Pool PnL handler | `0x1410fa3f87f6a6ea221e5d2ab608c23005ec34f3` | The trading contract is authorised for trader-PnL payments from the pool. |
| Pool ProxyAdmin | `0x5a780c806b8394c575eae1781fdbcca5506090bb`, owned by the 14-day timelock | The observed pool upgrade authority is timelocked. |
| Timelock minimum delays | 1,209,600 / 259,200 / 36,000 seconds | Fourteen days, three days and ten hours respectively. |

At block 519069984, the Safe had proposer, executor and canceller roles on
all three timelocks. These reads establish the observed roles, not an exhaustive
enumeration of every historical role grant or potential privileged path.

Nova documents immediate Safe control over market fees, leverage, liquidation
parameters, fee splits, some oracle replacements and trading pauses. Its
separate manager key can adjust market exposure/depth and borrowing/funding
rates without a timelock. Trading-code changes can use the three-day route or
the ten-hour emergency route. The latter is shorter than the pool's minimum
three-day withdrawal delay. Consequently, the 14-day pool-upgrade timelock is
not a general guarantee that depositors can exit before every material
governance change. The PnL handler's code and the oracle system remain relevant
even while the pool implementation is unchanged.

Nova's [pricing documentation](https://docs.nova.markets/pricing.md) describes
a custom Chainlink-node network using the median of at least three of five
responses. Independent node-operator identities were not established. Nova's
[risk disclosures](https://docs.nova.markets/risks.md) explicitly identify
operated oracles and keepers. A multi-node response threshold alone does not
establish independent control of the nodes.

### NLP operational controls

At HyperEVM block 49755898, the public `owner()` getter and the shared Lagoon
v0.6 ERC-7201 role reader confirmed these addresses, matching Lagoon's API:

| Role | Address |
| --- | --- |
| Owner and valuation manager | `0xfbb96d7e8683142d61f5e93dad93355026c54012` |
| Asset / market-making account | `0x01e187f06c9e7871986f0ed4e9d2bf528af9b2cf` |
| Whitelist manager | `0x6ed32f3236e039dd157c744104b218bc0a657ac0` |
| Fee receiver | `0x3ee91834b77650b83494e6e21b676ea8c3a6b4e2` |

The owner/valuation address is also one of the three Arbitrum Safe owners.
The role addresses above had no HyperEVM contract code in the adjacent block
49755839 check; in particular, the API's `safe` label does not make NLP's asset
account an onchain Safe multisig. Nova identifies Fordefi MPC key management,
but the MPC signing threshold and policy administrators were not independently
verified. An externally owned address does not reveal how many people control
its key shares.

NLP therefore depends on operator custody/key policies, valuation reporting,
settlement execution and Hyperliquid availability. Nova's seven-day withdrawal
description is an operator estimate; the standard Lagoon request/settlement
flow does not impose that maximum as an onchain deadline. The existing Lagoon
technical-risk label describes the infrastructure and is not a Nova operator
or strategy risk assessment. On 2026-10-28 the user assigned **Dangerous** to
Nova. The Nova protocol matrix and both exact vault address overrides now use
`VaultTechnicalRisk.dangerous`. NLP retains its Lagoon protocol classification.
The public export applies address-specific manual ratings over lower cached
scanner ratings while retaining stricter ratings such as a blacklist. This
takes effect on metadata export without a price rescan. The separate curator
whitelist/blacklist status remains `unknown`; the Dangerous technical-risk
rating does not blacklist either vault.

## Integration verification

Manual local run on 2026-10-28 using the supplied Arbitrum provider environment
(Goldsky, dRPC and Alchemy; credentials redacted):

```shell
source .local-test.env && PYTHONPATH="$PWD" timeout 180s poetry run pytest tests/erc_4626/vault_protocol/test_nova_markets.py -q
```

The fixed fork is block 518876926, because the canonical July Arbitrum block
predates this deployment. The pooled-fork cache contains actual pool, USDC,
PnL-feed, deposit and redemption reads and is committed as a warm seed.
At that block, total assets are 206502017790 raw USDC, total supply is
194515028053 raw nUSDC, and 100 USDC mints 94934580 raw shares. The guarded
lifecycle returns 99999999 raw USDC after request, epoch settlement and claim,
and leaves zero shares. The reader reports a full three-day epoch rather than
the six-hour oracle sampling interval; withdrawal bounds are three to nine days.

Result: passed. The two fork tests passed with Anvil v1.3.2, then passed again
with the current CI-pinned Anvil v1.8.6 after merging the updated master branch
on the same date. The committed seed was refreshed from the successful v1.8.6
run. On this older-glibc host the official Alpine/musl build was used:

```shell
source .local-test.env && PATH="/tmp/foundry-1.8.6-musl:$PATH" PYTHONPATH="$PWD" timeout 180s poetry run pytest tests/erc_4626/vault_protocol/test_nova_markets.py -q --tb=short
```

This is a manual provider-backed Anvil run, not a CI result. Transactions were
submitted only to the local fork. The test is automatically runnable wherever
`JSON_RPC_ARBITRUM` and Anvil are supplied.

## Production migration reminder

**Run after merge and deployment.** Follow the [numbered migration guide](../../../../.claude/docs/migration-scripts.md)
to stop the daemon, preview and apply the full pending batch through the host
launcher, then restart the same inspected image for cleaning, exports and uploads.
Record the UTC date, deployed commit, command result and validation result.
The former standalone Nova command is retired and its format is legacy.
Use a new numbered migration for later repairs.

Migration 002 repairs exactly the reviewed Arbitrum row. It fails for a missing
row or conflicting protocol/detection identity. It preserves manual flags,
manager enrichment, risk overrides, first-seen blocks, event counts, leads,
per-chain scan boundaries, historical prices, compatible reader state and
all timestamp caches. Migration 003 refreshes the other 24 Gains deployments.

Validate the exported row by chain/address: `protocol: Nova Markets`,
`protocol_slug: nova-markets`, `features: nova_markets_like`, curator
`nova-markets`, the Nova app link and the Conviction Pool strategy note.
Price row counts and reader-state files must remain unchanged by migrations.

The normal export also applies the NLP curator override at chain 999/address
`0xeeed7bb939d65938fe8f40dd898cd5942e32f09e`: `protocol_slug: lagoon-finance`,
`curator_slug: nova-markets` and `protocol_curator: true`. It needs no price rescan.
