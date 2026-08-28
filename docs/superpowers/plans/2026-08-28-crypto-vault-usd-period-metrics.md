# Crypto vault USD period metrics and exchange-rate Parquet plan

## Goal

Extend the private crypto-vault export so ETH- and BTC-denominated vault JSON
records contain `periodic_metrics_usd`: the same `PeriodMetrics` structure as
the existing native-denomination `period_results`, calculated after converting
the daily share-price and TVL bars to USD with the historical currency API
series.

Also publish a cleaned `exchange-rates.parquet` companion to the existing
`exchange-rates.duckdb` R2 data-file export, and show both base-denomination and
USD lifetime CAGR in the crypto performance examiner.

This is additive. Do not change the public stablecoin export, the crypto price
Parquet schema, native `period_results`, qualification thresholds or sticky
selection behaviour.

## References reviewed

- `docs/superpowers/plans/2026-08-27-crypto-vaults-export.md`: existing private
  bundle contract, native-unit semantics and failure isolation.
- `eth_defi/currency_api/README-currency-api.md`: rate direction, data floor,
  known-bad-rate cleaning and current DuckDB R2 export.
- `eth_defi/currency_api/database.py` and `cleaning.py`: stored schema and the
  sanitised read path.
- `eth_defi/research/vault_metrics.py`: `PeriodMetrics`, daily forward filling,
  `calculate_period_metrics()`, `calculate_vault_record()` and
  `calculate_lifetime_metrics()`.
- `eth_defi/vault/crypto_vaults.py` and `crypto_vault_export.py`: crypto JSON,
  local paths, manifest and private R2 publication.
- `eth_defi/vault/data_file_export.py`: flat public/alternative data-file keys
  and alternative-bucket daily backups.
- `scripts/erc-4626/export-crypto-vaults.py` and
  `examine-crypto-vault-performance.py`: standalone build and operator report.
- `scripts/erc-4626/README-vault-scripts.md`: current production and operator
  documentation.

## Current findings and coverage

There is no current `calculate_lifetime_rows()` function on `master`. The
corresponding implementation path is `calculate_lifetime_metrics()`, which
groups vaults and calls `calculate_vault_record()` for each vault. Add the
opt-in USD calculation there rather than creating a second ambiguously named
lifetime pipeline.

The local production-shaped data checked on 2026-08-28 has:

- BTC rates: 2024-03-02 through 2026-08-25, 906 daily rows;
- ETH rates: 2024-03-02 through 2026-08-25, 906 daily rows;
- one raw missing day for both currencies, 2025-12-10;
- one additional known-bad day removed by `filter_known_bad_rates()`,
  2025-12-06;
- crypto prices through 2026-08-26; and
- 399 selected BTC vaults and 1,183 selected ETH vaults. Of these, 36 BTC and
  239 ETH vaults start before exchange-rate history begins.

The provider builds the file labelled date D from rates fetched just after
00:00 UTC on D. Treat it as a start-of-day D snapshot, equivalent to the prior
UTC end-of-day vault bar: shift its effective vault date to D-1. The raw
2024-03-02 through 2026-08-25 rows therefore cover effective vault dates
2024-03-01 through 2026-08-24. This convention follows the provider's
midnight-scheduled publication workflow and the generated response date; link
and document both in the implementation.

The available effective rates cover all recent one-week through one-year
lookbacks, apart from small fillable gaps, but cannot provide a true USD view
of native history before 2024-03-01. For older vaults, USD `lifetime` means the
maximum common vault/rate window beginning no earlier than 2024-03-01. Preserve
the effective range in the existing `PeriodMetrics.period_start_at`,
`samples_start_at`, `samples_end_at` and sample-count fields. Do not backfill or
fabricate pre-2024 rates.

## 1. Start from a new branch and worktree

1. Fetch `origin/master` and verify merge commit `ecc96c8f4` is its ancestor.
   Also verify by content that `crypto_vaults.py`, `crypto_vault_export.py` and
   `tests/vault/test_crypto_vaults.py` contain the merged PR #1530 hardening,
   including optional Brotli import handling and the isolated flat-key
   publication test. Squash history will not contain the feature branch's
   individual fix commits, so do not mistake their absence for missing code.
