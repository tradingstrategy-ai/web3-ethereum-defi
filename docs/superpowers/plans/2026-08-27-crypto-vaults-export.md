# Crypto vaults export plan

## Goal

Create a new, isolated `crypto-vaults` data bundle containing:

1. stablecoin-denominated vaults already covered by the production export;
2. ETH-denominated vaults whose denomination symbol is in the maintained ETH
   wrapper whitelist; and
3. BTC-denominated vaults whose denomination symbol is in the maintained BTC
   wrapper whitelist.

The bundle must have its own cleaned one-day price series, lifetime metrics,
vault metadata, sticky export state, manifest, private R2 object namespace and
daily backups. Existing public stablecoin artefacts and their behaviour must
remain unchanged.

This is an export and post-processing extension. The raw scanner already reads
all supported vault denominations because
`scan_historical_prices_to_parquet()` constructs
`VaultHistoricalReadMulticaller(supported_quote_tokens=None)`. The existing raw
`vault-prices-1h.parquet`, reader state and per-chain scans therefore remain the
single source of price history. Do not launch a second historical chain scan or
create a second reader-state database for this bundle.

## References reviewed

- `scripts/erc-4626/README-vault-scripts.md`: production scan, cleaning,
  lifetime-metrics, R2 data export and backup behaviour.
- `docs/source/tutorials/erc-4626-scan-prices.rst`: raw versus cleaned price
  responsibilities and the current stablecoin-only cleaning limitation.
- `eth_defi/erc_4626/vault_protocol/README-reader-states.md`: shared raw scan
  continuation and warm-up state.
- `eth_defi/data/README.md`: static metadata conventions and R2 metadata flow.
- `eth_defi/currency_api/README-currency-api.md`: existing daily BTC/ETH/USD
  rates, availability boundary and known-bad-rate cleaning.
- `scripts/lighter/README-lighter-vaults.md` and
  `scripts/hyperliquid/README-hyperliquid-vaults.md`: native price merge before
  common cleaning.
- `docs/claude-plans/2026-07-16-hypercore-four-hour-cleaned-prices.md`:
  observation-preserving resampling rules and the distinction between source
  cadence and published cadence.
- `eth_defi/erc_4626/vault_protocol/README-utilisation.md` and
  `eth_defi/erc_4626/vault_protocol/README-vault-redeemable.md`: denomination
  units for TVL, utilisation and available liquidity.
- The parquet migration and Monad state-preservation rules in `CLAUDE.md`.

## Current behaviour and extension points

The current production path is:

```text
per-chain and native scans
  -> vault-prices-1h.parquet                         all scanned denominations
  -> generate_cleaned_vault_datasets()
       -> filter_vaults_by_stablecoin()
  -> cleaned-vault-prices-1h.parquet                stablecoins only
  -> top_vaults_json.main()
       -> a second is_stablecoin_like() filter
       -> calculate_lifetime_metrics()
       -> vault-export-state.json
       -> top_vaults_by_chain.json
  -> public and alternative R2 exports
```

The relevant implementation is split across:

- `eth_defi/research/wrangle_vault_prices.py` for selection and cleaning;
- `eth_defi/research/vault_metrics.py` for daily return and lifetime metrics;
- `eth_defi/vault/top_vaults_json.py` for stablecoin selection, sticky state
  and JSON generation;
- `eth_defi/vault/post_processing.py` for phase ordering and stale-output
  protection;
- `eth_defi/vault/data_file_export.py` for flat public/alternative data-file
  uploads and alternative-bucket daily backups; and
- `eth_defi/vault/scan_all_chains.py` for paths, local backups and configuration.

The existing data-file exporter cannot be reused unchanged for the new bundle:
it uploads every selected file to both primary and alternative buckets. The
`crypto-vaults` bundle is private-only and needs a separately named,
manifest-last publication operation.

## Output contract

Store generated files locally under:

```text
$PIPELINE_DATA_DIR/crypto-vaults/
  crypto-cleaned-vault-prices-1d.parquet
  crypto-vault-metadata.json
  crypto-vault-metadata.json.br
  crypto-vault-manifest.json
  crypto-vault-export-state.json
```

Use crypto-specific local basenames because the existing local backup helper
flattens source paths to basenames. Upload them only to the configured
alternative/private data bucket using the same flat overwrite-in-place practice
as the current exports:

