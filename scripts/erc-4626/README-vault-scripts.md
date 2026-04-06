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

| Variable | Description |
|----------|-------------|
| `JSON_RPC_URL` | Required. RPC endpoint for the chain. |
| `LOG_LEVEL` | Optional. Default: WARNING. |
| `MAX_GETLOGS_RANGE` | Optional. Max block range for getLogs. |
| `SCAN_BACKEND` | Optional. Event reader backend (`auto`, `hypersync`, `rpc`). |
| `END_BLOCK` | Optional. Stop scanning at this block. |
| `RESET_LEADS` | Optional. Rescan from block 1, discarding existing leads. Use when new protocol event support has been added and historical events need to be re-discovered. Very slow on large chains like Ethereum mainnet (~24M+ blocks). |
| `HYPERSYNC_API_KEY` | Optional. Required when using `auto` scan backend. |

#### Re-discovering vaults after adding new protocol support

The vault scanner is incremental — it only scans new blocks since the last run.
When support for a new protocol's custom events is added (e.g. Ember's `VaultDeposit`),
vaults that emitted events before the code change will not be discovered because the scanner
has already passed those blocks. Use `RESET_LEADS` to rescan from the beginning:

```shell
# Re-discover all vaults on Ethereum from block 1
# Works with both Hypersync and RPC backends
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
| `TEST_CHAINS` | Optional. Comma-separated chain IDs to scan (for testing). |
| `SKIP_POST_PROCESSING` | Optional. Skip post-processing steps. |
| `MAX_WORKERS` | Optional. Parallel workers. |
| `LOG_LEVEL` | Optional. Default: WARNING. |

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

### heal-timestamps.py

Heal gaps in the block timestamp DuckDB cache populated by HyperSync.

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

### vault-analysis-json.py

Multi-chain vault analysis with JSON export and lifetime metric analysis.

In production, run with `OUTPUT_JSON` pointing to the upload path:

```shell
OUTPUT_JSON=~/.tradingstrategy/top_vaults_by_chain.json poetry run python scripts/erc-4626/vault-analysis-json.py
```

| Variable | Description |
|----------|-------------|
| `OUTPUT_JSON` | Optional. Output file path. Default: `~/.tradingstrategy/vaults/stablecoin-vault-metrics.json`. |

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