2. Create a new worktree and feature branch from current `origin/master`, for
   example branch `add-crypto-vault-usd-metrics` in a sibling worktree named
   `crypto-vault-usd-metrics`.
3. Follow the repository worktree setup: ensure `.venv` and `.claude` resolve
   to the parent checkout and copy the gitignored `.local-test.env` from the
   main checkout when it is absent. Never edit `.local-test.env`.
4. Confirm a clean worktree and run all Python/test commands through Poetry.

## 2. Materialise and export `exchange-rates.parquet`

Add a focused currency API Parquet helper, preferably
`eth_defi/currency_api/parquet.py`, with typed functions to load one cleaned
read-only DuckDB snapshot and atomically materialise it as a sanitised Parquet
file. Accept separate explicit `source_path` and `destination_path` arguments:
an overridden DuckDB may live outside the pipeline directory, but the Parquet
destination remains `$PIPELINE_DATA_DIR/exchange-rates.parquet`.

The output contract is:

```text
$PIPELINE_DATA_DIR/exchange-rates.parquet
R2 flat key: exchange-rates.parquet
UPLOAD_PREFIX=test- -> test-exchange-rates.parquet
```

The Parquet contains all stored currencies, not only BTC and ETH, with stable
columns in this order:

```text
date, base_currency, quote_currency, rate, source, written_at
```

Pin the physical schema as `date32`, UTF-8 strings for the three
currency/source columns, `float64` for `rate`, and `timestamp[us]` without a
timezone for the naive-UTC `written_at` value. Do not let Pandas inference
change this contract.

Materialisation rules:

1. Open the configured `exchange-rates.duckdb` with a focused
   `duckdb.connect(..., read_only=True)` reader and select the `exchange_rates`
   table. Do not instantiate `CurrencyRateDatabase`: its constructor opens
   read-write, creates directories and initialises/migrates schema. Always close
   the read-only connection.
2. Apply `filter_known_bad_rates()`; the DuckDB remains the auditable raw
   source and the Parquet is the cleaned consumer view.
3. Preserve the stored direction: `rate` is quote units per one base unit. Do
   not invert values in the exported Parquet.
4. Validate finite positive rates, required columns, sorted
   `(date, base_currency, quote_currency, source)` order and uniqueness of that
   logical key.
5. Write Zstandard-compressed Parquet to a temporary file, verify it can be
   read with the expected row count/schema, then atomically replace the prior
   file. Preserve each source row's `written_at`; do not stamp the
   materialisation time into row data. A conversion failure must never replace
   the last good file.

Materialise the cleaned snapshot once in post-processing after the currency
scan and before crypto metadata calculation. Pass that exact cleaned snapshot
or its verified Parquet to the USD conversion context; do not reread and clean
the mutable DuckDB independently for metadata. Update
`eth_defi/vault/data_file_export.py` so `export-data-files.py` uploads the
already materialised Parquet next to the existing DuckDB rather than creating a
second snapshot later. Source resolution must use the same
`CURRENCY_API_DB_PATH`/`CURRENCY_API_DATABASE_PATH` contract, while destination
resolution always uses the active pipeline data directory.

Upload the new flat key to the same primary and alternative data buckets as
the DuckDB and include it in the existing alternative-bucket
`daily/YYYY-MM-DD/...` backup loop. Do not add a duplicate
`crypto-exchange-rates.parquet` key or place the general rate file inside the
crypto manifest. If the source DuckDB is absent, log and omit both the newly
generated Parquet and any stale Parquet left from an earlier run. If the source
exists but fresh materialisation fails, omit the stale Parquet, log an error
with its traceback, and continue uploading unrelated data files. After those
uploads/backups complete, propagate the accumulated materialisation failure so
the guarded `export-data-files` step reports failure without suppressing the
other artefacts. Retain the last good local Parquet for diagnosis and recovery,
but exclude its path explicitly from that run's upload and backup lists; do not
delete or silently republish it. Carry the materialisation success flag into
the data-file path builder so this exclusion does not depend on deleting or
renaming the old file.