```text
crypto-cleaned-vault-prices-1d.parquet
crypto-vault-metadata.json
crypto-vault-metadata.json.br
crypto-vault-export-state.json
crypto-vault-manifest.json
```

`UPLOAD_PREFIX=test-` must produce keys such as
`test-crypto-cleaned-vault-prices-1d.parquet` so staging runs
cannot overwrite production. Do not add any of these objects to the primary
public data bucket, the primary top-vaults bucket, the current sample export or
the existing flat object list returned by `get_data_file_paths()`.

The current vault exports do not use generation IDs. Do not introduce them for
this bundle. Use `crypto-vault-manifest.json` as the current bundle checksum and provenance
document and upload it last. It must contain:

- bundle name and schema version;
- `generated_at` and `metadata.version` build provenance;
- the denomination-symbol whitelist schema/version or content digest;
- flat object keys, file names, sizes, row counts and SHA-256 digests;
- total vault and row counts by `stablecoin`, `eth` and `btc` family;
- minimum and maximum observation timestamps;
- sparse daily-observation and forward-filled metric semantics; and
- matching build provenance shared by the Parquet and JSON metadata.

If any payload upload before the manifest fails, leave the previous manifest
unchanged, do not create daily backups for that run, and return a failed crypto
post-processing step. Because the flat payload keys may already have changed,
consumers must verify their digests against the manifest and reject a mismatched
bundle until the next successful publication or daily-backup restoration. This
matches the overwrite-in-place model of the current exports without adding a
generation lifecycle.

### Cleaned Parquet schema

The daily Parquet should retain the useful columns from
`CleanedVaultPriceRow`, including protocol, fee, capacity, flow, settlement,
utilisation and native-perp fields where present. Add explicit bundle columns:

| Column | Meaning |
|---|---|
| `denomination_family` | `stablecoin`, `eth` or `btc` |
| `canonical_underlying` | `USD`, `ETH` or `BTC` |
| `denomination_token_address` | Exact lower-case token address, or null for a reviewed synthetic denomination |
| `denomination_token_symbol` | Observed token symbol retained for display and auditing |
| `returns_1d` | Legacy name for the sparse return between consecutive published observations, which may be more than one calendar day apart |

`share_price`, `total_assets`, `available_liquidity`, deposit/withdrawal values
and related quantities remain in denomination-token units. Do not relabel ETH
or BTC amounts as USD. Both `returns_1h` and `returns_1d` are legacy sparse
observation-return names and must not be interpreted literally as fixed-cadence
returns. New bundle metrics must calculate their regular daily series from
`share_price` through `prepare_daily_share_price_series()`.

Document the compatibility field with the repository's Sphinx-style line
comments wherever it is declared, for example:

```python
#: Legacy name for a sparse observation-to-observation return.
#: Consecutive observations may be more than one calendar day apart.
returns_1d: float
```

The initial bundle does not need historical USD conversion. The existing
exchange-rate database can support a later, separately specified
`total_assets_usd` presentation column, but its BTC/ETH history begins on
2024-03-02 and must not be silently backfilled. Keeping native denomination
units makes the first bundle complete for older price histories and keeps
asset-relative yield calculations correct.

### Daily sampling semantics

Materialise at most one row per vault per occupied UTC date:

1. run the existing vault-specific cleaning and repair logic at the retained
   source cadence;
2. group by vault ID and UTC date;
3. select the chronologically last valid cleaned observation, breaking an
   exact timestamp tie with the greater block number and then stable source
   order;
4. preserve the selected observation's original timestamp and block number;
5. do not invent missing dates and do not forward-fill rows; and
6. calculate the legacy sparse `returns_1d` field from consecutive selected
   observations, with the first valid observation set to zero.

Preserving the source timestamp avoids labelling an end-of-day observation as
midnight and accidentally introducing look-ahead behaviour. “One-day
frequency” therefore means no more than one actual observation in each UTC-day
bucket, not a synthetic regular grid.

The Parquet remains observation-preserving, but metric calculation uses the
existing :py:func:`eth_defi.research.vault_metrics.prepare_daily_share_price_series`
path from current `master`. That helper resamples observations to calendar days
and forward fills missing days before calculating regular daily returns. An
unobserved day therefore has zero return and accumulated movement appears on
the next observed day. Reuse this existing convention for lifetime, volatility,
Sharpe and period calculations instead of adding separate sparse-series logic.

