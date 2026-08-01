# Asseto tokenised funds

Asseto products are permissioned tokenised fund shares. They are represented by
`AssetoVault`, a read-only `VaultBase` adapter, rather than ERC-4626 because
subscriptions and redemptions are KYC-gated request/claim workflows and NAV is
published independently of an ERC-4626 conversion function.

## Registry and metadata

Asseto's public web application exposes undocumented endpoints at
`/api/home/products`, `/api/product/get` and `/api/product/price/list`. They
are useful for product identity, description, denomination and daily displayed
NAV history, but are not a versioned API. Onchain token supply and a published
`Pricer`, when present, remain canonical valuation inputs.

The scheduled `Asseto` row fetches the registry once per due daily cycle before
constructing adapters. A normalised JSON snapshot is stored at
`~/.tradingstrategy/cache/asseto/registry-cache.json`, shared by the scanner
containers. A failed, empty or duplicate response cannot overwrite this cache.
For up to seven days the scanner may use a stale snapshot only to reconstruct
already-known adapters; it never writes stale descriptions or registers a new
product. The dashboard diagnostic reports `registry=fresh` or `registry=stale`.

Fresh data is preferred field by field: reviewed corrections, current detail
description, current registry values, then onchain state. The only maintained
product-specific override is AoABT's documentation link; other products fall
back to the Asseto product index. Curator mappings remain reviewed constants
because Asseto publishes partner logos rather than a stable textual curator id.

## Scheduled and manual history

The Asseto feed samples approximate daily (`1d`) history. Each token is scanned
and rewritten independently, so a newly registered old deployment cannot widen
another product's Parquet deletion window. A fresh supported EVM product is
registered into the vault database from the same registry snapshot before its
first price scan. Products without a denomination, an RPC or usable NAV source
remain visible through diagnostics and do not claim a fresh price.

`scripts/backfill-tokenised-funds.py` uses the same persisted registry for
Asseto metadata and history. For an address-scoped repair, use
`scripts/erc-4626/backfill-tokenised-fund-prices.py` with
`TOKENISED_FUND_PROTOCOLS=asseto`; it shares the recurring price writer and
preserves all other products' rows.

USD, USDC and USDT are already USD accounting denominations. Other
denominations require historical rates from the shared currency-rate database;
no synthetic 1.0 FX rate is used. Asseto `stoken` products without an explicit
denomination are treated as USD only where the existing integration explicitly
recognises that product type.

## Operations

The current stale-registry limit is seven days. The first failed registry read
retains a valid cache; if no usable snapshot exists, the Asseto item fails
without writing metadata or raw prices while later tokenised-fund protocols
continue.

Use `DRY_RUN=true PROTOCOLS=asseto poetry run python
scripts/backfill-tokenised-funds.py` to inspect a registry-based backfill. The
registry cache is adapter-critical state and should be included with vault
metadata and raw-price backups.