In the scheduled all-chain flow, publish a newly built crypto bundle only after
that run's `export-data-files` step succeeds. If data export is skipped or
fails, retain the local crypto artefacts but do not advance the private R2
bundle, because its `usd_metrics` digest would not have a confirmed matching
rate Parquet in the data export. The existing step-level boolean is sufficient:
gate conservatively on the whole data-file export instead of adding a new
generation manifest.

For `export-crypto-vaults.py` standalone publication, reuse a focused shared
uploader to place the exact local `exchange-rates.parquet` in the alternative
bucket and its normal daily backup before publishing the crypto bundle. Abort
only the standalone crypto publication if this companion upload fails. Local
build mode (`CRYPTO_VAULTS_PUBLISH=false`) does not require R2.

If a new public `eth_defi.currency_api.parquet` module is added, include it in
the currency API Sphinx index as required for API modules.

## 3. Build a reusable USD conversion context

Add a small `@dataclass(frozen=True, slots=True)` in the vault layer to carry
cleaned USD-per-underlying daily series for `ETH` and `BTC`, plus an immutable
vault-ID-to-family mapping. Keep generic DuckDB reading and Parquet
materialisation in `currency_api`; the vault-ID classification is crypto-bundle
policy and does not belong there. Build the mapping from the same
`VaultDatabase` denomination-symbol classifier used by the crypto bundle before
calling `calculate_lifetime_metrics()`. This avoids depending on
`canonical_underlying`, which is added only later by
`build_crypto_vault_record()`. Load the rates and construct the mapping once per
crypto metadata build, not once per vault.

Construction rules:

1. Select only `base_currency='usd'`, `source=SOURCE_NAME` and
   `quote_currency IN ('eth', 'btc')` from the already cleaned immutable
   snapshot used to create `exchange-rates.parquet`.
2. Assert the snapshot has already passed `filter_known_bad_rates()`; cleaning
   occurs exactly once before either metrics or Parquet materialisation.
3. Invert the stored quote-per-USD rate to USD per ETH/BTC and reject zero,
   negative, duplicate or non-finite values. Add deliberately broad,
   family-specific USD-per-unit sanity bounds that accept plausible long-term
   ETH/BTC prices but reject an accidentally uninverted sub-one rate. Keep the
   bounds as documented constants and test their boundaries. Treat a bound
   violation as a validation failure for that currency family: emit the
   family-specific invalid-rate error periods described below while the other
   family and all native metrics continue.
4. Normalise DuckDB `DATE` values explicitly to midnight on a naive UTC daily
   `DatetimeIndex`, then subtract one calendar day to form the effective vault
   date. Never join a Python `date`, timezone-aware timestamp and naive vault
   timestamp implicitly.
5. Reindex each currency to calendar days from its first valid rate through the
   last UTC date present in the crypto price DataFrame. Forward fill at most
   three consecutive missing days, including at the trailing edge. Thus the
   observed one-day publication lag keeps the vault's last date, while a tail
   longer than three days is usable only through the third filled day. This
   also covers isolated missing/known-bad dates without permitting indefinitely
   stale valuation.
6. Never backward fill before effective date 2024-03-01. A vault that predates
   rate history is truncated to the overlapping window for its USD metrics.
7. Reserve a build-level error for an absent or unreadable exchange-rate
   database. If only one required currency series is entirely absent, retain
   native metrics and emit a full `periodic_metrics_usd` list with an explicit
   missing-rate `error_reason` for vaults in that family; the other family
   continues normally. A trailing gap within three days uses the bounded
   forward-filled rate; beyond that, truncate the USD metric window to the
   third filled day. An internal gap longer than the three-day allowance splits
   the usable data: calculate a period only from the latest contiguous covered
   segment that satisfies the established period rules, otherwise return a
   `PeriodMetrics.error_reason` for that period. One bad currency or period
   must not remove native metrics or unrelated vaults.

