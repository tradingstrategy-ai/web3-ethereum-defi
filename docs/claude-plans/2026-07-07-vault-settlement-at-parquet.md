# Generic vault settlement data for historical backtests

## Why

Phase-aware backtests need to know when asynchronous vault settlement cycles
happened. Lagoon vaults can report `maxDeposit(address(0)) == 0` in historical
ERC-4626 state reads, but real settlement events still happened during those
periods.

The settlement event stream should be kept separate from raw vault price data.
Raw price parquet rows are scanner snapshots; settlement transactions are sparse
events.

## Storage model

Store settlement transactions in a separate DuckDB database:

```text
vault-settlements.duckdb
```

Table:

```text
vault_settlements
```

Schema:

```text
chain_id     INTEGER
address      VARCHAR
block_number BIGINT
protocol     VARCHAR
block_hash   VARCHAR
timestamp    TIMESTAMP
tx_hash      VARCHAR
event_name   VARCHAR
inserted_at  TIMESTAMP
```

Rows are idempotently upserted by logical key:

```text
(chain_id, address, tx_hash)
```

No DuckDB primary key is required; use delete-then-insert transactions to avoid
ART index stability issues seen elsewhere in the project.

Multiple settlement transactions in the same block are valid separate rows. Do
not deduplicate by ``block_number``.

## Generic interface

Add:

```text
eth_defi/vault/settlement_data.py
```

Responsibilities:

- Open/create the DuckDB database.
- Create `vault_settlements`.
- Insert settlement rows idempotently.
- Query settlement rows as a DataFrame.
- Annotate a price DataFrame with a nullable `vault_settlement_at` column.

`vault_settlement_at` is not written to raw price parquet. It is added to the
in-memory raw price DataFrame before cleaning.

Annotation semantics per `(chain, address)`:

```text
vault_settlement_at = latest settlement timestamp in
                      (previous_raw_price_timestamp, current_raw_price_timestamp]
```

For the first raw price row, include settlements up to the first timestamp.

`NaT` means no settlement is known for that raw price interval.

## Lagoon producer

Add:

```text
eth_defi/erc_4626/vault_protocol/lagoon/settlement.py
```

Responsibilities:

- Read Lagoon `SettleDeposit` and `SettleRedeem` logs.
- Store settlement logs as generic `VaultSettlement` rows.
- Ignore valuation-only `TotalAssetsUpdated` transactions.
- Convert events to generic `VaultSettlement` rows.
- Store rows through `VaultSettlementDatabase`.

Use Hypersync when available and JSON-RPC `eth_getLogs` as fallback.

## Production settlement scan

Populate `vault-settlements.duckdb` before `clean_prices()` runs.

Initial production support was Lagoon-only, but the storage and annotation code
is protocol-generic and now covers Lagoon and D2 Finance. The scan step should:

- Select supported vaults from the vault metadata database and intersect them
  with vaults present in the raw price parquet.
- For each `(chain_id, address)`, choose the scan range from raw price data:
  start at the greater of the first raw price block and the latest stored
  settlement block plus one, and end at the latest raw price block for that
  vault.
- Allow an operator-forced backfill range for historical repairs. Overlapping
  scans are acceptable because inserts are idempotent by
  `(chain_id, address, tx_hash)`.
- Run as part of each successful EVM chain scan cycle before cleaned price
  generation.
- Query all supported vault addresses on the chain as one event-reader batch,
  chunked by block range for the JSON-RPC fallback, then filter returned logs
  back to each vault's incremental block range.
- Treat settlement scan failures as non-fatal: log the failed chain batch,
  show it in the scanner dashboard, and continue the scanner cycle using the
  previously stored `vault-settlements.duckdb` data.

The first implementation can expose this as a small helper called by the vault
pipeline instead of embedding Lagoon event details in the wrangling module.

## Cleaning pipeline

Do not change `VaultHistoricalRead` or the raw price parquet schema.

Modify the cleaning entry point:

```text
eth_defi.research.wrangle_vault_prices.generate_cleaned_vault_datasets()
```

Flow:

```text
read vault-prices-1h.parquet
read vault-settlements.duckdb if present
merge vault_settlement_at into the in-memory DataFrame
run existing cleaning
write cleaned-vault-prices-1h.parquet
```

Add `vault_settlement_at` to `CleanedVaultPriceRow` and `VAULT_STATE_COLUMNS`
so cleaned output always has the column, even if the settlement DB is missing.

Row-level cleaners must not silently discard settlement information. If a raw
row has a non-null `vault_settlement_at` and the vault survives cleaning, that
settlement marker must either:

- remain on the corresponding cleaned row, or
- be carried forward to the next surviving cleaned row for the same
  `(chain_id, address)`.

Whole-vault filters may still remove settlement data together with the vault,
for example when the vault is out of scope for the cleaned dataset.

## Pipeline integration

Thread the settlement DB path through:

- `eth_defi.vault.post_processing.clean_prices()`
- `eth_defi.vault.post_processing.run_post_processing()`
- `scripts/erc-4626/post-process-prices.py`
- `eth_defi.vault.scan_all_chains.run_pipeline()`

Add `vault-settlements.duckdb` to:

- pipeline backup files
- data-file export list

DuckDB durability must be explicit before copying or uploading the database.
Settlement writers should call `save()`/`CHECKPOINT` and close the connection
before backup/export code copies `vault-settlements.duckdb`, or the pipeline
must deliberately copy the WAL/checkpoint state as one consistent unit.

## Tests

Use synthetic, offline tests:

- DuckDB insert/upsert behaviour.
- Price DataFrame interval annotation.
- Boundary behaviour for previous/current row timestamps.
- Multiple settlement transactions in one block are stored as separate rows.
- `generate_cleaned_vault_datasets()` output when the settlement DB is absent.
- `generate_cleaned_vault_datasets()` output when a synthetic settlement DB has
  matching rows.
- Cleaning behaviour that would otherwise drop a row carrying
  `vault_settlement_at`.
- Pipeline helper behaviour for selecting scan ranges from raw price rows and
  existing settlement rows.
- Checkpoint/close behaviour before backup/export.

Live Hypersync/RPC tests can be added separately but should not be required for
normal CI.