## Denomination-family identification

### Classification rules

Add a shared, typed classifier and one maintained symbol whitelist rather than
scattering symbol lists through the cleaner and exporter. Suggested locations
are:

```text
eth_defi/vault/denomination.py
eth_defi/data/crypto_assets/denomination-symbols.yaml
```

Use a string enum whose members and values are snake case:

```text
stablecoin
eth
btc
unsupported
```

Classification precedence must be:

1. Existing `is_stablecoin_like()` behaviour for stablecoins. This preserves
   the exact membership contract of the current export.
2. The denomination symbol, normalised to upper case and matched against the
   ETH symbol whitelist.
3. The same normalised symbol matched against the BTC symbol whitelist.
4. `unsupported` for everything else.

Use only the denomination token symbol stored in vault metadata. Do not infer
the family from the vault name, protocol name, chain-native token or contract
address. Address and decimals remain useful audit context but are not part of
the inclusion decision.

The whitelist data should contain:

- separate `eth` and `btc` symbol lists;
- exact normalised symbols, including bridge or wrapper variants where those
  variants must remain distinct;
- a display name and wrapper kind per symbol for metadata output;
- canonical underlying (`ETH` or `BTC`); and
- a short note and review date for non-obvious symbols.

Normalisation must be deterministic and deliberately narrow: strip surrounding
whitespace and uppercase the symbol. Do not automatically remove arbitrary
prefixes or suffixes. Add bridge spellings such as `.E` explicitly when they
are intended matches. Reject a symbol appearing in both family lists at load
time. Also reject an ETH/BTC whitelist symbol already classified as a
stablecoin, so precedence cannot hide a configuration conflict.

Make the whitelist deliberately broad. Include native representations, wrapped
and bridged representations, liquid-staking tokens, liquid-restaking tokens,
yield-bearing wrappers and protocol-specific receipt/wrapper tokens whose
denomination is economically ETH-like or BTC-like. Do not limit the list to
one-to-one wrappers.

Seed and production-audit at least these common symbol candidates:

- ETH-like: `ETH`, `WETH`, `WETH.E`, `STETH`, `WSTETH`, `RETH`, `CBETH`,
  `SFRXETH`, `FRXETH`, `ANKRETH`, `SWETH`, `OSETH`, `ETHX`, `EETH`, `WEETH`,
  `EZETH`, `RSETH`, `RSWETH`, `PUFETH`, `UNIETH`, `METH`, `CMETH`, `YNETH`,
  `YNETHX`, `APXETH`, `TETH`, `WBETH`, `BETH`, `SETH2`, `AWETH`, `AETHWETH`,
  `CETH` and `CWETH`;
- BTC-like: `BTC`, `WBTC`, `WBTC.E`, `CBBTC`, `IBTC`, `TBTC`, `TBTCV2`,
  `FBTC`, `LBTC`, `EBTC`, `KBTC`, `MBTC`, `SBTC`, `XBTC`, `BTCB`, `BTC.B`,
  `RENBTC`, `HBTC`, `OBTC`, `UNIBTC`, `PUMPBTC`, `SOLVBTC`, `SOLVBTC.BBN`,
  `DLCBTC`, `SWBTC`, `CLBTC` and `VBTC`.

These are seed candidates, not a ceiling. Extend both lists with every
ETH-like and BTC-like denomination symbol found by the production inventory,
including protocol-specific wrappers. Record each entry's wrapper kind, for
example `native`, `wrapped`, `bridged`, `liquid_staking`, `restaking`,
`yield_bearing` or `protocol_receipt`. Exclude LP tokens, multi-asset baskets,
leveraged/index tokens and unrelated assets unless they are separately reviewed
as an ETH-like or BTC-like denomination. Membership in these maintained symbol
lists authorises inclusion regardless of chain or token address.

### Guideline threshold conversion

Add a shared `convert_usd_threshold_to_denomination()` helper alongside the
classifier. It converts one familiar USD guideline into deterministic native
denomination thresholds without fetching live prices:

```text
stablecoin: USD 1
ETH-like:   USD 2,000 per ETH
BTC-like:   USD 60,000 per BTC
```