Use stable error-reason constants/messages that distinguish at least missing
currency series, invalid currency series, insufficient contiguous rate
coverage and the existing insufficient-vault-samples cases. The examiner may
display all errored periods as `N/A`, but tests and operators must be able to
tell why the USD calculation degraded.

Use the context's precomputed family mapping for conversion and write the same
mapping later as each JSON record's existing `canonical_underlying`: all
reviewed ETH-like wrappers use ETH/USD and all reviewed BTC-like wrappers use
BTC/USD. This is a canonical-underlying benchmark, not a wrapper
redemption-price oracle; it intentionally does not model stETH/ETH,
restaking-token/ETH or protocol receipt-token exchange-rate drift.

The existing crypto price validation rejects unsupported denominations before
common metric calculation. Keep that invariant and log per-currency input
coverage plus final successful and degraded vault counts; do not add a second
unreachable unsupported-family branch to the USD context.

## 4. Calculate `periodic_metrics_usd`

Thread an optional exchange-rate context through
`calculate_lifetime_metrics()` to `calculate_vault_record()`. The default is
`None`, so every existing caller and the public stablecoin pipeline retain
their current behaviour and output columns.

Refactor the small repeated portion of `calculate_vault_record()` into a
helper that calculates the complete list of `PeriodMetrics` for supplied
inputs. Preserve the current distinction between sparse real observations and
the regular daily curve: `calculate_period_metrics()` derives `raw_samples`,
sample endpoints, gross return, fee duration and eligibility from sparse
observations, while `daily_samples`, volatility, Sharpe and drawdown use the
regular daily curve. Reuse the helper for both native and USD views without
collapsing these two inputs, so sample semantics cannot drift.

For an ETH/BTC vault:

1. Preserve the original sparse real share-price observations. Join each
   observation's UTC date to the bounded effective-date rate series and produce
   a sparse USD-valued observation series for raw counts, endpoints and returns.
2. Separately prepare the existing forward-filled native daily share-price bars
   for the covered segment and multiply them by the regular effective-date rate
   series to produce the daily USD curve used by risk metrics.
3. A provider row labelled D is a start-of-day D snapshot and applies to the
   vault's end-of-day calendar bar D-1. Shift the rate's effective date back one
   day exactly once; do not also shift vault timestamps. Add a stepped-rate
   fixture plus first/last coverage assertions so the convention cannot regress.
   Document that using a midnight snapshot for the preceding end-of-day bar is
   still an approximation because the external source does not publish an exact
   fixing timestamp.
4. Calculate `share_price_usd = share_price_native * underlying_usd_rate` for
   both the sparse endpoint series and regular daily curve.
5. Forward-fill the vault's daily native TVL consistently and calculate
   `tvl_usd = total_assets_native * underlying_usd_rate`.
6. Recalculate daily returns from the regular `share_price_usd`; never multiply
   the native return column by an exchange rate.
7. Call the shared period helper for every established lookback, including
   `lifetime`, and store the result as `periodic_metrics_usd`.

Apply the bounded-rate coverage before calling
`prepare_daily_share_price_series()` or any extracted helper that regularises
prices. Split on every rate gap longer than three days and pass only the latest
contiguous covered segment eligible for that requested period. Filter the
sparse real-observation series to the same segment, but never count a synthetic
daily bar as a raw observation. Never pass NaN rate gaps to the native
forward-fill helper: it would turn a deliberately uncovered USD interval into
a stale flat price and defeat the three-day bound. If the selected segment
cannot satisfy the established period rules, return the explicit
insufficient-rate-coverage error instead of bridging the gap.

`periodic_metrics_usd` must serialise as the same list-of-dictionaries shape as
`PeriodMetrics`. Its share-price and TVL fields are USD; its returns and risk
statistics describe a USD investor's combined vault performance and ETH/BTC
market movement. Keep ranking fields `null` in this change rather than copying
native-unit rankings or introducing a new ranking population.

Do not apply an externalised performance fee to ETH/BTC market appreciation.
For each USD period, calculate the investor's native-denomination net return
over the exact USD sample endpoints with the existing fee-mode-adjusted native
fee logic, then compose it with the underlying FX factor:

```text
usd_net_factor = native_net_factor * (rate_end / rate_start)
usd_net_return = usd_net_factor - 1
```

Gross return, volatility, Sharpe and drawdown still come from the converted USD
price series. This composition applies management/performance fees to vault
performance in its denomination token while entry/exit fees still scale the
investor's final holding; it does not charge a vault performance fee on an
unrelated ETH/BTC/USD move. Extend the common period calculation with an
explicit optional native fee-basis/FX-return path (or an equivalently small
typed strategy), rather than silently feeding USD prices to
`calculate_net_profit()`. Internalised fees remain already reflected in the
native share price through `get_net_fees()`. State these invariants in the
helper docstring and cover both internalised and externalised fee modes in
tests.

Document that permitted one-to-three-day forward-filled rate gaps create
flat-then-jump USD returns, so short-window volatility, Sharpe and drawdown
retain the same cadence-sensitive approximation caveat as native sparse bars.
Reuse the existing zero annual risk-free-rate convention for both native and
USD Sharpe calculations.

Keep the existing native `period_results`, top-level `cagr`/`cagr_net`,
`current_total_assets` and `peak_total_assets` unchanged. Do not add
`cagr_usd`, USD columns to `CleanedVaultPriceRow`, or other duplicate top-level
fields. `export_lifetime_row()` already recursively serialises dataclasses, so
the new list should use that path rather than a second JSON encoder.

Only ETH- and BTC-family records receive `periodic_metrics_usd`; omit it from
stablecoin records to avoid duplicating their existing USD-like native metrics.
Treat this as an additive JSON field within the current compatible crypto
schema version. Do not bump the shared sticky-state schema or require a
migration script; the next successful crypto metadata cycle generates the new
field while preserving sticky qualification state.

Pass the verified cleaned snapshot/Parquet path explicitly into
`build_crypto_vault_metadata()` and through the guarded post-processing
wrapper. Resolve the DuckDB source and pipeline-directory Parquet destination
separately in `export-crypto-vaults.py` so scheduled and standalone builds use
the same snapshot contract.

Add one bundle-level `usd_metrics` provenance section to
`crypto-vault-metadata.json` containing the source, base currency, stored and
applied rate directions, per-currency input min/max dates and row counts,
three-day fill allowance, cleaning-policy digest and a deterministic digest of
the cleaned, pre-fill ETH/BTC rows materialised in `exchange-rates.parquet`.
Coverage fields may describe the bounded reindexed series, but the digest must
remain reproducible from the published cleaned Parquet. The existing per-record
`canonical_underlying` already states the rate basis, so do not add a duplicate
`usd_rate_basis` field. Confirm no fixed-schema Parquet writer consumes the
opt-in lifetime DataFrame column; the new nested data must remain JSON-only.

## 5. Update the performance examiner

The table is produced by
`scripts/erc-4626/examine-crypto-vault-performance.py`, not by the export
script. Update the examiner while leaving
`scripts/erc-4626/export-crypto-vaults.py` responsible only for building and
publishing artefacts.

Rename the current displayed CAGR column to `Lifetime CAGR (base)` and add
`Lifetime CAGR (USD)`. The base compatibility fields are top-level `cagr_net`
with fallback to top-level `cagr`. The nested `PeriodMetrics` fields are
`cagr_net` with fallback to `cagr_gross`. Read the latter from the
`period='lifetime'` entry in `periodic_metrics_usd` and show `N/A` only when the
USD period has an `error_reason` or insufficient samples. Treat a supposedly
successful USD period missing both documented CAGR keys as an invalid export,
not as an ordinary `N/A` result.

Add a `USD from` date column sourced from the USD lifetime
`samples_start_at`. This prevents the two CAGR columns being mistaken for the
same window when native history predates effective date 2024-03-01. Keep it
visible for all rows, using `N/A` when no USD lifetime window exists.

Keep the existing stablecoin/blacklist exclusions, approximate latest USD TVL,
sorting, `LIMIT` behaviour and tabulated output. Update the module description
to distinguish:

- base CAGR: vault share performance in the denomination family; and
- USD CAGR: base performance plus the canonical ETH/BTC USD move over the
  available exchange-rate window.

