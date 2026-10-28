# Flying Tulip staking integration

[Flying Tulip](https://flyingtulip.com/ftusd/) issues **ftUSD**, a dollar-targeted settlement token, and two optional staking positions: **sftUSD**, with separately claimable FT rewards, and **ssftUSD**, with yield compounded as ftUSD. This package covers reviewed sftUSD deployments on Ethereum, BNB Chain and Sonic and the reviewed ssftUSD deployment on Ethereum.

The authoritative product description is the [Flying Tulip ftUSD documentation](https://docs.flyingtulip.com/product-suite/ft-usd/). The [current address book](https://flyingtulip.com/address-book/flying-tulip-address-book-prod.json) includes ssftUSD; the older ftUSD contracts API lists only sftUSD. These are maintenance references, not runtime dependencies or dynamic classification sources.

## Auto-compounding ssftUSD

The [September update](https://blog.flyingtulip.com/flying-tulip-september-update/) corroborates the 22 September 2026 announcement. The reviewed Ethereum proxy is `0x232128844e5af56fd4b3f8bf97e08ab477282ab5`; on 2026-10-28 its explorer-reported implementation was [StableEpochRewardsVault](https://etherscan.io/address/0xD940d531b983557884B31fD915A1Df97bebe6e09#code). Verified source was read through Routescan's Ethereum `getsourcecode` API after resolving the ERC-1967 proxy. The implementation can be upgraded; these observations describe the reviewed version.

`FlyingTulipStableVault` uses standard ERC-4626 price, supply and NAV accounting. The six-decimal ftUSD asset and eighteen-decimal share token must be scaled independently. Distributed ftUSD vests into the share price over eight hours; this is reward vesting, not an eight-hour withdrawal lockup. `totalAssets()` excludes unvested rewards, so the reported NAV follows vested accounting rather than gross funded backing. Treasury allocations are variable and the documented ssftUSD allocation is slightly lower than sftUSD.

Standard withdrawals settle synchronously or revert atomically if the underlying circuit breaker would queue them. This differs from sftUSD's queue-ID return. The new adapter remains uncertified for public transaction-manager support.

The vault rejects zero-address deposit receivers. Its local historical reader excludes `maxDeposit(0)` instead of interpreting zero as a global cap; existing generic readers are unchanged. Earlier persisted zero-address capacity observations cannot establish historical deposit availability. The metadata migration does not rewrite them or any price history.

At review block `26,234,307`, NAV was `411946.118918` ftUSD, supply was `409264.10240710532903198` ssftUSD, and the share price was `1.006553265959853966066145662` ftUSD. The real-provider regression uses the shared Anvil pool at this post-deployment block because the canonical July Ethereum fork predates the vault.

## Product model

The user-facing principal route is:

```text
USDC -> ftUSD -> sftUSD
USDC <- ftUSD <- sftUSD
```

The outer USDC/ftUSD conversion uses Flying Tulip's `MintAndRedeem` engine. At the reviewed blocks, the inner ftUSD/sftUSD conversion is 1:1. On Ethereum, the relevant reviewed contracts are:

- [sftUSD / EpochRewardsVault proxy](https://etherscan.io/address/0xeb48218a4c35C814C7678cBcae88C6Ee037F7625#code)
- [ftUSD MintAndRedeem proxy](https://etherscan.io/address/0xAa48EcBC843cF7E9A29155D112b8Cb27902bD23C#code)
- [ftUSD token](https://etherscan.io/address/0xF7D85EC4E7710F71992752EAC2111312E73E9C9C#code)

sftUSD holders receive FT distributions through settled reward epochs. The rewards are separately claimable FT: they do not automatically increase the ERC-4626 redemption amount. The documentation describes this distinction explicitly: ftUSD is non-yielding until it is staked, while sftUSD receives the distributions and users claim FT separately.

## sftUSD external rewards

The sftUSD contract presents a standard ERC-4626 surface, but its economics differ materially from a conventional auto-compounding vault.

| Area | Ordinary auto-compounding ERC-4626 | Flying Tulip sftUSD |
| --- | --- | --- |
| Principal conversion | `totalAssets() / totalSupply()` normally changes with profit and loss | The reviewed contract state reports 1 sftUSD = 1 ftUSD; rewards are paid separately |
| Yield | Usually retained by the vault and reflected in the share price | Distributed as separately claimable FT rewards |
| Historical return | Can use the redeemable share price | Requires a non-redeemable reward-reinvested ftUSD share-price equivalent |
| Exit | Usually one synchronous redemption call | May pay immediately or return a circuit-breaker queue ID for later execution |
| Comparable entry/exit fee | Often a vault deposit/redemption fee | The relevant cost is the separate USDC/ftUSD mint and redeem route |

The adapter reads the live ERC-4626 conversion as reported by the contract. The reviewed blocks report 1:1; this does not imply that a reward-adjusted historical value can be redeemed from the vault.

The fee fields deliberately model the whole USDC-funded vault-equivalent route for comparisons with USDC-denominated vaults. At the reviewed configurations, the USDC mint and redeem fees are 0.07% each way on Ethereum and Sonic, and 0.10% each way on BNB Chain. They are externalised conversion costs, not direct sftUSD wrapper fees. They exclude gas, oracle conversion differences, queue-execution gas and the costs of selling claimed FT. The `MintAndRedeem` collateral configuration is governance-configurable; consult the [verified Ethereum contract](https://etherscan.io/address/0xAa48EcBC843cF7E9A29155D112b8Cb27902bD23C#code) before treating these rates as current.

## Pipeline and data model

The sftUSD integration uses a protocol-owned contextual history pipeline because polling `totalAssets()` and `totalSupply()` cannot capture separately claimable FT rewards and would report a false zero reward return. ssftUSD does not enter this pipeline or carry its synthetic-price flags.

1. [`constants.py`](constants.py) contains the reviewed chain/address registry, fee model, Curve boundary and other maintained protocol facts.
2. [`vault.py`](vault.py) contains separate adapters and price readers for the two reviewed products. A small private base shares only the fee convention, strategy-tag lookup and application link. Classification uses a shared chain-aware feature lookup; only sftUSD selects the contextual reader.
3. [`historical_context.py`](historical_context.py) streams `EpochSettled` reward events and sftUSD mint/burn transfers with Hypersync. It persists raw, replayable evidence in the shared `vault-historical-context.duckdb` cache.
4. [`reward_price.py`](reward_price.py) maps each settlement timestamp to Ethereum and stores the corresponding historical FT/ftUSD Curve oracle observation.
5. `FlyingTulipHistoricalReader` replays supply and reward events in order. It compounds each priced FT distribution into `share_price_equivalence`; `total_assets` is the matching synthetic performance value, not contractual vault assets.

The source history begins at each proxy deployment so supply is correct at the first supported reward settlement. Performance begins only after the canonical Curve market exists. This prevents pre-market FT rewards from being assigned a fabricated price, and it makes missing or stale price data visible instead of silently reporting zero yield.

The scanner marks these rows with the shared `share_price_equivalence` feature. Downstream charts, CAGR and TVL-equivalent calculations can reuse the GMX-equivalent pipeline, while consumers can distinguish this non-redeemable curve from the live ERC-4626 price.

## Role of the Curve pool

The Ethereum [FT/ftUSD Curve pool](https://etherscan.io/address/0x68102ff5406475881462880a8da3c9bc9181ad6c#code) is the canonical historical valuation source for **FT rewards**, not the principal entry or redemption market.

The fixed-block integration test verifies the pool token order and oracle orientation. During a production prefill, the reader records `price_oracle()` and its update time, then converts Curve's inverse quote to ftUSD per FT. For a settlement on any supported chain, it selects the greatest Ethereum block whose timestamp is not later than that settlement. An observation older than seven days is rejected. If the shared Ethereum timestamp cache has not yet reached a foreign-chain settlement, that reward remains visibly unpriced until the cache advances; it does not stop the wider vault scan. The canonical history starts from Ethereum block `25,531,725`; earlier epochs remain source evidence but cannot contribute to the reward-equivalent CAGR.

Curve is deliberately not used to discover Flying Tulip vaults, calculate the live sftUSD conversion, or price USDC entry/exit costs. Those are separate concerns handled by the hardcoded deployment registry, the sftUSD contract, and `MintAndRedeem`, respectively.

## Operations and verification

Use the dedicated scripts rather than manually altering the context database:

- [`backfill-flying-tulip-history.py`](../../../../scripts/erc-4626/backfill-flying-tulip-history.py) streams all reviewed source histories and Curve price provenance with visible progress bars.
- [`examine-flying-tulip-vaults.py`](../../../../scripts/erc-4626/examine-flying-tulip-vaults.py) is read-only and reports source coverage, missing or stale prices, equivalent TVL and CAGR.
- The [numbered migration system](../../../../.claude/docs/migration-scripts.md) repairs the fixed Ethereum ssftUSD deployment with migration 001. The broader `migrate-flying-tulip-vault-metadata.py` tool is retained as legacy implementation reference for the original three sftUSD deployments. Do not use its standalone format for later repairs; port the reviewed operation into a new numbered migration.

The operator commands and safety boundaries are documented in the [vault-script guide](../../../../scripts/erc-4626/README-vault-scripts.md#flying-tulip-reward-equivalence-backfill). Focused coverage lives in [`tests/erc_4626/vault_protocol/test_flying_tulip_vault.py`](../../../../tests/erc_4626/vault_protocol/test_flying_tulip_vault.py), [`test_flying_tulip_historical_context.py`](../../../../tests/erc_4626/vault_protocol/test_flying_tulip_historical_context.py) and the [real-provider historical integration test](../../../../tests/erc_4626/test_flying_tulip_historical_context_integration.py).