The helper must first normalise every whitelisted wrapper or protocol receipt
to its canonical wrapped-native underlying: stablecoins to `USD`, ETH-like
tokens to `WETH`/`ETH`, and BTC-like tokens to `WBTC`/`BTC`. It then divides the
USD guideline by the fixed family rate. For the default USD 5,000 guideline,
the resolved thresholds are 5,000 stablecoin units, 2.5 ETH-like units and
approximately 0.08333333 BTC-like units.

Use a typed interface along these lines:

```python
def convert_usd_threshold_to_denomination(
    usd_threshold: Decimal,
    denomination_symbol: str,
) -> Decimal:
    ...
```

The implementation resolves the symbol through the shared whitelist and its
canonical underlying before applying the fixed rate. Unsupported symbols must
raise a configuration error rather than silently receiving a stablecoin rate.

Use the converted value wherever the current cleaner or metadata qualification
needs an absolute low-TVL threshold. Liquid-staking, restaking, yield-bearing
and protocol-specific wrappers use their normalised ETH or BTC underlying; do
not introduce live wrapper exchange-rate calls. This is deliberately a stable
guideline for filtering obviously insignificant observations, not a valuation
oracle or a hard correctness blocker. Falling below it may drive the existing
low-TVL cleaning/qualification behaviour, but must not fail the scan, abort the
crypto phase or make the denomination audit fail.

### Inventory and coverage audit

Add `scripts/erc-4626/audit-crypto-vault-denominations.py`. It should read the
configured vault metadata pickle and, when present, the shared raw price
Parquet without network calls and output a tabulated report containing:

- selected vault counts and current/peak denomination NAV by family and chain;
- unique denomination tokens and vault counts;
- exact stablecoin, ETH and BTC selections;
- classified vaults with no valid raw price row and raw price vaults whose
  denomination is unsupported;
- symbol-like ETH/BTC candidates missing from the maintained whitelist;
- whitelist symbols absent from the current vault database;
- the chain/address/decimals observed for every whitelisted symbol, so symbol
  collisions and unexpected deployments are visible; and
- symbols duplicated across the ETH and BTC lists or conflicting with the
  stablecoin list.

Expose `VAULT_DB`, `UNCLEANED_PRICE_DATABASE` and optional report-file paths
through environment variables. The report must be read-only. Add a strict mode
for CI/rollout validation that exits non-zero on whitelist conflicts, on a
production denomination whose candidate symbol is not classified, or on a
classified active vault without raw coverage. A missing history must be
repaired through the existing address-scoped raw scanner and shared reader
state, not a second crypto-bundle scanner. This makes “all wrapper tokens” an
auditable, maintained symbol-whitelist claim.

Run the audit against a copy of the production metadata pickle before merging
the initial whitelist. Review every unique selected and candidate symbol, then
record the accepted symbol in YAML.

## Cleaning implementation

Refactor `eth_defi/research/wrangle_vault_prices.py` without changing legacy
defaults:

1. Replace the hard-coded internal stablecoin filtering step with a generic
   `filter_vaults_by_denomination_families()` helper.
2. Keep `filter_vaults_by_stablecoin()` as a compatibility wrapper calling the
   generic helper with `{stablecoin}`.
3. Add an explicit `denomination_families` argument to
   `process_raw_vault_scan_data()` and `generate_cleaned_vault_datasets()`;
   default it to stablecoin-only so every existing caller produces the same
   rows and schema.
4. Add a separate daily materialisation helper which runs only for the new
   bundle after common cleaning and writes the legacy sparse `returns_1d`
   compatibility column.
5. Parameterise required-column verification so the legacy output continues to
   require `returns_1h`, while the crypto output additionally requires
   denomination-family columns and `returns_1d`.
6. Parameterise `clean_by_tvl()` to accept a per-vault or per-row absolute
   threshold derived by `convert_usd_threshold_to_denomination()`. Preserve its
   current scalar USD default for every legacy caller.
7. Stamp denomination whitelist, fixed guideline rates and bundle schema
   metadata into the new
   Parquet alongside `metadata.version`.
8. Continue writing a temporary Parquet, verify row count, unique
   `(id, UTC date)` selection, schema, sorted order and readable footer, then
   atomically replace only the crypto destination.

Do not weaken parquet migration failure behaviour. Any failure to read,
migrate, cast or verify the shared raw Parquet must abort this phase and leave
the previous crypto Parquet untouched. Never catch `ArrowInvalid` and reset to
an empty table. The new cleaner is derived from the same raw data, including
unrecoverable old Monad rows, and must never modify or truncate that source.