## Tests

Add focused coverage without running the full suite.

### Currency Parquet and R2

- Materialise a small DuckDB fixture and assert column order, logical-key
  uniqueness, sorting, exact Parquet logical/physical types, Zstandard
  readability and atomic replacement. Assert `written_at` remains source
  provenance rather than materialisation time.
- Set `CURRENCY_API_DB_PATH` to a source outside the pipeline directory and
  assert the read-only source is neither created nor mutated while the Parquet
  is written to the explicit pipeline-directory destination.
- Prove the known-bad BTC/ETH cells are absent while ordinary currencies and
  the raw quote-per-base direction remain unchanged.
- Assert `exchange-rates.parquet` joins the existing data-file path list,
  honours `UPLOAD_PREFIX`, uploads to both configured data buckets and receives
  only the established alternative-bucket daily backup.
- Assert an absent DuckDB cannot cause an old Parquet to be uploaded and a
  failed fresh conversion does not upload stale output while unrelated files
  still upload and the guarded step ultimately reports failure.
- Assert scheduled crypto publication is skipped when that run's data-file
  export is skipped or fails, while public processing and local crypto artefacts
  remain intact. Assert standalone publication uploads and backs up the matching
  alternative-bucket rate Parquet before the crypto manifest.
- Materialise unchanged input twice and assert identical bytes/digest with no
  temporary files left behind. Verify prefix/key consistency across primary,
  alternative and alternative daily-backup calls.
- Reject zero, negative, non-finite and duplicate logical-key fixture rows while
  preserving the prior good Parquet.

### USD conversion and metrics

- Verify inversion, ETH/BTC canonical mapping, finite-value validation and
  three-day bounded forward fill, including the broad direction sanity bounds.
- Verify there is no backward fill before the first rate, a long trailing gap
  truncates the window, and a longer internal gap degrades only affected
  periods/vaults with an explicit error reason. Specifically prove the shared
  daily preparation cannot refill a greater-than-three-day rate gap and that a
  successful post-gap period shifts `samples_start_at` to the covered segment.
- Assert provider date D becomes effective vault date D-1, including the first
  and last available rows and a stepped rate. For every lookback after a long
  gap, assert `raw_samples` counts only real vault observations,
  `daily_samples` counts the regular covered curve, and sample/period endpoints
  retain their established meanings.
- Use a flat one-ETH vault with a rising ETH/USD series to prove native CAGR is
  zero while USD CAGR is positive, with USD TVL fields converted correctly.
- Verify BTC uses BTC rates, wrapper symbols use their canonical family, and
  stablecoin records do not gain `periodic_metrics_usd`.
- Verify the USD lifetime sample start is clamped to rate coverage for a vault
  beginning before effective date 2024-03-01.
- Assert native `period_results`, compatibility CAGR fields, qualification and
  the cleaned crypto Parquet schema are unchanged.
- Assert USD rankings remain null and JSON serialisation contains the complete
  `PeriodMetrics` key set without non-finite values.
- With an externalised non-zero performance fee, use a flat native price and a
  rising ETH/USD rate to prove the FX gain is not performance-fee charged. Also
  cover a positive native gain, management/deposit/withdrawal fees and an
  internalised fee mode so USD net CAGR composes the native investor factor and
  FX factor exactly.
- Assert an absent/unreadable database fails only the guarded crypto metadata
  phase and does not prevent established public post-processing steps. Assert
  an absent individual ETH or BTC series instead produces errored USD periods
  for only that family while preserving all native and other-family metrics.
- Assert bundle-level USD provenance digests and coverage match the exact
  cleaned pre-fill rows published in the Parquet, and the opt-in nested column
  is never written into the crypto price Parquet.
- Run the existing cross-vault ranking pass and assert it reads and mutates only
  `period_results`; every `periodic_metrics_usd` ranking field remains null.

### Examiner

- Assert the report contains `Lifetime CAGR (base)` and
  `Lifetime CAGR (USD)` with correct net/gross fallback and formatting, plus
  the effective `USD from` date.
