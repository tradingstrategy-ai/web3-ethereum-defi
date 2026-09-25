# Derive v3 native vaults

Derive v3 vaults are managed exchange subaccounts with native shares. They are
not ERC-4626 contracts. The [Derive vault guide](https://docs.derive.xyz/vaults/create-a-vault)
describes curator control, deposit assets and share accounting. Public v3 API
endpoints list vaults and return historical performance without credentials.

## Inspect public data

```shell
DERIVE_V3_NETWORK=both poetry run python scripts/derive/inspect-v3-vaults.py
DERIVE_V3_NETWORK=testnet HISTORY_SAMPLE_LIMIT=5 poetry run python scripts/derive/inspect-v3-vaults.py
```

The inspector is read-only. It reports vault IDs, deposit assets, fees, access
settings, USD NAV and share price, and the latest `HISTORY_SAMPLE_LIMIT`
performance points for each vault. The displayed date range covers only that
sample. For the first selected vault it also calls `public/get_vault` and
prints the response field names; it does not validate their values.
`DERIVE_V3_NETWORK` accepts
`mainnet`, `testnet` or `both` (the default). Use `VAULT_IDS` with comma-separated
IDs from a current listing to narrow the inspection.

## Collect observations

```shell
DERIVE_V3_NETWORK=testnet poetry run python scripts/derive/scan-v3-vaults.py
DERIVE_V3_NETWORK=mainnet poetry run python scripts/derive/scan-v3-vaults.py
```

The standalone scanner defaults to testnet. It stores each deployment at
`~/.tradingstrategy/vaults/derive-v3-{network}-vaults.duckdb`. Set `DB_PATH` to
choose another file, or `VAULT_IDS` to scan specific IDs from the listing.

Each run fetches all available daily history for the selected vaults. There
is no incremental cursor. Storage replaces points with matching timestamps
and retains every other stored point. Metadata and history commit together
for each vault. A failed fetch stops the scan: the failing vault keeps its
previous data, and vaults already committed remain saved.

The DuckDB tables are:

| Table | Contents | Row identity |
|-------|----------|--------------|
| `vault_metadata` | Latest listing record, resolved deposit asset and original JSON; `observed_at` retains the first scan time | `network`, `subaccount_id` |
| `vault_prices` | Daily share price, NAV, share supply and write time | `network`, `subaccount_id`, `timestamp` |

Amounts are stored as decimal strings to retain API precision. Timestamps
are naive UTC. Both tables retain data for vaults absent from a later listing.

## Run in the shared scanner

**Mainnet** vaults are added to the shared catalogue and price pipeline by
default in `scan-vaults-all-chains.py`. Set `SCAN_DERIVE_V3=false` to skip them.
The all-chain scanner uses the mainnet API and stores its DuckDB under
`PIPELINE_DATA_DIR` (default `~/.tradingstrategy/vaults`). The scheduled item
is `Derive V3`; set its interval with, for example, `SCAN_CYCLES='Derive V3=24h'`.
An empty listing succeeds with zero vaults and price rows. Testnet rows are
excluded from shared metadata and prices.

Post-processing can be run separately with
`MERGE_DERIVE_V3=true` in `post-process-prices.py`. Existing price rows are
kept when the API returns a shorter series, and an empty listing does not
remove existing metadata. When merging prices, a new row replaces an existing
row only if both its vault address and timestamp match.

## Interpret the exported data

Derive's `subaccount_id` is the native vault ID. Shared exports use synthetic
chain ID `9993` and address `derive-v3-vault-{subaccount_id}`. Historical
`share_price` and `nav` are USD values; the accepted deposit token is kept in
a separate metadata field. The live simulated share price is not used as a
historical sample. The shared USD denomination uses a fixed 1:1 rate. The
[performance history API](https://docs.derive.xyz/api-reference/vault-shareholders/publicget_vault_performance_history)
supports 1-hour, 8-hour, daily and weekly samples; this collector reads daily
points and accepts response timestamps in seconds or milliseconds. Pagination
uses the API's exclusive `to` bound in Unix seconds.
The shared `First seen` date is the earliest stored performance point, or the
first scan when no history exists; it is not a verified vault creation date.

## Availability, fees and settlement

On 24 September 2026 the public mainnet listing contained no vaults and
testnet listed 18. These counts can change; use the inspector for the current
listing.

The [fee guide](https://docs.derive.xyz/vaults/fees) describes fees paid by
issuing shares to the curator and protocol, reducing other holders' share of
the vault. Fee rates are converted from basis points to fractions for shared
metadata: 100 basis points becomes `0.01`. The live simulated price assumes
pending fees have been settled; historical samples reflect settlements when
they occur.

Curator-managed [deposit and withdrawal requests](https://docs.derive.xyz/vaults/deposits-withdrawals)
are subject to settlement and cooldown; a request is not a completed flow.
`whitelist_only` controls which accounts can deposit. The `closed` flag is
exported separately as the reason deposits are closed. Neither an open vault
nor an approved account guarantees immediate settlement.

## Strategy tags and curator attribution

No stable public per-vault web route has been verified; the catalogue links to
the Derive v3 app landing page. Derive vaults can trade options, spot and
perpetuals or lend. Record reviewed mainnet strategies in
`eth_defi/derive/tags.py`, using the synthetic vault address and a source for
the classification. The mapping is currently empty. Only vaults tagged
`perpetual_futures` receive the perp DEX flag and an account observation with
USD equity and unavailable position data.

The public vault record supplies a curator wallet but no verified organisation
identity. The wallet is retained under `other_data.derive.curator`. A vault
name alone does not establish who controls that wallet, so `curator_slug`,
`curator_name` and a top-level curator record remain absent until a mainnet
vault's curator identity is verified and mapped to an existing curator record.
Testnet vaults are not manually tagged or attributed in the shared catalogue.

## Position transparency shortcoming

Derive v3 does not expose current vault positions through its public API. This
is a protocol transparency shortcoming: an independent observer can see NAV
and share-price history but cannot calculate a vault's gross long and short
exposure, position concentration, or options risk from those values. The
public vault action history records vault settlement events, not the trading
positions needed to reconstruct exposure. This collector therefore cannot
provide the position breakdown or gross exposure available for Hyperliquid vaults.

The [v3 API specification](https://docs.derive.xyz/openapi.json) provides
`private/get_positions` and `private/get_subaccount` for authenticated
wallet sessions. A curator must grant access to the vault subaccount, for
example with a [read-only session key](https://docs.derive.xyz/authentication/access-scopes)
restricted to that subaccount. This integration does not implement private
position collection, and vault-specific session access has not been tested.
Exposure and concentration are exported as **unknown (null)**. A null value
does not mean that the vault has no positions.