Add a small orchestration function in `eth_defi/vault/post_processing.py`, for
example `clean_crypto_vault_prices()`, rather than loading a new standalone
script through `importlib`. It should accept all input/output paths explicitly
for focused tests.

## Lifetime metrics and metadata

Create a separate exporter module, for example
`eth_defi/vault/crypto_vaults.py`. Do not add ETH/BTC rows to
`top_vaults_json.main()` and do not point the new exporter at
`top_vaults_by_chain.json` or the existing `vault-export-state.json`.

The module should:

1. read `crypto-cleaned-vault-prices-1d.parquet` and the common vault metadata
   pickle;
2. cross-check every Parquet vault ID against metadata;
3. select only the three allowed denomination families using the same shared
   classifier as cleaning;
4. calculate regular daily returns and lifetime/period metrics from
   `share_price` using the existing forward-filled
   `prepare_daily_share_price_series()` helper; never treat the legacy sparse
   `returns_1d` column as fixed-cadence input;
5. reuse common risk, fees, curator, deposit/redemption, settlement, flow,
   Core3 and Xerberus enrichment where unit semantics remain valid;
6. write its own sticky state at
   `crypto-vaults/crypto-vault-export-state.json`;
7. serialise its own `crypto-vault-metadata.json`; and
8. atomically write and validate JSON before publication.

Extract reusable calculations from `top_vaults_json.py` where necessary, but
keep selection, thresholds, filenames, object keys and sticky-state paths in
bundle-specific configuration. Avoid copying the full exporter and allowing
the two implementations to drift.

### Metadata schema

The top-level `crypto-vault-metadata.json` should contain:

```json
{
  "bundle": "crypto-vaults",
  "schema_version": 1,
  "generated_at": "...",
  "metadata": {"version": {}},
  "denomination_families": ["stablecoin", "eth", "btc"],
  "vaults": []
}
```

Each vault record must explicitly contain:

- vault, chain, protocol and curator identity;
- denomination symbol, token address, decimals, family, wrapper kind and
  canonical underlying;
- share-price source and observation range;
- current and peak total assets with `total_assets_unit`;
- returns, CAGR, volatility, Sharpe and drawdown metrics in denomination-token
  terms;
- fees, risk, flags, strategy tags and technical/deposit metadata;
- period results whose sample counts refer to the daily series; and
- a `stablecoinish` compatibility field only if existing consumers need it.

Rename or qualify USD-specific labels in the new schema. In particular, do not
describe native ETH/BTC `current_nav`, `peak_nav`, flow or liquidity values as
USD. Prefer `current_total_assets`, `peak_total_assets`,
`total_assets_unit`, and denomination-qualified flow fields. If common metric
internals continue to use the historical `current_nav` variable name, convert
only at the new serialisation boundary and document the internal legacy name.

Use `convert_usd_threshold_to_denomination()` for minimum peak-total-assets
qualification rather than applying the stablecoin `MIN_TVL=5000` number
directly to ETH-like and BTC-like denomination-token units. Configure one base
guideline with a default of USD 5,000:

```text
CRYPTO_VAULTS_MIN_TVL_USD=5000
```

Record the base USD guideline, fixed ETH/BTC rates and resolved family threshold
in the manifest and each vault record. The conversion deliberately treats a
whitelisted wrapper as its canonical ETH or BTC underlying and is approximate;
it does not make TVL rankings across wrappers a live USD valuation. Do not use
live USD or wrapper exchange rates to change membership from run to run.

Keep sticky qualification family-aware. A stablecoin qualification must not be
replayed after a token is corrected to ETH/BTC or vice versa. Store family,
denomination identity, threshold and schema version with each sticky record,
and structurally suppress a fallback whose current classification conflicts
with stored classification.

Follow the current `top_vaults_json.main()` commit timing exactly: validate the
metadata JSON and sticky state, atomically write the metadata JSON, then
atomically save the sticky state locally before R2 publication. If R2
publication later fails, retain the locally advanced sticky state and publish
it on the next successful cycle. Do not add a second transaction protocol for
the crypto bundle.

## New post-processing phases

Extend `run_post_processing()` with three explicit, independently reported
phases after native raw-price merges:

```text
clean-crypto-vault-prices
calculate-crypto-vault-metadata
export-crypto-vault-bundle
```

