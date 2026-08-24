# Rysk Premium vaults

This module integrates Rysk Premium with the common vault analytics pipeline.
Rysk Premium shares are epoch-settled LP shares, rather than ERC-4626 vault
shares.

## What is Rysk

[Rysk](https://docs.rysk.finance/) is an onchain options protocol. Its option
writers receive upfront premium through a request-for-quote system and provide
full collateral: cash for puts and the underlying asset for calls. The
[protocol overview](https://docs.rysk.finance/getting-started/protocol-and-product/how-it-works)
describes the option lifecycle and collateralisation.

Rysk Premium packages option writing into curator-managed liquidity pools. A
curator selects which options the pool writes, including their strikes and
expiries. Liquidity providers supply the collateral and receive ERC-20 pool
shares. See the official [Rysk Premium LP explainer](https://docs.rysk.finance/rysk-premium/rysk-premium-explainer)
for the authoritative description of shares, epochs, valuation, withdrawals,
fees and risks.

Premium shares are ERC-20 claims on a pool's epoch accounting process.

## What are Rysk vaults

A Rysk Premium vault is an option-writing pool with three relevant contract
roles:

- The **pool** holds accounting state, issues ERC-20 LP shares and queues
  deposits and withdrawals.
- The **registry** supplies protocol-level configuration for the pool.
- The **option handler** connects the pool to its option-writing lifecycle.

The current catalogue is published by Rysk's [public pools API](https://premium.rysk.finance/api/pools).
The pipeline refreshes this catalogue dynamically for the reviewed Ethereum
and HyperEVM deployments. A newly supported chain also needs a scanner RPC
configuration before it can be indexed.

Representative deployments from the catalogue include:

| Product | Pool share | Registry | Option handler |
|---|---|---|---|
| Hyperion HiHYPE/USDC Put, HyperEVM | [pool](https://hyperevmscan.io/address/0xa26801f689fbdf0ff96eff52077b958d1062ba85) | [registry](https://hyperevmscan.io/address/0xb8dbfca0fd36cf5102cdf4d32087ca1e7b42f6c5) | [option handler](https://hyperevmscan.io/address/0xc92c394982a32c98bb8781101a825b7abed9e732) |
| KPK WETH Put, Ethereum | [pool](https://etherscan.io/address/0x1195826418541cb3e80a22ef5736a6794393c91a) | [registry](https://etherscan.io/address/0x0941f9a243878d4d5922462d07c15027e3b9026b) | [option handler](https://etherscan.io/address/0x93bfe72a9729ae68c15c3d6da1206f408fca8c4e) |

These addresses are examples, not a static statement of complete protocol
coverage. Use the public pools API for the current set. Rysk's public source
code and client libraries are available through the official
[Rysk Finance GitHub organisation](https://github.com/rysk-finance).

## How Rysk vaults differ from ERC-4626 vaults

Rysk Premium shares are ERC-20 tokens, but the pools do not implement the
ERC-4626 vault interface.

| Behaviour | ERC-4626 vault | Rysk Premium vault |
|---|---|---|
| Entry | Shares are normally minted by `deposit()` or `mint()` at the current conversion rate | Collateral enters `pendingDeposits`; shares are minted when the epoch executes at the final deposit price |
| Exit | `withdraw()` or `redeem()` normally burns shares in one synchronous transaction | The user initiates a withdrawal, waits for an eligible epoch and then completes it |
| Pricing | `convertToAssets()` and `totalAssets()` provide standardised onchain conversion inputs | A Governor submits deposit and withdrawal prices based on epoch NAV |
| Price timing | Usually available continuously at an arbitrary block | Final only at discrete epoch boundaries, after the dispute period and epoch execution |
| Valuation | Often derivable from onchain assets and share supply | Includes an offchain mark-to-market value for open option positions |
| Generic flow events | Standard ERC-4626 `Deposit` and `Withdraw` events | Protocol-specific queued deposit, withdrawal and epoch activity |

The Premium contract's simplified TVL is free collateral plus allocated
collateral. The official explainer states that full NAV also subtracts the
mark-to-market liability of the open option book. Consequently, dashboard TVL
must not be divided by share supply to manufacture a share price.

## What users should expect

Liquidity providers should expect:

- **Discrete pricing.** Share prices change at finalised epoch boundaries, not
  continuously with every block.
- **Queued entry.** A deposit can be usable by the pool before its corresponding
  shares are minted at the next executed epoch.
- **Two-step exits.** A withdrawal is initiated first and completed after an
  epoch processes it.
- **Liquidity-dependent withdrawals.** If free collateral is insufficient,
  escrowed shares remain queued until a later epoch can process them.
- **Option risk.** Premium income can increase NAV, while adverse option
  settlement or mark-to-market losses can reduce it materially.
- **Curator and valuation risk.** Strategy selection is discretionary, and the
  epoch NAV depends on governance submitting a committee-derived option-book
  valuation. A dispute period delays final execution and permits corrections.
- **Protocol-specific fees.** An option-sale fee is deducted from option
  premium and split between the protocol and curator. It is not modelled as a
  generic vault management or performance fee.

Library users should also expect the adapter to be read-only. Generic deposit,
redemption and flow-manager construction raises `NotImplementedError` until a
complete Rysk epoch transaction lifecycle is implemented and tested. The
adapter does not advertise public deposit-manager capability.

## Share-price equivalent

The historical equity curve uses the final epoch withdrawal price per share:

```text
share_price_equivalent = withdrawalPps / 10**collateral_token.decimals
```

`withdrawalPps` is encoded in the native precision of the pool collateral
token: six decimals for USDH or USDC, and eighteen for kHYPE. Once scaled, it
is the final exit price per share. `depositPps` is retained for entry-price
auditing but is not substituted into the equity curve. If the feed contains
several final rows for an epoch, the row at the greatest source block is
selected; rows tied within one block have no published update order and are
selected by a deterministic record fingerprint.

The curve is sparse by design: it contains final epoch observations rather
than interpolated hourly values. Historical rows leave `total_assets` and
`total_supply` empty because the source does not provide a matching full-NAV
and supply pair for the observation. This avoids presenting the simplified TVL
as full option-book NAV.

## How the pipeline handles Rysk Premium

The normal EVM vault pipeline handles Rysk in the following sequence:

1. [`fetch_rysk_premium_pools()`](api.py) reads the current public catalogue.
2. [`fetch_and_sync_rysk_premium_catalogue()`](vault_sync.py) installs the
   runtime catalogue and creates or refreshes common vault metadata rows.
3. Chain-aware hardcoded classification assigns `rysk_premium_like` and
   `share_price_equivalence`. Rysk is exempt from the generic ERC-4626 deposit
   activity filter because its pools do not emit those standard events.
4. [`fetch_rysk_premium_snapshots()`](api.py) reads the complete paginated
   snapshot stream for each selected pool.
5. [`RyskHistoricalContextStore`](historical_context.py) retains every raw
   snapshot in the shared `vault-historical-context.duckdb` database. Exact
   duplicate records are ignored; distinct corrected records are preserved for
   deterministic epoch selection.
6. [`RyskPremiumHistoricalReader`](historical.py) selects final `EPOCH` rows,
   deterministically selects one record per epoch and emits the scaled withdrawal PPS
   through the common `VaultHistoricalRead` interface.
7. The generic historical scanner merges these sparse observations into the
   common vault price Parquet without replacing unrelated vault data.

## Manual backfill and review

[`backfill-rysk-vault-prices.py`](../../../../scripts/erc-4626/backfill-rysk-vault-prices.py)
performs a complete source refresh for the current Ethereum and HyperEVM
catalogue, then writes only final epoch exit-price observations through the
common Parquet writer. It does not use reader state. Set `CHAINS` to
`ethereum`, `hyperliquid`, or both when an operator needs to scope a run.

[`examine-rysk-vault-performance.py`](../../../../scripts/erc-4626/examine-rysk-vault-performance.py)
is read-only. It prints each pool's name, chain, latest reported collateral
TVL, last final PPS and lifetime collateral-denominated CAGR. It deliberately
labels TVL as reported collateral rather than NAV and omits CAGR for curves
with insufficient final epochs.

The scheduled integration is wired through
[`scan_all_chains.py`](../../../vault/scan_all_chains.py). Both metadata-cache
hits and full lead scans refresh the Rysk catalogue, and price scans prefill
the contextual Rysk history before requesting common vault reads.

## Our Python APIs

The main integration surfaces are:

- [`RyskVault`](vault.py) — common read-only `VaultBase` adapter, token metadata
  and protocol identity.
- [`RyskPremiumPool`](constants.py) — typed catalogue record and reviewed seed
  deployments.
- [`RyskPremiumSnapshot`](api.py) — typed raw snapshot.
- [`fetch_rysk_premium_pools()`](api.py) — current Rysk Premium catalogue.
- [`fetch_rysk_premium_snapshots()`](api.py) — paginated snapshot iterator for
  one pool.
- [`RyskHistoricalContextStore`](historical_context.py) — durable raw snapshot
  storage and final-epoch selection.
- [`fetch_and_store_rysk_premium_history()`](historical_context.py) — history
  prefill for a set of pools.
- [`RyskPremiumHistoricalReader`](historical.py) — adapter from final epoch PPS
  to the common historical-read API.
- [`fetch_and_sync_rysk_premium_catalogue()`](vault_sync.py) — metadata database
  reconciliation.

The shared interfaces implemented by the adapter are documented in
[`VaultBase`](../../../vault/base.py), while protocol selection is wired in
[`classification.py`](../../classification.py). The generated Sphinx entry is
under [`docs/source/api/rysk`](../../../../docs/source/api/rysk/index.rst).

## External API stability

The catalogue and snapshot endpoints are public application APIs rather than a
versioned developer API. The client validates response shapes and identifiers
and raises `RyskPremiumAPIError` when the service returns malformed or
incompatible data. Callers should treat endpoint availability and schema as an
external dependency.
