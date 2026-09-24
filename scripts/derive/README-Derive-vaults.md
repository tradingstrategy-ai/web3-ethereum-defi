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
sample, not the vault's full history. It also checks the full response for the
first selected vault. `DERIVE_V3_NETWORK` accepts
`mainnet`, `testnet` or `both` (the default). Use `VAULT_IDS` with comma-separated
IDs from a current listing to narrow the inspection.

## Collect observations

```shell
DERIVE_V3_NETWORK=testnet poetry run python scripts/derive/scan-v3-vaults.py
DERIVE_V3_NETWORK=mainnet poetry run python scripts/derive/scan-v3-vaults.py
```

The standalone scanner stores deployments separately at
`~/.tradingstrategy/vaults/derive-v3-{network}-vaults.duckdb`. Set `DB_PATH` for
an isolated file or `VAULT_IDS` for a comma-separated ID filter. Repeated runs
replace matching timestamps and retain older points. A failed vault fetch
leaves that vault's earlier data intact. The DuckDB stores the full source
metadata record alongside parsed fields.

**Mainnet** vaults are added to the shared catalogue and price pipeline by
default in `scan-vaults-all-chains.py`. Set `SCAN_DERIVE_V3=false` to skip them.
The all-chain scanner always uses the mainnet API and the
mainnet DuckDB. Post-processing can be run separately with
`MERGE_DERIVE_V3=true` in `post-process-prices.py`. Existing price rows are
kept when the API returns a shorter series, and an empty listing does not
remove existing metadata.

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

## Source limits

On 24 September 2026 the public mainnet listing contained no vaults and
testnet listed 18. These counts can change; use the inspector for the current
listing.

The [fee guide](https://docs.derive.xyz/vaults/fees) describes management and
performance fees settled through dilutive share mints. Per-vault basis points
are converted to fractions for shared metadata. The historical sampled price
can differ from the live simulated price, which includes a hypothetical fee
settlement. Curator-managed [deposit and withdrawal requests](https://docs.derive.xyz/vaults/deposits-withdrawals)
are subject to settlement and cooldown; a request is not a completed flow.
The `whitelist_only` field describes account approval; a closed vault is
reported separately as closed to deposits.

No stable public per-vault web route has been verified; the catalogue links to
the Derive v3 app landing page while mainnet has no listed vaults. Derive
vaults can trade options, spot and perpetuals or lend; only a vault with
reviewed mainnet evidence receives a strategy tag and the perp DEX account
observation. No mainnet vault has been classified yet.

The public vault record supplies a curator wallet but no verified organisation
identity. The wallet is retained under `other_data.derive.curator`. A vault
name alone does not establish who controls that wallet, so `curator_slug`,
`curator_name` and a top-level curator record remain absent until a mainnet
vault's curator identity is verified and mapped to an existing curator record.
Testnet vaults are not manually tagged or attributed in the shared catalogue.

### Position transparency shortcoming

Derive v3 does not expose current vault positions through its public API. This
is a protocol transparency shortcoming: an independent observer can see NAV
and share-price history but cannot calculate a vault's gross long and short
exposure, position concentration, or options risk from those values. The
public vault action history records vault settlement events, not the trading
positions needed to reconstruct exposure.

The [v3 API specification](https://docs.derive.xyz/openapi.json) provides
`private/get_positions` and `private/get_subaccount` for authenticated
wallet sessions. A curator must grant access to the vault subaccount, for
example with a [read-only session key](https://docs.derive.xyz/authentication/access-scopes)
restricted to that subaccount. Until such access is available and verified,
exposure and concentration remain **unknown (null)**, never zero.