The complete relevant order should be:

1. merge native protocol prices into the shared raw Parquet;
2. run the existing stablecoin cleaner unchanged;
3. build the isolated crypto-vault daily cleaned Parquet inside its guarded
   phase;
4. run the existing stablecoin top-vault JSON export;
5. calculate the isolated crypto-vault lifetime metadata and manifest inside
   its guarded phase;
6. run existing sparkline, protocol metadata, data-file and sample exports;
7. upload the complete crypto bundle to private R2; and
8. create private R2 daily backups only after all bundle uploads and the
   manifest commit succeed.

The crypto phases need their own failure gate:

- crypto cleaning failure skips crypto metadata and crypto upload;
- crypto metadata/manifest failure skips crypto upload;
- crypto failure does not publish stale crypto files and does not block the
  existing stablecoin/public exports;
- existing stablecoin cleaning failure keeps its current downstream gates;
  and
- every exception raised within a crypto helper is contained at the
  post-processing orchestration boundary, recorded as a failed crypto step and
  logged with its traceback without escaping to the scanner cycle.

The three crypto-vault phases are a mandatory part of post-processing with no
separate escape hatch. Follow the existing guarded-phase examples in
`scan-vaults-all-chains.py` and `post_processing.py`: helpers may raise normally,
but their orchestration wrapper catches `Exception`, logs it with
`logger.exception()`, marks the step false and continues the scanner. Validate
the alternative bucket configuration and credentials at scanner startup inside
the same kind of guarded boundary. Record a failed validation for later crypto
publication, but continue the scan and all existing public exports. Reuse the
existing R2 endpoint/access credentials and
`R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME`; do not add a second secret set.

Pass the following explicit paths from `scan_all_chains.py` into post-processing:

```text
crypto_vaults_dir
crypto_cleaned_price_path
crypto_metadata_path
crypto_manifest_path
crypto_sticky_state_path
```

Do not make helpers rediscover these paths through global defaults during
tests.

## Private R2 export and backups

Add a dedicated exporter in `eth_defi/vault/crypto_vault_export.py` or beside
the bundle builder. It should reuse:

- `create_r2_client()`;
- digest-aware `upload_file_to_r2()` / `upload_bytes_to_r2()`;
- Brotli JSON compression conventions; and
- `copy_r2_object_daily_backup()`.

It must not reuse `data_file_export.main()` because that function deliberately
targets both primary and alternative buckets with flat keys.

Publication rules:

1. require `R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME`, with startup validation
   errors contained and carried forward as a failed crypto publication step;
2. upload only to that bucket;
3. preserve `UPLOAD_PREFIX` in every key and use the flat `crypto-` object
   names from the output contract;
4. upload Parquet, metadata JSON, Brotli metadata JSON and sticky state before
   `crypto-vault-manifest.json`;
5. treat digest matches as successful unchanged uploads;
6. publish the manifest last;
7. after a complete successful publication, copy the committed manifest and
   every payload key it references to `daily/YYYY-MM-DD/<full source key>` when
   `R2_DAILY_BACKUP` is enabled; and
8. log one grep-friendly success line containing bundle, manifest key, row
   count, vault counts by family and commit hash.

Back up the local crypto sticky state, manifest, metadata and cleaned Parquet by
adding their paths to the existing pre-scan `backup_pipeline_files()` list.
Keep the existing flat-basename, first-backup-of-day and retention behaviour
unchanged. The crypto-specific local basenames in the output contract prevent
collisions with existing pipeline files. Although cleaned data and metadata can
be regenerated, sticky state is not safely regenerable and is mandatory.

## Configuration and deployment wiring

Update `scripts/erc-4626/scan-vaults-all-chains.py` documentation and
`docker-compose.yml` with:

| Variable | Purpose |
|---|---|
| `CRYPTO_VAULTS_MIN_TVL_USD` | Base USD guideline converted with fixed USD 2,000/ETH and USD 60,000/BTC rates; default 5,000 |

Reuse `PIPELINE_DATA_DIR`, `UPLOAD_PREFIX`,
`R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME`, the existing R2 credentials and
`R2_DAILY_BACKUP`. Do not overload `FREQUENCY`; it controls raw historical
sampling, while the bundle output is always daily.

## Tests

### Denomination classification

Add focused tests for:

