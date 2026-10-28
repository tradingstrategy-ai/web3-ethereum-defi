# Utilisation and available liquidity metrics for lending vaults

The lending adapters expose `fetch_available_liquidity()` in denomination-token
units (`Decimal`) and `fetch_utilisation_percent()` as a fraction (`Percent`,
0.0–1.0). Either can return `None` when an observation is unavailable. Zero is a
valid observation and must not be treated as missing data.

## Meaning of the metrics

For single-market adapters such as Llama Lend, Euler EVK, Gearbox and Silo,
utilisation measures borrowing against supplied assets. Available liquidity
measures the market's remaining lender liquidity. A particular user's withdrawal
can also depend on their share balance, protocol restrictions and rounding.

For Euler Earn, Morpho and IPOR, the current implementation measures the fraction
allocated outside the vault and reports only its idle denomination-token balance.
Underlying markets may have additional redeemable assets. These metrics therefore
measure capital deployment and idle liquidity, rather than aggregate borrower
utilisation or total redeemable capacity. See
[redeemable liquidity versus utilisation](README-vault-redeemable.md).

`maxRedeem(address(0))` cannot measure market-wide liquidity: ERC-4626 limits the
result to the owner's shares, and the zero address normally owns none.

## Sources used by the adapters

| Adapter | Available liquidity source | Utilisation calculation |
|---------|----------------------------|-------------------------|
| Gearbox | `availableLiquidity()` | `totalBorrowed / (totalBorrowed + availableLiquidity)` |
| Euler EVK | `cash()` | `totalBorrows / (cash + totalBorrows)` |
| Silo | `getLiquidity()` | `getDebtAssets() / getCollateralAssets()` |
| Llama Lend V1/V2 | `max(totalAssets() - controller.total_debt(), 0)` | `min(controller.total_debt() / totalAssets(), 1)` |
| Euler Earn, Morpho V1/V2, IPOR, Fluid | `asset().balanceOf(vault)` | `(totalAssets - idle) / totalAssets` |

The idle-balance formula describes these adapters' implementation; it does not
establish that every protocol stores all withdrawable assets in its vault.

## Llama Lend accounting

Llama holds unborrowed lending tokens in its controller. Reading the token
balance of the vault itself generally reports zero and incorrectly implies full
utilisation.

The [V1 vault accounting](https://docs.curve.finance/developer/lending/contracts/vault/)
adds the controller's token balance to outstanding debt. The
[V2 vault accounting](https://docs.curve.finance/developer/llamalend-v2/vault/)
adds its tracked available balance to debt and deducts accrued protocol fees.
Both versions therefore support the same calculation:

```text
available_liquidity = max(totalAssets - controller.total_debt, 0)
utilisation = min(controller.total_debt / totalAssets, 1)
```

An empty vault reports zero utilisation. The floor and cap handle protocol fees
exhausting available lender liquidity. These protocol deductions are already in
NAV; reporting zero separate management and performance fees does not mean the
market has no protocol fees. An instant withdrawal period means no scheduled
waiting period, subject to sufficient market liquidity.

Spot queries resolve dynamic block tags once per observation. Historical reads
collect vault assets and controller debt at the same sampled block, with the debt
call attributed to the vault. Failed or malformed assets/debt responses leave
both lending metrics missing and produce diagnostics.

## Reading and recording data

Use an explicit block to compare the two spot methods at the same state:

```python
block = web3.eth.block_number
available = vault.fetch_available_liquidity(block)
utilisation = vault.fetch_utilisation_percent(block)
```

Llama also exposes `available, utilisation = vault.fetch_lending_metrics(block)`
to read both values from one assets/debt pair. The metadata scanner uses this
combined getter when provided by an adapter and keeps the separate-method path
for other lending adapters.

Lending protocols are identified by
`eth_defi.erc_4626.core.is_lending_protocol(vault.features)`. Historical adapters
construct protocol-specific multicalls through `vault.get_historical_reader()`.
For Llama, the extra call is `total_debt`, targeted at the controller.

The metadata scanner stores spot observations in `_available_liquidity` and
`_utilisation`. Historical rows carry `available_liquidity` and `utilisation` in
`VaultHistoricalRead`, exported through the existing Parquet schema. Missing
historical metrics export as missing values, rather than fabricated zeros.

Deploying the corrected adapter affects subsequent observations. It does not
repair historical Llama price rows. The metadata refresh version is bumped so
the next enabled-chain metadata scan regenerates stored spot metrics instead
of reusing its bounded cache. Preserve price history and reader state;
any historical repair requires a separate, deliberate backfill. Normal historical
scans retain their existing sampling and sparse-row policy, so they do not record
every utilisation change.
Published tables that prefer the latest historical row over metadata retain
that row's old metrics until a new historical observation is retained.

## Focused tests

The following unit tests run without RPC credentials:

```bash
source .local-test.env && PYTHONPATH="$PWD" poetry run pytest tests/erc_4626/vault_protocol/test_llama_lend_utilisation.py -k 'not real_withdrawal'
```

The complete focused check also includes the shared Arbitrum V1 fork and the
real Optimism V2 archive regression:

```bash
source .local-test.env && PYTHONPATH="$PWD" poetry run pytest tests/erc_4626/vault_protocol/test_llama_lend.py tests/erc_4626/vault_protocol/test_llama_lend_utilisation.py
```

`JSON_RPC_ARBITRUM` enables the V1 fork test. `JSON_RPC_OPTIMISM` enables V2 tests
at blocks 158341301 and 158341302, immediately before and after the 8,910 USDC
withdrawal on 17 October 2026. They verify spot reads, historical multicalls,
exported values and scanner metadata against fixed expected values. Missing
credentials skip the respective integration test; they do not count as a pass.
The vault-protocol CI workflow accepts both RPC variables as repository secrets.
An RPC provider must be able to execute the requested historical state; a
provider's erroneous revert is a failed integration check, not zero liquidity.
