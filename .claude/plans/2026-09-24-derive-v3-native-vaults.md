# Derive v3 native vault plan

Implemented in PR #1597. This file records the scope and checks; use
`scripts/derive/README-Derive-vaults.md` for current operator instructions.

## Scope

Add Derive v3 native vaults using the same API → DuckDB → shared vault metadata
and prices path as GRVT, Lighter, Hibachi and ApeX. The production all-chain
loop reads **mainnet only**. A manual command inspects mainnet and testnet.

Keep Derive v2 authentication and data separate. On 24 September 2026 the
public testnet API listed 18 vaults, while mainnet listed none. An empty
mainnet listing must succeed without removing stored data.

## 1. Operator README

Write `scripts/derive/README-Derive-vaults.md` using the GRVT, Lighter,
Hibachi and ApeX vault READMEs as examples. Explain the public endpoints,
native vault IDs, deposit assets, USD NAV/share-price units, fees and curator
settlement, daily history, unavailable position data, and the commands for
inspection and scanning. Document known mainnet/testnet availability and link
the README from the Derive package and API documentation. Add API stubs for
new modules. For a feature PR, use the `feat:` title prefix and add a dated
`CHANGELOG.md` entry.

## 2. Manual inspection

Add `scripts/derive/inspect-v3-vaults.py`. Select `mainnet`, `testnet` or
`both` with an environment variable; default to both. Use the existing v3
public client to list vaults, resolve deposit assets and show a small history
sample. Report counts, IDs, names, fees, access status, NAV, share price,
sampled history timestamps and missing history in `tabulate` tables. A
mainnet listing with zero vaults is a valid result. The command must not write
to the production DuckDB, shared pickle or Parquet. Inspect one full-vault
response to confirm fields that the listing omits.

## 3. Derive storage and tagging

Reuse `eth_defi/derive/v3_vaults.py` and `v3_vault_metrics.py`. Keep separate
mainnet and testnet DuckDB files under `~/.tradingstrategy/vaults/`. Reuse the
existing HTTP session unless the v3 API needs different retry or rate-limit
settings; never send v2 authentication to public v3 endpoints. Retain source
decimal values and complete history, and make repeated scans update matching
timestamps without deleting older points. A failed fetch must leave prior
data intact. Keep bulk DuckDB tables free of `PRIMARY KEY`
and `UNIQUE` constraints, as required by the repository's DuckDB guidance.

Add `eth_defi/derive/tags.py` for reviewed mainnet strategy tags, with a source
for each classification. Do not tag testnet vaults. Strategies may
include options, spot and lending. Add the native feature and protocol
metadata needed by the shared catalogue. For vaults classified as perp DEX
vaults, follow GRVT and Hibachi's shared account-observation schema: publish
USD equity with unavailable positions and null exposure, and register the
matching capability. Do not give every Derive vault a perp DEX flag.

## 4. Mainnet all-chain integration

Create a Derive export adapter following the existing native vault adapters.
Use the vault `subaccount_id` as its stable identity in DuckDB, metadata and
prices; use synthetic chain ID `9993` and address
`derive-v3-vault-{subaccount_id}`. Check 9993 against current chain/Parquet
data, then register it in the chain metadata and native scanner mappings.
Map deposit token metadata, fee rates, share supply, NAV, access restrictions
and source description into `VaultRow`.
Retain the curator wallet without inferring an organisation from the vault
name. Link to the app landing page until a per-vault route is verified.
Map cooldown and deposit permission; convert
fee basis points to fractions and set the fee mode to match Derive's
share-minting settlements. Register the native feature/name, synthetic
address prefix, special offchain-vault slug, fee/risk matrix entries, protocol
metadata YAML and logo, as GRVT and Hibachi do.
Use historical `share_price` and `nav` for prices, not the live simulated
price. Verify whether these historical values are USD and keep USD price
units distinct from the deposit token; resolve any common-schema mismatch
before export. Normalise API timestamps supplied in seconds or milliseconds.
Leave unavailable positions/exposure null, and add no price rows for a vault
without history.

Add `SCAN_DERIVE_V3` to `eth_defi/vault/scan_all_chains.py` as one scheduled
native item. Its wrapper must use the canonical mainnet API and mainnet
DuckDB only; testnet must not enter the shared pickle or Parquet. Include the
flag and cycle in the scanner script and Compose configuration. Scanning is
enabled by default; `SCAN_DERIVE_V3=false` disables it.

Extend native post-processing to merge Derive prices. Preserve existing
history when the API or local DuckDB has fewer rows: for each vault and
timestamp, use the new value when present and retain older unmatched rows.
An empty mainnet listing or missing DuckDB must leave existing shared data
untouched; retain vaults that disappear from a later listing. Wire the merge
through both the all-chain runner and `post-process-prices.py` with a
`MERGE_DERIVE_V3` switch, and add the mainnet Derive DuckDB to scanner backups.
Use the existing scanner lock and close database connections after each scan.

## Verification

- Run the manual inspector against both public deployments. Keep a live
  testnet integration test for a real vault history response and a mainnet
  listing test while mainnet is empty.
- Test timestamp units, pagination, idempotent DuckDB ingestion, testnet
  exclusion, vault metadata and price export, and preservation of older
  Parquet rows after a shorter scan. While mainnet is empty, run the export
  path against testnet-shaped fixtures and temporary shared outputs; verify
  USD units before exporting real mainnet prices.
- Test the all-chain flag and an empty mainnet scan, then run focused pytest
  with `.local-test.env` and Ruff. With temporary files, check that Derive
  rows survive cleaning and reach the combined JSON with correct null account
  metrics. Record the real integration result in the feature PR as required
  by the repository instructions.