1. representative native, wrapped, bridged, liquid-staking, restaking,
   yield-bearing and protocol-specific receipt symbols matching their whitelist
   family;
2. case-insensitive matching after narrow symbol normalisation;
3. an unlisted symbol remaining `unsupported` regardless of address or vault
   name;
4. WBNB, WAVAX, WHYPE and WMON remaining unsupported unless explicitly added
   to an allowed family whitelist;
5. stablecoin membership matching `is_stablecoin_like()` exactly;
6. a symbol listed in both ETH and BTC, or in crypto and stablecoin lists,
   failing at load time;
7. bridge suffixes matching only when explicitly whitelisted; and
8. observed chain/address/decimal collisions being surfaced by the audit
   without changing symbol-based classification;
9. USD 5,000 resolving to 5,000 stablecoin units, 2.5 ETH-like units and the
   fixed-rate BTC equivalent;
10. every ETH/BTC wrapper kind resolving through its canonical wrapped-native
    underlying; and
11. unsupported symbols failing threshold conversion explicitly.

### Cleaning and daily output

Extend `tests/research/test_clean_prices.py` or add a focused crypto-bundle
module covering:

1. legacy `generate_cleaned_vault_datasets()` remains stablecoin-only by
   default;
2. crypto selection contains the exact union of stablecoin, ETH and BTC IDs;
3. unrelated denominations such as SOL, BNB, AVAX, HYPE and arbitrary ERC-20s
   are excluded;
4. multiple observations on one UTC date select one deterministic last row;
5. missing dates do not create rows;
6. selected rows preserve original timestamps and block numbers;
7. the legacy sparse `returns_1d` field is isolated per vault and starts from
   zero;
8. denomination-valued columns retain native units;
9. crypto output verification failure preserves the old destination; and
10. a raw Parquet read/migration failure is raised rather than treated as an
    empty input.

Also cover a sparse observation series and assert that the Parquet retains only
real observation dates while `prepare_daily_share_price_series()` creates the
forward-filled calendar-day calculation series with zero returns on missing
days.

Include a mixed stablecoin/ETH/BTC fixture with whitelisted symbols appearing
at multiple addresses and chains to prove classification is symbol-based.

### Lifetime metadata

Add tests for:

1. all three families reaching the metadata exporter;
2. metrics consuming the existing forward-filled regular daily return series
   and calendar-day sample counts;
3. ETH/BTC totals not being labelled USD;
4. fixed-rate guideline thresholds and sticky qualification;
5. sticky state isolation from the existing `vault-export-state.json`;
6. a changed or conflicting denomination family suppressing stale replay;
7. protocol/curator/risk enrichment being restricted to exported vaults;
8. JSON and Parquet sharing build provenance;
9. metadata JSON being atomically written before sticky state, matching the
   existing exporter; and
10. corrupt crypto sticky state aborting only the crypto metadata phase without
   resetting it.

### Orchestration, R2 and backup

Extend `tests/erc_4626/test_post_processing.py`,
`tests/erc_4626/test_export_data_files.py` and R2 tests to prove:

1. phase order and explicit path forwarding;
2. each crypto failure gate skips stale downstream crypto publication;
3. current stablecoin/public phases still run after an ordinary crypto failure;
4. the existing public data-file list is unchanged;
5. crypto keys go only to the alternative bucket as flat `crypto-` names;
6. `UPLOAD_PREFIX` is honoured;
7. the manifest is uploaded last;
8. no daily backup is made after a partial upload;
9. the committed manifest and all flat payload keys it references,
   including sticky state, receive an alternative-bucket daily backup after
   success;
10. unchanged digest skips count as successful publication;
11. local pre-scan backups include the crypto bundle without basename
    collisions; and
12. absent or invalid alternative-bucket configuration marks crypto publication
    failed while existing scanner and public-export phases remain completed.

Run focused tests with the required environment prefix and extended timeout:

```shell
source .local-test.env && poetry run pytest \
  tests/research/test_clean_prices.py \
  tests/erc_4626/test_crypto_vaults.py \
  tests/erc_4626/test_post_processing.py \
  tests/erc_4626/test_export_data_files.py \
  tests/test_cloudflare_r2.py \
  --timeout=180
```

Format changed Python with `poetry run ruff format`. Do not run a local Sphinx
build.

## Production-data acceptance

Before enabling production publication:

