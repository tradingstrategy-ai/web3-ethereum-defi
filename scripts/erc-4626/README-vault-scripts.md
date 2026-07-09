# ERC-4626 vault scripts

Scripts for discovering, scanning, analysing, and debugging ERC-4626 vault data.

All scripts use environment variables for configuration.
Run with `poetry run python scripts/erc-4626/<script>.py`.

## Production pipeline

These scripts form the core data pipeline for vault discovery, price scanning, and export.

### scan-vaults.py

Discovery scan for ERC-4626 vaults on a single chain. Stores metadata in the vault database.

```shell
JSON_RPC_URL=$JSON_RPC_BASE poetry run python scripts/erc-4626/scan-vaults.py
```

For Robinhood Chain, set `JSON_RPC_ROBINHOOD` to an archive-capable provider
endpoint and pass it through as the single-chain `JSON_RPC_URL`:

```shell
LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_ROBINHOOD poetry run python scripts/erc-4626/scan-vaults.py
```

| Variable | Description |
|----------|-------------|
| `JSON_RPC_URL` | Required. RPC endpoint for the chain. |
| `LOG_LEVEL` | Optional. Default: WARNING. |
| `MAX_GETLOGS_RANGE` | Optional. Max block range for getLogs. |
| `SCAN_BACKEND` | Optional. Event reader backend (`auto`, `hypersync`, `rpc`). |
| `END_BLOCK` | Optional. Stop scanning at this block. |
| `RESET_LEADS` | Deprecated. Historical whole-chain lead rediscovery from block 1. Existing vault database rows are not deleted before the scan; new results are merged back in and matching rows may be overwritten with freshly detected metadata. Prefer the [recommended targeted backfill approach](#recommended-targeted-backfill-for-new-vault-protocols). Very slow on large chains like Ethereum mainnet (~24M+ blocks). |
| `HYPERSYNC_API_KEY` | Optional. Required when using `auto` scan backend. |
| `HYPERSYNC_RPM` | Optional. Hypersync API requests-per-minute limit. Default: 150 (75% of the 200 RPM free-tier limit). Throttling is always on; set this to lower the limit after persistent 429 errors. |
| `HYPERSYNC_CONCURRENCY` | Optional. Number of Hypersync requests in flight per stream — the main throughput knob. Default: server default (10). Increase for dense workloads, decrease for rate-limited plans. See [Envio StreamConfig tuning](https://docs.envio.dev/docs/HyperSync/stream-config-tuning). |

#### Recommended targeted backfill for new vault protocols

The vault scanner is incremental — it only scans new blocks since the last run.
When support for a new protocol's custom events is added (e.g. Ember's `VaultDeposit`),
vaults that emitted events before the code change will not be discovered because the scanner
has already passed those blocks.

For protocols with an authoritative API or registry of vault addresses, prefer a targeted
protocol repair script. The current pattern is `fix-t3tris-vaults.py` or
`fix-upshift-vaults.py`: seed the known vault addresses into the lead database,
refresh only those metadata rows, and run a vault-address-scoped historical
price scan.

#### Deprecated RESET_LEADS historical rediscovery

`RESET_LEADS` is deprecated for adding new vault protocols to existing
production data. Use the [recommended targeted backfill approach](#recommended-targeted-backfill-for-new-vault-protocols)
above when the protocol has an API, registry, factory query, or operator-curated
address list.

`RESET_LEADS` does **not** truncate the existing vault database before scanning. The scanner
builds a fresh in-memory lead set from block 1, then merges those leads and metadata rows back
into the existing pickle. Existing leads that are found again are refreshed; unrelated existing
rows remain in the database. Because matching rows may be overwritten, take a backup before a
large production rescan:

```shell
cp ~/.tradingstrategy/vaults/vault-metadata-db.pickle \
   ~/.tradingstrategy/vaults/vault-metadata-db.before-reset-leads.pickle
```

The scanner currently cannot limit discovery to only the newly added custom event topics. A
historical custom-event backfill, such as TokenGateway/ForgeYields support, still re-queries all
configured vault discovery events for the block range.

This has several operational problems on mature chains:

- It scans every configured vault discovery topic from block 1, not only the new protocol.
- It rebuilds metadata for thousands of unrelated historical leads after discovery finishes.
- A failure in any unrelated protocol adapter can abort the whole run before the new protocol
  data is written. For example, adding T3tris event signatures on Arbitrum exposed a separate
  Lagoon `v0.3.0` adapter gap during metadata extraction.
- It is slow and expensive on chains with large histories, and retrying after an unrelated
  metadata failure repeats the same full-chain discovery work.

Use deprecated `RESET_LEADS` only when there is no reliable protocol API,
registry, factory query, or operator-curated address list to seed targeted leads.

```shell
# Deprecated fallback: re-discover all vaults on Ethereum from block 1
# Prefer: #recommended-targeted-backfill-for-new-vault-protocols
# Works with both HyperSync and RPC backends
RESET_LEADS=1 LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_ETHEREUM poetry run python scripts/erc-4626/scan-vaults.py
```

### scan-vaults-all-chains.py

Scan ERC-4626 vaults across all supported chains with a live console dashboard.

```shell
poetry run python scripts/erc-4626/scan-vaults-all-chains.py
```

| Variable | Description |
|----------|-------------|
| `SCAN_PRICES` | Optional. Also scan prices after vault discovery. Default: false. |
| `RETRY_COUNT` | Optional. Number of retries on failure. |
| `TEST_CHAINS` | Optional. Comma-separated chain names to scan (for testing). Use `none` to skip all EVM chains. |
| `DISABLE_CHAINS` | Optional. Comma-separated chain names to exclude. |
| `SKIP_POST_PROCESSING` | Optional. Skip post-processing steps. |
| `MAX_WORKERS` | Optional. Parallel workers. Default: 50. |
| `LOG_LEVEL` | Optional. Default: WARNING. |
| `PIPELINE_DATA_DIR` | Optional. Directory for all pipeline files (parquet, pickle, DuckDB, state). Default: `~/.tradingstrategy/vaults`. |
| `LOOP_INTERVAL_SECONDS` | Optional. When >0, enables looped mode — ticks every N seconds. Default: 0 (single run). |
| `SCAN_CYCLES` | Optional. Per-item cycle intervals, e.g. `Ethereum=8h,Base=8h,Arbitrum=8h,Lighter=4h,GRVT=4h,Hypercore=4h,Hibachi=4h,Core3=24h`. |
| `DEFAULT_CYCLE` | Optional. Default cycle for items not in `SCAN_CYCLES`. Default: `24h`. |
| `MAX_CYCLES` | Optional. Exit after N cycles (for testing). Default: 0 (unlimited). |
| `SCAN_HYPERCORE` | Optional. Enable Hyperliquid native vault scanning. Default: false. |
| `SCAN_GRVT` | Optional. Enable GRVT native vault scanning. Default: false. |
| `SCAN_LIGHTER` | Optional. Enable Lighter native pool scanning. Default: false. |
| `SCAN_HIBACHI` | Optional. Enable Hibachi native vault scanning. Default: false. |
| `SCAN_VAULT_SETTLEMENTS` | Optional. Scan Lagoon and D2 settlement events before price cleaning. Default: true. Set to `false` only for debugging runs where settlement markers are deliberately skipped. |
| `VAULT_SETTLEMENT_START_BLOCK` | Optional. Inclusive settlement scan start block for forced backfills. Normally unset so scans continue incrementally from `vault-settlements.duckdb`. |
| `VAULT_SETTLEMENT_END_BLOCK` | Optional. Inclusive settlement scan end block for forced backfills. Normally unset so scans continue up to the latest raw price block. |
| `SKIP_CORE3` | Optional. Skip Core3 risk intelligence enrichment. Default: false. Core3 is default-on enrichment for the top-vaults JSON, unlike optional native vault sources that use opt-in `SCAN_*` flags. |
| `CORE3_API_KEY` | Optional. Core3 API key. If missing, Core3 is disabled for the run with a warning. |
| `CORE3_DATABASE_PATH` | Optional. Core3 DuckDB path. Default: `~/.tradingstrategy/vaults/core3/core3.duckdb`. |
| `CURRENCY_API_DB_PATH` / `CURRENCY_API_DATABASE_PATH` | Optional. Exchange-rate DuckDB bundle path. Default: `$PIPELINE_DATA_DIR/exchange-rates.duckdb`. |
| `CORE3_MAX_WORKERS` | Optional. Core3 API worker threads. Default: 8. |
| `CORE3_FETCH_SECTIONS` | Optional. Fetch detailed Core3 section endpoints. Default: true. Set to `false` to skip. |
| `SKIP_SAMPLES` | Optional. Skip Ethereum-only sample file export. Default: false. |
| `HYPERSYNC_RPM` | Optional. Hypersync API requests-per-minute limit. Default: 150. Lower after persistent 429 errors. |
| `HYPERSYNC_CONCURRENCY` | Optional. Hypersync stream concurrency. Default: 1 (sequential) in the all-chains scanner to avoid API pressure when scanning many chains. Set higher for faster throughput. See [Envio StreamConfig tuning](https://docs.envio.dev/docs/HyperSync/stream-config-tuning). |

Robinhood Chain is scanned when `JSON_RPC_ROBINHOOD` is configured. For a
focused Robinhood-only dry run:

```shell
source .local-test.env && \
TEST_CHAINS=Robinhood \
SCAN_PRICES=false \
SKIP_POST_PROCESSING=true \
poetry run python scripts/erc-4626/scan-vaults-all-chains.py
```

Core3 runs after EVM and native vault scans and before post-processing. This
keeps the Core3 DuckDB closed before `eth_defi.vault.top_vaults_json` reads it
and before `export-data-files.py` uploads it to R2.

### scan-prices.py

Scan historical vault share prices and fees for all discovered vaults on a single chain.

```shell
JSON_RPC_URL=$JSON_RPC_BASE poetry run python scripts/erc-4626/scan-prices.py
```

| Variable | Description |
|----------|-------------|
| `JSON_RPC_URL` | Required. RPC endpoint. |
| `FREQUENCY` | Optional. Sampling frequency (`1h`, `1d`). Default: `1h`. |
| `START_BLOCK` | Optional. Start scanning from this block. Default: auto-detected from reader states. |
| `END_BLOCK` | Optional. Stop at this block. Default: latest. |
| `MAX_WORKERS` | Optional. Parallel workers. Default: 20. |
| `VAULT_ID` | Optional. Comma-separated list of vault specs to scan (format: `chain_id-address`). When set, only those vaults are scanned and their saved reader states are cleared for a fresh scan. Parquet deletion is vault-aware — other vaults' data is preserved. |
| `READER_STATE_DATABASE` | Optional. Custom reader state pickle path. |
| `UNCLEANED_PRICE_DATABASE` | Optional. Custom parquet output path. |
| `OUTPUT_FOLDER` | Optional. Custom output directory. |

#### Scanning specific vaults

You can scan a subset of vaults using `VAULT_ID` with comma-separated vault specs.
This is safe to run against production data — only the specified vaults' parquet rows
are deleted and rewritten.

```shell
# Scan all Ember vaults on Ethereum from scratch
VAULT_ID="1-0xf3190a3ecc109f88e7947b849b281918c798a0c4, 1-0x373152feef81cc59502da2c8de877b3d5ae2e342, 1-0x0b9342c15143e8f54a83f887c280a922f4c48771, 1-0x821fc97196d47566b618d27515df2c5201cc4125, 1-0xde88c15bbc9c4254a147a964f1fc937bae12712e, 1-0xb920ed46dec7455d0caf52b357d9a9f55b4daeca, 1-0x7e1916fa3bb694d4e7a038771e8fe97222e775ca, 1-0x9be9294722f8aad37b11a9792be2c782182cafa2, 1-0x2b13311fd553e74b421d4ccc96e348f71e179dcf" \
JSON_RPC_URL=$JSON_RPC_ETHEREUM \
START_BLOCK=1 \
poetry run python scripts/erc-4626/scan-prices.py
```

### fix-t3tris-vaults.py

Targeted repair script for all supported EVM T3tris vaults returned by the
official T3tris API. The script has a baked API snapshot as a fallback, so
operators can review the current vault address list in the script even if the
API is temporarily unavailable.

This follows the [recommended targeted backfill approach](#recommended-targeted-backfill-for-new-vault-protocols)
for adding T3tris vaults to an existing production database after protocol
support has been merged. It does not use deprecated `RESET_LEADS` and does not
wipe whole-chain discovery or price data. It upserts lead rows for the selected
T3tris API vaults, repairs missing or broken metadata rows for those same vaults,
and scans historical prices only for those listed vault addresses. Historical
prices are scanned at most once per supported chain per run. Caught-up vaults
are skipped. For the remaining selected vaults on the chain, the scan starts
from the earliest missing block any of those vaults needs, and parquet deletion
remains scoped to those listed T3tris vault addresses.

```shell
source .local-test.env && poetry run python scripts/erc-4626/fix-t3tris-vaults.py
```

| Variable | Description |
|----------|-------------|
| `DRY_RUN` | Optional. Show planned work without writing metadata or prices. Default: false. |
| `T3TRIS_FETCH_API` | Optional. Fetch the live T3tris API and prefer it over the baked snapshot. Default: true. |
| `T3TRIS_VERIFIED_ONLY` | Optional. Process only API-verified vaults. Default: false. |
| `T3TRIS_SCAN_PRICES` | Optional. Set to `false` to update only leads and metadata. Default: true. |
| `T3TRIS_REWRITE_TARGETED` | Optional. Rescan every selected T3tris vault from its first known API block and rewrite only that vault's rows. Default: false. |
| `T3TRIS_REFRESH_EXISTING_METADATA` | Optional. Refresh existing good metadata rows as well as missing or broken rows. Default: false. |
| `MAX_WORKERS` | Optional. Historical multicall worker count. Default: 8. |
| `FREQUENCY` | Optional. Historical price frequency, `1h` or `1d`. Default: `1h`. |
| `START_BLOCK` | Optional. Global start block override. Use only for a carefully scoped targeted backfill. |
| `END_BLOCK` | Optional. Global end block override. |
| `VAULT_DB_PATH` | Optional. Metadata DB path. Default: production vault metadata DB. |
| `UNCLEANED_PRICE_DATABASE` | Optional. Raw price parquet path. Default: production uncleaned price DB. |
| `READER_STATE_DATABASE` | Optional. Reader-state pickle path. Default: production reader state DB. |

The script reads RPC URLs using normal `JSON_RPC_<CHAIN_NAME>` variables where
the chain is known by `eth_defi.chain`. T3tris currently returns Arbitrum vaults,
so set `JSON_RPC_ARBITRUM` in `.local-test.env`.

### fix-upshift-vaults.py

Targeted repair script for all EVM Upshift vaults returned by the official
Upshift API. The script has a baked API snapshot as a fallback, so operators can
review the full vault address list in the script even if the API is temporarily
unavailable.

This follows the [recommended targeted backfill approach](#recommended-targeted-backfill-for-new-vault-protocols).
It does not use deprecated `RESET_LEADS` and does not wipe whole-chain discovery
or price data. It upserts lead rows for the selected Upshift API vaults, repairs
missing or broken metadata rows for those same vaults, and scans historical
prices only for those listed vault addresses. Historical prices are scanned at
most once per supported chain per run. Caught-up vaults are skipped. For the
remaining selected vaults on the chain, the scan starts from the earliest
missing block any of those vaults needs, and parquet deletion remains scoped to
those listed Upshift vault addresses.

```shell
source .local-test.env && poetry run python scripts/erc-4626/fix-upshift-vaults.py
```

| Variable | Description |
|----------|-------------|
| `DRY_RUN` | Optional. Show planned work without writing metadata or prices. Default: false. |
| `UPSHIFT_FETCH_API` | Optional. Fetch the live Upshift API and prefer it over the baked snapshot. Default: true. |
| `UPSHIFT_STATUS` | Optional. Comma-separated statuses to include, or `all`. Default: `all`. |
| `UPSHIFT_VISIBLE_ONLY` | Optional. Process only API-visible vaults. Default: false. |
| `UPSHIFT_SCAN_PRICES` | Optional. Set to `false` to update only leads and metadata. Default: true. |
| `UPSHIFT_REWRITE_TARGETED` | Optional. Rescan every selected Upshift vault from its first known API block and rewrite only that vault's rows. Default: false. |
| `UPSHIFT_REFRESH_EXISTING_METADATA` | Optional. Refresh existing good metadata rows as well as missing or broken rows. Default: false. |
| `MAX_WORKERS` | Optional. Historical multicall worker count. Default: 8. |
| `FREQUENCY` | Optional. Historical price frequency, `1h` or `1d`. Default: `1h`. |
| `START_BLOCK` | Optional. Global start block override. Use only for a carefully scoped targeted backfill. |
| `END_BLOCK` | Optional. Global end block override. |
| `VAULT_DB_PATH` | Optional. Metadata DB path. Default: production vault metadata DB. |
| `UNCLEANED_PRICE_DATABASE` | Optional. Raw price parquet path. Default: production uncleaned price DB. |
| `READER_STATE_DATABASE` | Optional. Reader-state pickle path. Default: production reader state DB. |

The script reads RPC URLs using normal `JSON_RPC_<CHAIN_NAME>` variables where
the chain is known by `eth_defi.chain`. Upshift API chains not yet present in
the global chain metadata are skipped. For supported chains, lead rows are still
upserted if an RPC URL is missing; only metadata repair and historical price
scanning for that chain are skipped.

### post-process-prices.py

Standalone post-processing pipeline: merges native protocol data, cleans prices,
and uploads to R2. Each step reports success/failure and exits with code 1 if any step fails.
Use to debug post-processing independently of the full chain scan.

```shell
# Full pipeline (merge + clean + export to R2)
source .local-test.env && poetry run python scripts/erc-4626/post-process-prices.py

# Only clean, skip R2 upload
SKIP_EXPORT=true poetry run python scripts/erc-4626/post-process-prices.py

# Include native protocol merges
MERGE_HYPERCORE=true MERGE_GRVT=true MERGE_LIGHTER=true \
  source .local-test.env && poetry run python scripts/erc-4626/post-process-prices.py
```

| Variable | Description |
|----------|-------------|
| `MERGE_HYPERCORE` | Optional. Merge Hyperliquid native vault data. Default: false. |
| `MERGE_GRVT` | Optional. Merge GRVT native vault data. Default: false. |
| `MERGE_LIGHTER` | Optional. Merge Lighter native pool data. Default: false. |
| `SKIP_EXPORT` | Optional. Skip sparkline and metadata export to R2. Default: false. |
| `SKIP_SAMPLES` | Optional. Skip Ethereum-only sample file export. Default: false. |
| `LOG_LEVEL` | Optional. Default: info. |

### repair-vault-features.py

Repair stale top-level feature fields in `vault-metadata-db.pickle`.

Use this after classifier or scan-record changes when `_detection_data.features`
contains protocol flags but the top-level `features` field is missing or empty.
The detection features are authoritative: the script copies them to the
top-level `features` field and does not mutate `_detection_data.features`.
This repairs metadata only: it does not touch `vault-prices-1h.parquet`,
`cleaned-vault-prices-1h.parquet`, reader state, or any vault history rows.

Run a dry run first:

```shell
source .local-test.env && \
DRY_RUN=true \
poetry run python scripts/erc-4626/repair-vault-features.py
```

Then repair the local production pickle:

```shell
source .local-test.env && poetry run python scripts/erc-4626/repair-vault-features.py
```

The script creates a `*.bak-feature-repair` backup next to the pickle before
writing. If that backup already exists, it appends a numeric suffix instead of
overwriting it. After repairing production data, upload the fixed pickle with
`export-data-files.py`.

Run this script from a checkout that can unpickle the current production
metadata schema. If the production pickle contains newer enum members or
dataclass fields than the local checkout, update the checkout first.

Use `purge-royco-tranche-data.py` instead when stale feature flags also caused
bad historical price rows or reader-state progress and affected vaults need to
be purged and rescanned.

| Variable | Description |
|----------|-------------|
| `VAULT_DB` | Optional. Path to the vault metadata pickle. Default: `~/.tradingstrategy/vaults/vault-metadata-db.pickle`. |
| `DRY_RUN` | Optional. Set to `true` to report without modifying the pickle. |
| `LOG_LEVEL` | Optional. Default: info. |

### clean-prices.py

Clean raw scanned vault data. Reads `vault-prices-1h.parquet` and generates `vault-prices-1h-cleaned.parquet`.

```shell
poetry run python scripts/erc-4626/clean-prices.py
```

| Variable | Description |
|----------|-------------|
| `VAULT_ID` | Optional. Debug a single vault only. |

### export-sparklines.py

Export all vault sparklines to Cloudflare R2. Run after cleaned prices are generated.

```shell
poetry run python scripts/erc-4626/export-sparklines.py
```

| Variable | Description |
|----------|-------------|
| `MAX_WORKERS` | Optional. Parallel workers. |

### export-protocol-metadata.py

Export vault protocol metadata and logos to Cloudflare R2.

```shell
poetry run python scripts/erc-4626/export-protocol-metadata.py
```

| Variable | Description |
|----------|-------------|
| `R2_VAULT_METADATA_BUCKET_NAME` | Required. R2 bucket name. |
| `R2_VAULT_METADATA_ACCESS_KEY_ID` | Required. R2 access key. |
| `R2_VAULT_METADATA_SECRET_ACCESS_KEY` | Required. R2 secret key. |
| `R2_VAULT_METADATA_ENDPOINT_URL` | Required. R2 endpoint URL. |
| `R2_VAULT_METADATA_PUBLIC_URL` | Required. R2 public URL. |
| `R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME` | Optional. Alternative R2 bucket for the upcoming private commercial professional vault data bucket. Uses same credentials as primary. |
| `MAX_WORKERS` | Optional. Default: 20. |

### export-data-files.py

Export production data files to Cloudflare R2: raw and cleaned price Parquet,
vault metadata pickle, reader state pickle, sticky vault export state JSON
files, Core3 risk intelligence DuckDB, and exchange-rate DuckDB.

```shell
source .local-test.env && poetry run python scripts/erc-4626/export-data-files.py
```

When `R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME` is configured, files are
uploaded to both buckets. Daily `daily/YYYY-MM-DD/...` backup copies are created
only in the alternative bucket. Missing files, including the Core3 and exchange-rate
DuckDB files, are logged and skipped. Existing `vault-export-state.json` is included
so sticky qualification history is backed up with the rest of the production data set.
The exchange-rate DuckDB path uses the same `CURRENCY_API_DB_PATH` /
`CURRENCY_API_DATABASE_PATH` configuration as the scheduled currency-rate scanner;
without an override it is read from `$PIPELINE_DATA_DIR/exchange-rates.duckdb`.
Run this after metadata-only repairs such as `repair-vault-features.py` so the
fixed `vault-metadata-db.pickle` is published.

| Variable | Description |
|----------|-------------|
| `R2_DATA_BUCKET_NAME` | R2 bucket for data files (falls back to `R2_VAULT_METADATA_BUCKET_NAME`). |
| `R2_DATA_ACCESS_KEY_ID` | R2 access key (falls back to `R2_VAULT_METADATA_ACCESS_KEY_ID`). |
| `R2_DATA_SECRET_ACCESS_KEY` | R2 secret (falls back to `R2_VAULT_METADATA_SECRET_ACCESS_KEY`). |
| `R2_DATA_ENDPOINT_URL` | R2 endpoint (falls back to `R2_VAULT_METADATA_ENDPOINT_URL`). |
| `R2_DATA_PUBLIC_URL` | Public base URL (falls back to `R2_VAULT_METADATA_PUBLIC_URL`). |
| `R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME` | Optional. Alternative bucket for private/professional data. |
| `R2_DAILY_BACKUP` | Optional. Set to `false` to disable daily backup copies. Default: true. |
| `CORE3_DATABASE_PATH` | Optional. Core3 DuckDB path. Default: `~/.tradingstrategy/vaults/core3/core3.duckdb`. |
| `CURRENCY_API_DB_PATH` / `CURRENCY_API_DATABASE_PATH` | Optional. Exchange-rate DuckDB bundle path. Default: `$PIPELINE_DATA_DIR/exchange-rates.duckdb`. |
| `UPLOAD_PREFIX` | Optional. Prefix for S3 keys. |

### export-sample-files.py

Export Ethereum-only sample versions of cleaned vault data files to Cloudflare R2.
Generates `vault-historical.sample.parquet` and `vault-metadata.sample.json`
filtered to Ethereum mainnet (chain_id=1) only, for free download.

Sample files are uploaded to the primary (public) R2 bucket only, not the alternative bucket.

```shell
source .local-test.env && poetry run python scripts/erc-4626/export-sample-files.py
```

| Variable | Description |
|----------|-------------|
| `R2_DATA_BUCKET_NAME` | R2 bucket for data files (falls back to `R2_VAULT_METADATA_BUCKET_NAME`). |
| `R2_DATA_ACCESS_KEY_ID` | R2 access key (falls back to `R2_VAULT_METADATA_ACCESS_KEY_ID`). |
| `R2_DATA_SECRET_ACCESS_KEY` | R2 secret (falls back to `R2_VAULT_METADATA_SECRET_ACCESS_KEY`). |
| `R2_DATA_ENDPOINT_URL` | R2 endpoint (falls back to `R2_VAULT_METADATA_ENDPOINT_URL`). |
| `R2_DATA_PUBLIC_URL` | Public base URL (falls back to `R2_VAULT_METADATA_PUBLIC_URL`). |
| `UPLOAD_PREFIX` | Optional. Prefix for S3 keys. |

Public download URLs:
- `https://vault-protocol-metadata.tradingstrategy.ai/vault-historical.sample.parquet`
- `https://vault-protocol-metadata.tradingstrategy.ai/vault-metadata.sample.json`

### scan-vault-posts.py

Collect tracked RSS, Twitter/X, and LinkedIn posts into the vault post DuckDB.
Prints a summary table plus per-source loaded and failed dashboards.

```shell
poetry run python scripts/erc-4626/scan-vault-posts.py
```

| Variable | Description |
|----------|-------------|
| `DB_PATH` | Optional. DuckDB output path. Default: `~/.tradingstrategy/vaults/vault-post-database.duckdb`. |
| `MAPPINGS_DIR` | Optional. Feeder YAML directory. Default: repo `eth_defi/data/feeds`. |
| `MAX_WORKERS` | Optional. Worker threads for concurrent feed collection. Default: 8. |
| `MAX_POSTS_PER_SOURCE` | Optional. Default: 20. |
| `REQUEST_TIMEOUT` | Optional. Default: 20. |
| `REQUEST_DELAY_SECONDS` | Optional. Default: 1. |
| `TWITTER_RSS_BASE_URLS` | Optional. Comma-separated list of Nitter or xcancel-style RSS bridge base URLs. No default is shipped. |
| `TWITTER_FEED_URL_TEMPLATES` | Optional. Comma-separated URL templates with `{handle}` for Twitter/X live feed bridges. |
| `LINKEDIN_FEED_URL_TEMPLATES` | Optional. Comma-separated URL templates with `{company_id}` for LinkedIn company live feed bridges. |
| `MAX_PROXY_ROTATIONS` | Optional. Default: 3. Maximum Webshare proxy rotations before direct fallback. |
| `WEBSHARE_API_KEY` | Optional. Enables Webshare proxy-backed feed requests when set. |
| `WEBSHARE_PROXY_MODE` | Optional. Webshare proxy mode if supported by the account. |
| `MAX_POST_AGE_DAYS` | Optional. Default: 365. |
| `LOG_LEVEL` | Optional. Default: warning. |

## Docker usage

The vault scanner is packaged as a Docker image via `Dockerfile.vault-scanner`.
The default entrypoint is `scan-vaults-all-chains.py`, which scans **all chains**.
Vault settlement scanning is enabled by default in both the one-shot and looped
Docker Compose services with `SCAN_VAULT_SETTLEMENTS=true`, so Lagoon and D2
settlement markers are populated before cleaned price data is exported.

To run a **single-chain** script or override the command, you must use `--entrypoint`
because the Dockerfile sets `ENTRYPOINT` (not just `CMD`). Without `--entrypoint`,
any command you pass is appended as arguments to the all-chains script.

### Scan only Ethereum (vault discovery)

```shell
docker compose --profile oneshot run --rm \
  --entrypoint python \
  -e JSON_RPC_URL="$JSON_RPC_ETHEREUM" \
  -e LOG_LEVEL=info \
  vault-scanner \
  scripts/erc-4626/scan-vaults.py
```

### Deprecated: scan only Ethereum with lead reset

`RESET_LEADS` is deprecated for adding new vault protocols to existing
production data. Prefer the [recommended targeted backfill approach](#recommended-targeted-backfill-for-new-vault-protocols).
This fallback rescans all configured vault discovery event topics from block 1
and merges the results back into the existing vault database. It does not delete
the database first, but matching rows may be refreshed with newly detected
metadata. Back up `~/.tradingstrategy/vaults/vault-metadata-db.pickle` before
running this against production data.

```shell
docker compose --profile oneshot run --rm \
  --entrypoint python \
  -e JSON_RPC_URL="$JSON_RPC_ETHEREUM" \
  -e HYPERSYNC_API_KEY="$HYPERSYNC_API_KEY" \
  -e SCAN_BACKEND=hypersync \
  -e RESET_LEADS=true \
  -e LOG_LEVEL=info \
  vault-scanner \
  scripts/erc-4626/scan-vaults.py
```

### Scan historical prices for specific vaults only (e.g. ForgeYields)

Use `VAULT_ID` with comma-separated `chain_id-address` pairs. This is safe
to run against production data — only the specified vaults' parquet rows
are deleted and rewritten.

```shell
# ForgeYields: fyUSDC, fyETH, fyWBTC on Ethereum (chain_id=1)
docker compose --profile oneshot run --rm \
  --entrypoint python \
  -e JSON_RPC_URL="$JSON_RPC_ETHEREUM" \
  -e VAULT_ID="1-0x943109dc7c950da4592d85ebd4cfed007af64670,1-0x98cd770b4e9905b1263f0c9ae6cde34e1923508e,1-0xedca8230366b9eaff06becdd1d261577836aa507" \
  -e LOG_LEVEL=info \
  vault-scanner \
  scripts/erc-4626/scan-prices.py
```

### Run the all-chains scanner (default)

```shell
docker compose --profile oneshot run --rm vault-scanner
```

### Interactive shell inside the container

```shell
docker compose --profile oneshot run --rm --entrypoint /bin/bash vault-scanner
```

## Debugging and verification

Scripts for checking individual vault data and diagnosing issues.

### check-price-freshness.py

Check how fresh the cleaned vault price data is. Prints absolute and median latest timestamps
(with IQR outlier removal) and exits with code 1 if the median age exceeds the threshold.

```shell
# Check local file (default)
poetry run python scripts/erc-4626/check-price-freshness.py

# Check production data
PARQUET_URL=https://vault-protocol-metadata.tradingstrategy.ai/cleaned-vault-prices-1h.parquet \
  poetry run python scripts/erc-4626/check-price-freshness.py
```

| Variable | Description |
|----------|-------------|
| `PARQUET_URL` | Optional. URL to load parquet from. Default: local file. |
| `MAX_AGE_HOURS` | Optional. Maximum allowed age in hours. Default: 24. |

### check-vault-onchain.py

Check a vault's current on-chain data: name, TVL, share price, descriptions, flags, deposit/redemption status.
Edit the vault spec inside the script to change the target vault.

```shell
source .local-test.env && poetry run python scripts/erc-4626/check-vault-onchain.py
```

### check-vault-history.py

Check historical data for a single vault using the same `VaultHistoricalReadMulticaller` pipeline as `scan-prices.py`.
Displays tabulated head + tail output.

```shell
VAULT_ID="143-0x8d3f9f9eb2f5e8b48efbb4074440d1e2a34bc365" \
  poetry run python scripts/erc-4626/check-vault-history.py
```

| Variable | Description |
|----------|-------------|
| `VAULT_ID` | Required. Format: `chain_id-address`. |
| `START_BLOCK` | Optional. First block to read. |
| `END_BLOCK` | Optional. Last block to read. Default: latest. |
| `STEP` | Optional. Block step. Default: ~1h based on chain block time. |
| `LIMIT` | Optional. Rows in head + tail display. Default: 10. |
| `MAX_WORKERS` | Optional. Parallel workers. Default: 4. |

### check-vault-historical-data.py

Simpler block-by-block historical data checker. Reads one block at a time (no multicall batching).

```shell
VAULT_ID="1-0xF0A33207A6e363faa58Aed86Abb7b4d2E51591c0" \
  JSON_RPC_URL=$JSON_RPC_ETHEREUM \
  poetry run python scripts/erc-4626/check-vault-historical-data.py
```

| Variable | Description |
|----------|-------------|
| `VAULT_ID` | Required. Format: `chain_id-address`. |
| `LIMIT` | Optional. Rows in head + tail display. Default: 20. |
| `BLOCK_COUNT` | Optional. Number of blocks to scan. Default: 50. |

### check-vault-metadata.py

Check the written metadata of a vault and validate scanner identification.

```shell
poetry run python scripts/erc-4626/check-vault-metadata.py
```

### check-reader-states.py

Examine vault reader states from the state pickle file. Prints broken contracts and state summaries.

```shell
poetry run python scripts/erc-4626/check-reader-states.py
```

| Variable | Description |
|----------|-------------|
| `READER_STATE_PATH` | Optional. Default: `~/.tradingstrategy/vaults/reader-state.pickle`. |

### check-share-price.py

Check a vault share price at specific blocks.

```shell
poetry run python scripts/erc-4626/check-share-price.py
```

### check-prices-parquet.py

Check and inspect the Parquet vault share prices file.

```shell
poetry run python scripts/erc-4626/check-prices-parquet.py
```

### examine-vault-state.py

Examine scan state for a single vault. Superseded by the `erc-4626-examine-vault-reader-state.ipynb` notebook.

```shell
VAULT_ID="1-0x..." poetry run python scripts/erc-4626/examine-vault-state.py
```

## Data repair

Scripts for fixing data issues in the pipeline.

### purge-stale-chain-ids.py

Purge stale vault entries with obsolete chain IDs from the vault database.
When a synthetic chain ID is changed (e.g. Hypercore from -999 to 9999),
old entries remain in the pickle and cause slug collisions.

```shell
# Dry-run (just report)
DRY_RUN=true poetry run python scripts/erc-4626/purge-stale-chain-ids.py

# Actually purge
poetry run python scripts/erc-4626/purge-stale-chain-ids.py
```

| Variable | Description |
|----------|-------------|
| `DRY_RUN` | Optional. Report only, no changes. |
| `LOG_LEVEL` | Optional. Default: info. |

### heal-broken-vaults.py

Heal broken vault metadata entries caused by transient RPC failures.
When the vault scanner hits an HTTP error or timeout, it stores a placeholder
record with `<broken: ...>` as the name. This script re-reads those vaults
from the chain and replaces the broken records with fresh data.

```shell
source .local-test.env && poetry run python scripts/erc-4626/heal-broken-vaults.py
```

| Variable | Description |
|----------|-------------|
| `MAX_WORKERS` | Optional. Thread pool size. Default: 8. |
| `DRY_RUN` | Optional. Report broken vaults without healing. Default: false. |
| `HEAL_ALL` | Optional. Also attempt empty-name entries (likely false positives). Default: false. |
| `JSON_RPC_<CHAIN>` | Required per chain with broken vaults. |
| `LOG_LEVEL` | Optional. Default: info. |

### prepopulate-timestamps.py

Prepopulate the Hypersync block timestamp DuckDB cache for all scanner chains.
Use this to recover chains stuck in a 429 rate-limit spiral — when a chain's
timestamp cache falls behind, each scan cycle needs more blocks, making it more
likely to hit 429 again. Running this script during a quiet period (with the
looped scanner stopped) lets the cache catch up without competing for API quota.

The script uses the same chain list as `scan-vaults-all-chains.py` and only
fetches the delta since the last cached block — it never re-downloads data that
is already in the cache. Large ranges are chunked into 100k-block pieces with
durable saves after each chunk.

```shell
# All chains (skips those without JSON_RPC_* or Hypersync support)
source .local-test.env && poetry run python scripts/hypersync/prepopulate-timestamps.py

# Specific stuck chains only
CHAIN_FILTER="Polygon,Binance,Plasma" \
  source .local-test.env && poetry run python scripts/hypersync/prepopulate-timestamps.py
```

Inside the Docker container:

```shell
docker compose --profile oneshot run --rm \
  --entrypoint python \
  -e CHAIN_FILTER="Polygon,Binance,Plasma" \
  -e LOG_LEVEL=info \
  vault-scanner \
  scripts/hypersync/prepopulate-timestamps.py
```

| Variable | Description |
|----------|-------------|
| `CHAIN_FILTER` | Optional. Comma-separated chain names to process. Default: all chains. |
| `HYPERSYNC_API_KEY` | Required. Envio Hypersync API key. |
| `HYPERSYNC_RPM` | Optional. Requests-per-minute limit. Default: 150. Lower after persistent 429 errors. |
| `HYPERSYNC_CONCURRENCY` | Optional. Stream concurrency. Default: server default (10). |
| `JSON_RPC_<CHAIN>` | Required per chain. Same env vars as docker-compose. |
| `LOG_LEVEL` | Optional. Default: info. |

### heal-timestamps-all-chains.py

Detect and heal gaps in block timestamp DuckDB caches across all chains.
Scans `~/.tradingstrategy/block-timestamp/` for existing databases, detects
interior gaps left by dropped Hypersync batches, and re-fetches them. No RPC
URLs needed — chain IDs are extracted from database filenames and Hypersync
servers are resolved automatically.

```shell
# Diagnose gaps without healing (recommended first step)
DRY_RUN=true poetry run python scripts/erc-4626/heal-timestamps-all-chains.py

# Heal all chains
poetry run python scripts/erc-4626/heal-timestamps-all-chains.py

# Heal specific chains only
TEST_CHAINS=Monad,Base poetry run python scripts/erc-4626/heal-timestamps-all-chains.py
```

Inside the Docker container:

```shell
docker compose --profile oneshot run --rm \
  --entrypoint python \
  -e DRY_RUN=true \
  -e LOG_LEVEL=info \
  vault-scanner \
  scripts/erc-4626/heal-timestamps-all-chains.py
```

| Variable | Description |
|----------|-------------|
| `DRY_RUN` | Optional. Report gaps without healing. Default: false. |
| `TEST_CHAINS` | Optional. Comma-separated chain names to heal. Default: all. |
| `HYPERSYNC_API_KEY` | Optional but recommended. Envio Hypersync API key. |
| `HYPERSYNC_RPM` | Optional. Requests-per-minute limit. Default: 150. |
| `HYPERSYNC_CONCURRENCY` | Optional. Stream concurrency. Default: server default (10). |
| `LOG_LEVEL` | Optional. Default: info. |

### heal-timestamps.py

Heal gaps in the block timestamp DuckDB cache for a single chain. Use
`heal-timestamps-all-chains.py` above for multi-chain healing.

```shell
JSON_RPC_URL=$JSON_RPC_MONAD poetry run python scripts/erc-4626/heal-timestamps.py
```

| Variable | Description |
|----------|-------------|
| `JSON_RPC_URL` | Required. RPC endpoint. |
| `DRY_RUN` | Optional. Diagnose gaps without healing. |

### fix-token-cache.py

Look at the token cache for bad entries and attempt to heal them.

```shell
poetry run python scripts/erc-4626/fix-token-cache.py
```

### purge-price-data.py

Purge vault share price data for a chain. The next scan starts from scratch.

```shell
CHAIN_ID=8453 poetry run python scripts/erc-4626/purge-price-data.py
```

| Variable | Description |
|----------|-------------|
| `CHAIN_ID` | Required. Chain to purge. |
| `LOG_LEVEL` | Optional. |

## Analysis and reporting

Scripts for generating analysis reports and statistics.

### identify-curators.py

Identify vault curators and print a per-curator summary (vault count, total TVL).

```shell
poetry run python scripts/erc-4626/identify-curators.py
```

| Variable | Description |
|----------|-------------|
| `DATA_DIR` | Optional. Vault data directory. Default: `~/.tradingstrategy/vaults`. |

### vault-analysis-json.py

Multi-chain vault analysis with JSON export and lifetime metric analysis.
The implementation lives in `eth_defi.vault.top_vaults_json`; the script is a
compatibility wrapper for manual operator runs.

Generates `top_vaults_by_chain.json` with the following top-level structure:

```json
{
  "generated_at": "2026-06-08T12:00:00Z",
  "metadata": {
    "version": {
      "tag": "v0.31",
      "commit_message": "feat: stamp version",
      "commit_hash": "4cea3aa3deadbeef"
    }
  },
  "core3_protocols": {
    "morpho": { "slug": "morpho", "pol": {...}, "fetched_at": "2026-06-07T12:00:00", ... },
    "fluid": { "slug": "instadapp", "pol": {...}, ... }
  },
  "curators": {
    "gauntlet": { "slug": "gauntlet", "name": "Gauntlet", "twitter": "https://x.com/gauntlet_xyz", "recent_posts": [...], ... },
    "hyperliquid": { "slug": "hyperliquid", "name": "Hyperliquid", "protocol_curator": true, ... }
  },
  "vaults": [ ... ]
}
```

The `metadata.version` dict carries the git version stamp of the exporter
Docker image (see `eth_defi.version_info.VersionInfo` and
`Dockerfile.vault-scanner`), so any generated JSON can be traced back to the
exact code revision that produced it. All version fields are `null` when the
exporter runs outside a stamped Docker image, e.g. from a source checkout.
Each field can also be `null` individually when its build ARG was not passed —
in particular `tag` is `null` for images built from an untagged commit, so
consumers should treat `commit_hash` as the primary build identifier.
The Ethereum-only `vault-metadata.sample.json` carries the same `metadata` key.

Core3 risk intelligence records are attached at the top level keyed by protocol
slug (not duplicated per-vault). The `core3_protocols` dict is built directly
from the Core3 DuckDB at export time and only includes protocols present in the
exported vaults.

Curator metadata and recent feed entries are attached at the top level keyed by
curator slug. The `curators` dict is built from curator/protocol YAML files and
the vault post feed database at export time, and only includes curators present
in the exported vaults. Each curator record includes up to 10 recent posts from
Twitter, LinkedIn, and RSS feeds.

The export is append-biased. Once a vault passes the production `MIN_TVL` peak
TVL filter, it is recorded in a sticky state file and remains in later exports
even if current metrics are temporarily missing, stale, or below the current
threshold. Sticky state is always enabled. The default state file is
`~/.tradingstrategy/vaults/vault-export-state.json` or, when
`PIPELINE_DATA_DIR` is set, `<PIPELINE_DATA_DIR>/vault-export-state.json`.
Both `scan-vaults-all-chains.py` and `post-process-prices.py` route
top-vaults generation through this shared pipeline data directory.
Manual scratch runs should set `VAULT_EXPORT_STATE_PATH` if they should not
read or update the shared state file.

Sticky fallback rows carry `sticky_export=true`; rows replayed from the stored
fallback record also carry `stale_export=true` and
`risk_possibly_stale=true`. Current rows that are old but still present remain
exported with `stale_current_row=true` and `risk_possibly_stale=true`.

Rows with the exact `Blacklisted` risk label are structurally suppressed and
are not sticky-replayed. Sticky fallback records that no longer contain a safe
vault identity are also structurally suppressed instead of being exported.

A corrupt sticky state file aborts the top-vaults export instead of resetting
qualification history. The post-processing wrapper reports this as `False`, the
same boolean it uses for upload failures.

#### Brotli-compressed R2 upload

When the top-vaults JSON is uploaded to R2 (via the post-processing pipeline or
`scan-vaults-all-chains.py`), both the raw `.json` and a brotli-compressed
`.json.br` variant are uploaded to each configured bucket. The `.json.br` object
uses `Content-Encoding: br` and `Content-Type: application/json` so that
browsers transparently decompress it.

Brotli compression uses quality 11 (maximum, suitable for offline pipelines).
If the `brotli` package is not installed, the upload fails with a logged warning
and the function returns `False` — the raw JSON is still uploaded first.

The `brotli` package is included in the `cloudflare_r2` poetry extra:

```shell
poetry install -E cloudflare_r2
```

In production, run with `OUTPUT_JSON` pointing to the upload path. This uses the
shared sticky state file under the pipeline data directory:

```shell
OUTPUT_JSON=~/.tradingstrategy/top_vaults_by_chain.json poetry run python scripts/erc-4626/vault-analysis-json.py
```

For local scratch exports, set both `OUTPUT_JSON` and `VAULT_EXPORT_STATE_PATH`
to temporary paths:

```shell
OUTPUT_JSON=/tmp/top-vaults.json VAULT_EXPORT_STATE_PATH=/tmp/vault-export-state.json poetry run python scripts/erc-4626/vault-analysis-json.py
```

| Variable | Description |
|----------|-------------|
| `OUTPUT_JSON` | Optional. Output file path. Default: `~/.tradingstrategy/vaults/stablecoin-vault-metrics.json`. |
| `CORE3_DATABASE_PATH` | Optional. Core3 DuckDB path. Default: `~/.tradingstrategy/vaults/core3/core3.duckdb`. |
| `FEED_DB_PATH` | Optional. Vault post feed DuckDB path. Falls back to `DB_PATH` (used by the feed collector). Default: `~/.tradingstrategy/vaults/vault-post-database.duckdb`. |
| `R2_VAULT_METADATA_PUBLIC_URL` | Optional. Public base URL for curator logo URLs in the export. |
| `VAULT_EXPORT_STATE_PATH` | Optional. Explicit sticky export state path for scratch or alternate-pipeline runs. Defaults to `vault-export-state.json` under the data directory. |
| `STICKY_STALE_WARNING_AGE_DAYS` | Optional. Age in days after which stale annotations and warnings are emitted. Default: 14. |

After generating, upload to R2 with rclone:

```shell
rclone copy ~/.tradingstrategy/top_vaults_by_chain.json vaults-storage:top-defi-vaults/
```

### vault-analysis-gsheet.py

Vault analysis with Google Sheets upload and lifetime metric analysis.

```shell
poetry run python scripts/erc-4626/vault-analysis-gsheet.py
```

| Variable | Description |
|----------|-------------|
| `SELECTED_CHAIN_ID` | Chain to analyse. |
| `MONTHS` | Lookback period in months. |
| `MIN_TVL` | Minimum TVL filter. |
| `DATA_DIR` | Data directory path. |
| `PARQUET_FILE` | Input parquet file. |
| `MAX_ANNUALISED_RETURN` | Cap for annualised return filter. |
| `GS_SERVICE_ACCOUNT_FILE` | Google Sheets service account credentials. |
| `GS_SHEET_URL` | Target Google Sheet URL. |
| `GS_WORKSHEET_NAME` | Target worksheet name. |

### vault-price-stats.py

Print statistics about vault price data parquet files (uncleaned and cleaned).

```shell
poetry run python scripts/erc-4626/vault-price-stats.py
```

### render-sparkline.py

Test rendering a sparkline for a single vault and open the result in a browser.

```shell
poetry run python scripts/erc-4626/render-sparkline.py
```

### list-depegged-vaults.py

Report which vaults are blacklisted because their denomination stablecoin has
depegged, and the total TVL impact. Cross-references the vault metadata database
against the stablecoin depeg markers (`depegged_at` in
`eth_defi/data/stablecoins/*.yaml`) using the same lookup the export pipeline
uses (`build_depegged_stablecoin_lookups`), so the figures match what gets
blacklisted in production. Prints a per-stablecoin summary (vault count, nominal
TVL, and estimated real USD value at the current depegged rate), a grand total,
and an optional per-vault detail table. It also logs a warning for any depegged
stablecoin that cannot be enforced because it has no `contract_addresses` and an
ambiguous ticker (e.g. multi-entry tokens such as USDX before their address is
pinned).

```shell
# Use the locally cached vault database
poetry run python scripts/erc-4626/list-depegged-vaults.py

# Point at an explicitly downloaded database and hide the per-vault detail
VAULT_DB_PATH=/tmp/vault-metadata-db.pickle SHOW_DETAIL=false \
    poetry run python scripts/erc-4626/list-depegged-vaults.py
```

| Variable | Description |
|----------|-------------|
| `VAULT_DB_PATH` | Optional. Path to the vault metadata database pickle. Default: `~/.tradingstrategy/vaults/vault-metadata-db.pickle`. |
| `STABLECOINS_DIR` | Optional. Stablecoin metadata YAML directory. Default: packaged `eth_defi/data/stablecoins`. |
| `MIN_TVL` | Optional. Ignore vaults whose NAV is below this nominal value. Default: 0. |
| `SHOW_DETAIL` | Optional. Print the per-vault detail table. Default: true. |
| `LOG_LEVEL` | Optional. Default: warning. |

## Data extraction

Scripts for extracting subsets of data for testing or analysis.

### extract-single-vault.py

Extract single chain price data from the bundled cleaned data file with resampling.

```shell
poetry run python scripts/erc-4626/extract-single-vault.py
```

### extract-cleaned-price-data-sample.py

Extract single chain price data from the cleaned bundled data file.

```shell
poetry run python scripts/erc-4626/extract-cleaned-price-data-sample.py
```

### extract-uncleaned-price-data-sample.py

Extract single chain price data from the uncleaned bundled data file.

```shell
poetry run python scripts/erc-4626/extract-uncleaned-price-data-sample.py
```

### extract-test-set.py

Extract a test set for a single vault from local metadata and uncleaned price Parquet.

```shell
poetry run python scripts/erc-4626/extract-test-set.py
```

| Variable | Description |
|----------|-------------|
| `CHAIN_ID` | Optional. Default: 8453. |
| `VAULT_ADDRESS` | Optional. Default provided in script. |
| `TEST_NAME` | Optional. Default: `base_usdc_yield_dynavault_v3`. |

## Utilities

### create-protocol-slug.py

Create a URL-friendly vault protocol slug from a protocol name.

```shell
PROTOCOL_NAME="My Protocol" poetry run python scripts/erc-4626/create-protocol-slug.py
```

### examine-scan-prices-profiling.py

Examine profiler output from scan-prices profiling.

```shell
poetry run python scripts/erc-4626/examine-scan-prices-profiling.py
```

### wrangle-single-vault.py

Inspect data cleaning functions for problematic vaults.

```shell
poetry run python scripts/erc-4626/wrangle-single-vault.py
```

## Examples and demos

### read-historical-apy.py

Example script to estimate the historical APY of an ERC-4626 vault. Requires an archive node.

```shell
JSON_RPC_URL=$JSON_RPC_BASE poetry run python scripts/erc-4626/read-historical-apy.py
```

### read-live-apy.py

Example script to estimate the live APY of an ERC-4626 vault. Requires an archive node.

```shell
JSON_RPC_URL=$JSON_RPC_BASE poetry run python scripts/erc-4626/read-live-apy.py
```

### read-share-prices.py

Read share prices of all previously discovered vaults.

```shell
poetry run python scripts/erc-4626/read-share-prices.py
```

### erc-4626-deposit-redeem.py

ERC-4626 vault deposit and redeem script. Supports simulation mode on Anvil mainnet fork with multichain support.

```shell
JSON_RPC_URL=$JSON_RPC_BASE poetry run python scripts/erc-4626/erc-4626-deposit-redeem.py
```