- Assert a missing or errored USD lifetime period displays `N/A` and does not
  change TVL sorting or stablecoin exclusion.

Suggested focused commands:

```shell
poetry run ruff format
poetry run ruff check \
  eth_defi/currency_api \
  eth_defi/research/vault_metrics.py \
  eth_defi/vault \
  scripts/erc-4626/export-crypto-vaults.py \
  scripts/erc-4626/examine-crypto-vault-performance.py \
  tests/currency_api \
  tests/vault/test_crypto_vaults.py \
  tests/erc_4626/test_export_data_files.py \
  tests/erc_4626/test_post_processing.py

source .local-test.env && poetry run pytest \
  tests/currency_api/test_currency_api.py \
  tests/research/test_vault_metrics.py \
  tests/vault/test_crypto_vaults.py \
  tests/erc_4626/test_export_data_files.py \
  tests/erc_4626/test_post_processing.py \
  --timeout=180
```

Do not build Sphinx locally.

## Production-data acceptance

1. Run `export-crypto-vaults.py` with `CRYPTO_VAULTS_PUBLISH=false` against a
   copy or the established local production state.
2. Confirm both raw ETH and BTC rate series still cover source labels from
   2024-03-02 through the current publication tail, and that their effective
   vault dates are exactly one day earlier. Record all raw and cleaned gaps,
   and verify gaps within the allowance are filled while longer gaps produce
   the documented per-period degradation rather than aborting the bundle.
3. Confirm every selected ETH/BTC vault receives either a successful USD
   lifetime period or the established insufficient-samples/coverage error;
   never weaken the shared minimum-duration rules. Separately count vaults
   whose native history starts before the rate floor and verify their USD
   sample start is not earlier than effective date 2024-03-01.
4. Compare representative WETH/stETH/restaking and WBTC/BTC-wrapper records:
   native metrics stay unchanged, USD price/TVL arithmetic follows the
   canonical underlying, and no wrapper-specific valuation is claimed.
5. Run the examiner and manually inspect the top TVL rows with both CAGR
   columns for plausible direction and magnitude.
6. Run `export-data-files.py` with `UPLOAD_PREFIX=test-`, confirm
   `test-exchange-rates.parquet` is readable from R2, matches the local digest
   and has an alternative daily backup. Do not require a new R2 integration
   mechanism; this uses the existing tested uploader and bucket configuration.

## Documentation and release notes

Update:

- `eth_defi/currency_api/README-currency-api.md` with the cleaned Parquet
  contract, rate direction, filename, R2 key and backup behaviour;
- `scripts/erc-4626/README-vault-scripts.md` with USD period semantics, the
  raw 2024-03-02/effective 2024-03-01 floor, bounded rate filling and both
  examiner CAGR columns;
- `docs/source/api/currency_api/index.rst` if a new public module is added;
- relevant script/module docstrings and environment-variable lists; and
- `CHANGELOG.md` with a dated feature entry when the implementation PR is
  opened.

Use British English, sentence-case headings and `onchain` spelling. Do not edit
generated Sphinx files.

## Rollout, rollback and completion

No migration script is required. The next successful all-chain cycle scans
rates first, materialises one cleaned exchange-rate snapshot, regenerates crypto
metadata with the additive USD field from that snapshot, uploads the same
Parquet during data export and publishes the crypto bundle only after the data
export succeeds.

Daily rollback is sufficient: restore the prior crypto JSON/manifest objects
and the matching `exchange-rates.parquet` from the alternative daily backup.
The raw DuckDB, vault price Parquets, reader state and sticky state do not need
to change.

The work is complete when:

- the clean exchange-rate Parquet is present in both data-file R2 exports and
  the alternative daily backup;
- ETH/BTC JSON records contain valid, coverage-bounded
  `periodic_metrics_usd` without changing native metrics or Parquet schemas;
- the examiner displays both base and USD lifetime CAGR;
- focused tests, Ruff and production-shaped acceptance checks pass; and
- documentation states the USD-rate direction, canonical-wrapper
  approximation and raw 2024-03-02/effective 2024-03-01 lifetime boundary
  accurately.