1. Copy `.local-test.env` into the worktree as instructed, without editing it.
2. Download or copy the current production metadata pickle and raw Parquet to
   an isolated `PIPELINE_DATA_DIR`. Preserve the originals.
3. Run the denomination audit and manually review every ETH/BTC candidate and
   every unclassified wrapper-like symbol before updating the whitelist.
4. Generate both the legacy stablecoin cleaned file and the new crypto daily
   bundle from the same raw snapshot.
5. Assert the crypto stablecoin vault-ID set exactly matches the legacy
   stablecoin vault-ID set. Any difference is a blocker unless separately
   explained by daily selection.
6. Assert every crypto row belongs to a metadata vault and one allowed family,
   `(id, UTC date)` is unique, timestamps are ordered and no family has an
   unexplained zero-row result.
7. Spot-check representative direct and nested ETH/BTC wrapper symbols and
   confirm the classifier result follows the whitelist, independent of chain
   and token address.
8. Compare lifetime metrics for stablecoin vaults with the existing exporter;
   return/period results should agree after accounting for the explicit daily
   materialisation.
9. Inspect ETH/BTC metrics for unit correctness and ensure the fixed-rate
   guideline thresholds admit the intended population without acting as a
   rollout blocker.
10. Run a staging R2 upload with `UPLOAD_PREFIX=test-`, list the resulting keys,
    verify digests and Brotli decoding, and confirm no primary-bucket key was
    created.
11. Confirm the manifest is the last object published and that all staging
    objects appear under the dated alternative-bucket backup prefix.
12. Run one production scanner cycle with normal public exports enabled and
    compare the existing public object names/schema/row membership to the
    pre-change contract.

For Monad, never delete or rebuild raw rows before the provider's dynamically
detected historical-state boundary. This feature derives its output from the
preserved production raw Parquet and does not change Monad reader state.

## Documentation changes

Update:

- `scripts/erc-4626/README-vault-scripts.md` with the three new phases, output
  contract, configuration, private-only R2 keys, backup layout and audit
  command;
- `docs/source/tutorials/erc-4626-scan-prices.rst` so it distinguishes the
  existing stablecoin hourly cleaned file from the new mixed-family daily
  bundle;
- `eth_defi/data/README.md` with the denomination-symbol whitelist schema;
- `docs/source/api/vault/index.rst` with stubs for any new public API modules;
  and
- the `scan-vaults-all-chains.py` module environment-variable documentation.

Use British English, sentence-case headings and `onchain` spelling. Do not edit
generated `_autosummary` files.

## Rollout and rollback

Roll out in four stages:

1. Merge the classifier, symbol whitelist and audit without enabling
   publication.
2. Generate and validate the local bundle from production copies; freeze the
   reviewed initial whitelist, USD guideline and fixed ETH/BTC rates.
3. Publish from a staging deployment with `UPLOAD_PREFIX=test-`, validate
   consumer parsing and backup restoration, then remove only the staging prefix
   when authorised.
4. Deploy the mandatory crypto phases to production with the flat root
   `crypto-` keys.
   Consumers must require a valid manifest and supported schema version before
   reading payload files.

Rollback is isolated and daily granularity is sufficient. Pause the looped
scanner, deploy the previous scanner build and restore all flat
`crypto-` objects together from the selected dated private R2 backup.
Restore the matching local files from the same day's existing local backup when
needed, including sticky state. Do not modify, delete or republish the existing
stablecoin objects during crypto-bundle rollback. Retain the shared raw Parquet
and reader state unchanged.

## Completion criteria

- The classifier identifies stablecoin, ETH and BTC denomination families from
  the maintained symbol whitelist and the production audit has no unresolved
  wrapper candidates.
- The legacy stablecoin cleaner and public exports retain their existing
  selection, filenames and schemas.
- The crypto daily Parquet has at most one real observation per vault and UTC
  day, preserves denomination units and contains no unsupported denomination.
- Lifetime metadata uses the daily series, has family-aware units, thresholds
  and sticky state, and carries matching provenance with the Parquet.
- Only the alternative/private R2 bucket contains the flat `crypto-` live
  objects and their dated backups.
- A partial upload cannot advance the manifest or create a backup advertised as
  complete; consumers reject flat payloads whose digests do not match the
  current manifest.
- Focused unit/integration tests and production-copy acceptance checks pass.
