# Vault redeemable liquidity vs utilisation

This document explains why the current utilisation metric does not mean
"how much can be redeemed right now" for multi-market vaults like Morpho
and IPOR, and what alternatives exist.

## What utilisation measures today

Our pipeline computes utilisation as:

```
utilisation = (totalAssets - idle) / totalAssets
```

where `idle = asset().balanceOf(vault_address)` — the denomination
tokens sitting unallocated inside the vault contract itself.

This is a measure of **capital deployment efficiency**: how much of the
vault's AUM the curator has put to work.  For single-market lending
vaults (Euler EVK, Gearbox, Silo) this also happens to equal the ratio
of assets that are locked in active loans, so
`available_liquidity = idle` is a fair proxy for "instantly redeemable".

See `README-utilisation.md` for the full utilisation API.

## Why utilisation is misleading for Morpho and IPOR

### Morpho V1 (MetaMorpho)

A MetaMorpho vault spreads its assets across a **withdraw queue** of
Morpho Blue markets.  Each market is an independent lending pool with
its own borrowers and suppliers:

```
MetaMorpho vault
├── idle (USDC sitting in the vault contract)
├── Market A  →  totalSupplyAssets: 10M, totalBorrowAssets: 7M  →  3M redeemable
├── Market B  →  totalSupplyAssets: 5M,  totalBorrowAssets: 1M  →  4M redeemable
└── Market C  →  totalSupplyAssets: 8M,  totalBorrowAssets: 8M  →  0  redeemable
```

When a user redeems, MetaMorpho iterates the withdraw queue and pulls
liquidity from each market until the redemption is filled.
**What is actually redeemable is `idle + Σ(supplyAssets − borrowAssets)`
across all markets**, not just the idle balance.

A vault can show 95% utilisation (only 5% idle) but have 40% of its
deployed capital sitting in low-utilisation markets that can be withdrawn
instantly.  Conversely, a vault could have low "utilisation" if the
curator recently reallocated but the underlying markets are fully
borrowed.

### Morpho V2 (adapter-based)

Same fundamental problem.  V2 wraps the allocation in adapters rather
than raw Morpho Blue market IDs, but each adapter ultimately deploys
into one or more yield sources.  Idle assets are only a fraction of what
the vault can actually redeem.

### IPOR (Plasma Vault)

IPOR deploys capital through **fuses** — pluggable strategy modules that
integrate with external protocols (Aave, Compound, Morpho, etc.).
The vault exposes `getInstantWithdrawalFuses()` returning the set of
fuses that *support* instant withdrawal, but the fuse interface
(`IFuseInstantWithdraw`) only provides the action
`instantWithdraw(bytes32[])` — there is **no view function to query how
much each fuse can return**.

The idle balance is therefore an undercount: the fuses can potentially
unwind their positions instantly, but the vault provides no on-chain read
path to find out how much without simulating the withdrawal.

### Summary of the gap

| Protocol | What `idle` captures | What is actually redeemable |
|----------|---------------------|-----------------------------|
| Euler EVK | `cash()` = full available liquidity | Same — single lending pool |
| Gearbox | `availableLiquidity()` = full available | Same — single lending pool |
| Silo | `getLiquidity()` = full available | Same — single lending pool |
| **Morpho V1** | Only unallocated idle | idle + per-market available liquidity across withdraw queue |
| **Morpho V2** | Only unallocated idle | idle + per-adapter available liquidity |
| **IPOR** | Only unallocated idle | idle + whatever instant withdrawal fuses can return |

For single-market vaults (Euler, Gearbox, Silo), idle *is* the redeemable amount.
For multi-market vaults (Morpho, IPOR), idle is a **lower bound**.

## Ways to get the actual redeemable amount

### ERC-4626 `maxWithdraw(owner)` / `maxRedeem(owner)`

The ERC-4626 standard defines `maxWithdraw(address owner)` which should
return the maximum assets the given owner can withdraw in a single
transaction.  For MetaMorpho V1 this is correctly implemented — it
iterates the withdraw queue and sums market-level liquidity, capped by
the owner's share balance.

**Limitation:** requires a real depositor address.  Calling with
`address(0)` returns 0 because `balanceOf(address(0)) == 0`.  To get
the vault-wide redeemable capacity you would need to use the vault's
total share supply or a known large holder.

### Morpho V1: query underlying Morpho Blue markets

The most reliable on-chain approach for MetaMorpho V1.  Morpho Blue
exposes a public `market(Id)` mapping returning the full market state:

```solidity
struct Market {
    uint128 totalSupplyAssets;
    uint128 totalBorrowAssets;
    uint128 totalSupplyShares;
    uint128 totalBorrowShares;
    uint64  lastUpdate;
    uint16  fee;
}
```

MetaMorpho exposes the withdraw queue as a public array:

```solidity
Id[] public withdrawQueue;   // ordered list of market IDs
```

The redeemable amount per market is:

```python
available_per_market = min(
    market.totalSupplyAssets - market.totalBorrowAssets,
    loan_token.balanceOf(morpho_blue_address),
)
```

Total redeemable = `idle + Σ available_per_market` across the withdraw queue.

**What is needed:** a minimal Morpho Blue ABI with the `market(bytes32)` view
function, plus the MetaMorpho `withdrawQueue(uint256)` and
`withdrawQueueLength()` accessors.  Both contracts are publicly verified
on Etherscan.

### Morpho V2: iterate adapters and their underlying markets

V2 vaults expose:

- `adaptersLength()` → count of adapters
- `adapters(index)` → adapter address
- `liquidityAdapter()` → special adapter used for liquidity routing

Each adapter (e.g. `MorphoMarketV1AdapterV2`) exposes:

- `realAssets()` → current value of the adapter's position
- `marketIds()` → underlying Morpho Blue market IDs
- `expectedSupplyAssets(marketId)` → supply allocated per market

To compute redeemable liquidity you would iterate adapters, then for
each adapter query the underlying Morpho Blue markets the same way as
V1.  This is more involved but follows the same pattern.

### IPOR: no on-chain read path

IPOR does not expose per-fuse withdrawal capacity.  The options are:

1. **Accept idle as a lower bound.** This is what we do today.
2. **Simulate withdrawal via `eth_call`.** Call `redeem(totalSupply, ...)` as
   a static call and observe the revert or returned amount.  Fragile and
   expensive for production scanning.
3. **Query underlying protocols directly.** If we know a fuse wraps Aave V3,
   read Aave's available liquidity for that asset.  Requires maintaining
   a fuse → underlying protocol mapping.
4. **Use `totalAssets()` as an upper bound.** Every asset the vault reports
   is theoretically redeemable, but subject to underlying protocol liquidity
   and redemption delays.

None of these are clean.  For IPOR, idle remains the pragmatic choice
until IPOR adds a view function to their fuse interface.

## Recommended approach for the pipeline

For protocols where we can compute true redeemable liquidity without
excessive RPC calls:

1. **Euler EVK, Gearbox, Silo** — already correct (single-market, dedicated
   liquidity functions).
2. **Morpho V1** — add `withdrawQueue` iteration + Morpho Blue `market(id)`
   queries.  Requires N+1 calls per vault where N is the queue length
   (typically 3-10 markets).
3. **Morpho V2** — same approach through adapter indirection, higher call
   count.
4. **IPOR** — keep idle as lower bound, document the limitation.

For the historical reader pipeline, these extra calls would be added to
`construct_utilisation_calls()` / `construct_multicalls()` on each
protocol's `VaultHistoricalReader` subclass, following the same pattern
as `cash()` and `totalBorrows()` on Euler EVK.
