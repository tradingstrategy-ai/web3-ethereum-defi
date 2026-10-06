# Hypercore permission recovery and DuckDB backups

[Issue #1628](https://github.com/tradingstrategy-ai/web3-ethereum-defi/issues/1628)
is caused by mixing current vault permissions with historical portfolio prices.
A price-row `written_at` is not a permission receipt. Rewriting a price must not
make retained permission or capacity look newly observed.

The collector now appends independent coherent responses before portfolio checks,
flow reads and historical cutoffs. Missing flags in a successful response are
explicit unknowns. Fetch failures are recorded separately. Changed bulk-list
closures are recorded before TVL/address/count filtering; a bulk open flag never
certifies deposits. Unchanged bulk denials retain their original receipt clock;
a later genuine response that clears closure allows a new bulk denial record. Price overlaps preserve their original write clock.

## Recovery script

Use `scripts/hyperliquid/recover-permissions.py`. It defaults to `DRY_RUN=true`,
opens the target read-only and writes a JSON report. A shared Poetry environment
in a worktree needs `PYTHONPATH=.` to select this checkout's source.

```shell
export TARGET_DATABASE=/path/to/local-copy/hyperliquid-vaults-hf.duckdb
export BACKUP_SOURCES='["/path/to/immutable/older/hyperliquid-vaults-hf.duckdb"]'
export PARQUET_SOURCES='["/path/to/immutable/r2/vault-prices-1h.parquet"]'
export MIGRATION_BACKUP_DIR=/path/to/separate/migration-backups
export RECOVERY_REPORT=/path/to/recovery-report.json
DRY_RUN=true PYTHONPATH=. poetry run python scripts/hyperliquid/recover-permissions.py
```

`BACKUP_SOURCES` order defines preference for keys absent from the target. Raw
Parquets have lower priority than scanner databases. Existing matching target
values win; differing archive alternatives, including alternatives for restored
keys, remain in `hypercore_price_conflicts`. Cleaned/resampled Parquets cannot
restore exact scanner points and are refused. Old flags, capacity inputs, source
hashes, original write times and raw rows remain in the evidence tables.

`SOURCE_AVAILABLE_AT` optionally maps absolute archive filenames to independently
verified naive UTC availability bounds, for example
`{"/path/to/older.duckdb":"2026-09-30T00:00:00"}`. A verified dated backup can use
a conservative end-of-day bound. This is deny-only archive evidence, never an
API receipt or capacity freshness. This deny-only evidence remains a last
fallback when no usable recovered price-clock or genuine receipt exists.
The target's own verified backup also provides a deny-only bound for its closures.
A genuine later coherent response takes precedence over archive-only evidence.

Under the operator's recovery policy, historical backup flags use the **price-row
timestamp** when an independent permission observation clock is unavailable.
These snapshots have `legacy_price_timestamp` provenance and preserve their
source path/hash and full original row. They can establish historical Open or
Closed under this approximation; they are not authenticated API receipts or
independent measurements. Only explicitly selected backup sources supply flags:
known-corrupted target flags are excluded. Scanner backups take priority over
raw Parquet for matching permission keys, and HF takes priority over daily at
identical inferred clocks. Raw Parquet flags may already have been carried
forwards; this is visible in their source provenance. A raw row cannot create a
new inferred observation at a key already present in a scanner backup, even if
its scanner flags are null. Raw-only keys remain an explicitly approximate
source under the operator policy. Their two-day expiry limits inferred-clock age;
it cannot authenticate an independent measurement or the age of a carried flag.

Historical uncertainty starts at the earliest legacy price. A recovered inferred
snapshot at or after that boundary can supply the fallback state; an older flag
cannot bridge an evidence gap. Inferred snapshots expire after two days, checked
against their original price clock rather than a rounded bucket. Genuine snapshots, including explicit unknowns,
take precedence over inferred flags. No recoverable flags means Unknown. Price
values and their `written_at` remain unchanged. Capacity clocks stay independent:
price-clock recovery never creates fresh capacity. Native observations already
present in a source or target are preserved. Dry-run reports include
`legacy_price_timestamp_permissions` before any target mutation.

For apply, explicitly set `DRY_RUN=false`. The script opens the stopped target for writing,
checkpoints it, makes its **own separate backup**, fsyncs and verifies both SHA-256
hashes, and writes a durable receipt **before** its repair transaction. Failure to
back up or validate aborts the repair. Primary-key/unique ART indexes on the bulk
price table are removed transactionally while preserving rows and nullability.
Repeated ingestion uses staging and hash joins instead. A row-count invariant
must pass before commit. Stop all database owners; never replace production state
with the earlier local rehearsal copy. Apply again to freshly stopped live data.

Use `scripts/hyperliquid/fetch-recovery-backups.py` for bounded downloads from the
private bucket: `BACKUP_DATES` is a JSON list of at most 31 ISO dates and
`RECOVERY_SOURCE_DIR` is an immutable evidence directory. It uses the existing
`daily/<date>/<UPLOAD_PREFIX>vault-prices-1h.parquet` layout, checks size and any supplied remote SHA-256,
always records a local SHA-256,
refuses to overwrite differing files and records remote identity/availability.

## Private R2 backup cadence

Every registered DuckDB uses the same gate, including settlement, risk and
currency databases previously uploaded every cycle. The registry includes
Hyperliquid daily/HF, GRVT, Lighter, Hibachi, ApeX, Derive v3, historical context,
RPC accounting and existing resolved settlement/Core3/Xerberus/currency paths.
An override preserves the database's logical identity. Unknown top-level files
are logged. Explicit extra owned databases can be supplied through
`DUCKDB_EXTRA_BACKUPS='[{"name":"block-timestamp-1","path":"/path/to/1-timestamps.duckdb"}]'`.
Do not register an independently active owner's file without arranging its stop.

The earliest next attempt is **48 hours after the latest remote attempt
anchor or retained legacy transfer**, including failed attempts. A skipped cycle does no database hash,
checkpoint, local copy, upload or server copy. Conditional remote claims exclude
competing exporters. Immutable attempt receipts and server timestamps survive
local-state or mutable-receipt loss. Clock rollback defers work. First scheduler
activation also honours the latest legacy flat/daily R2 copy, using HEAD metadata
without reading the database. At a due window, unchanged content updates the
check receipt without another transfer. Uploaded
snapshot progress survives a failed compatibility copy; that copy retries only
at another eligible window. All loops and byte copies report progress; checkpoint
operations have 30-second heartbeats.

The private layout is `duckdb-backups/<logical-name>/<attempt-id>.duckdb`, with
state and immutable receipts below the same logical prefix. Compatibility flat
copies happen inside the same gate, never the daily copy loop. Non-DuckDB files
keep their existing cadence. Normal price uploads run before backups so a locked
database cannot suppress uploading current prices; backup failures are reported
independently at the end and remaining databases are still attempted.

A non-empty `UPLOAD_PREFIX` requires an explicit stable
`DUCKDB_BACKUP_NAMESPACE`. Its scheduling/snapshot keys live below
`duckdb-backups/<namespace>/`. Staging and production must use different logical
namespaces. Changing the export prefix with the same namespace does not reset the
48-hour gate; compatibility flat copies still honour the current export prefix.

## Production maintenance order

1. Make the recovery tools available without starting the updated collector.
   Both collectors and Hypercore post-processing refuse an old constrained price
   table **before schema initialisation**; deploy maintenance tools first and
   migrate before restarting either path.
2. Reserve the daily/HF pair with `scripts/hyperliquid/reserve-backup-window.py`:
   set `RESERVATION_ID`, optionally `DATABASE_NAMES`, and private R2 credentials.
   The receipt gives `ready_after`, the later of both databases' due times.
   Routine exporters defer both members. `RESERVATION_ACTION=heartbeat` renews
   the lease for at most seven days total; `cancel` releases it without resetting
   gates. An abandoned lease expires after 72 hours without a heartbeat.
3. At that window, stop the owners. Inspect production Compose and override the
   oneshot entrypoint for maintenance; its normal entrypoint starts a full scan.
   Preserve the mounted `/root/.tradingstrategy`, HOME and timestamp caches.
4. Run `scripts/hyperliquid/backup-for-migration.py` with `MAINTENANCE_ID` and
   `R2_BACKUP_REPORT`. It backs up only the reserved pair and refuses readiness
   if either is deferred/missing/failed. It does not bypass the two-day gate.
5. Run recovery dry-run against freshly stopped production files. Then apply
   with each report's `snapshot_key` supplied as `PRE_MIGRATION_R2_KEY`.
   Production paths below `/root/.tradingstrategy` require this. Other deployments
   can set `REQUIRE_OFFHOST_BACKUP=true`. The migration verifies the private R2
   snapshot's SHA/size against **its own** pre-mutation backup, so a stale local
   preparation or newly written live rows cannot be overwritten silently.
6. Verify prices, row counts, evidence, sidecar and idempotence, then release the
   reservation and start the fixed collector. Metadata-only maintenance must
   disable historical price scanning; never reset reader states or old Monad rows.

## Permission consumer rollout

`hypercore-vault-permissions.parquet` is the authoritative independent history.
It retains response clocks, raw nullable pairs, relationship and capacity inputs,
provenance, IDs, payload hashes and uncertainty intervals. Several responses may
share the same portfolio timestamp; no synthetic prices are inserted for them.
Use `select_permission_state()` to select a coherent snapshot, rounding source
availability upwards **before** bucket selection. Successful unknowns supersede
older known flags. Exact receipt ties with conflicting inputs become unknown.
When no eligible genuine snapshot exists, use explicitly labelled recovered
price-clock snapshots. Consumers must prefer `permission_observed_at` to
`written_at` and check age against that original clock; missing legacy observation
clocks fall back to the source price timestamp, never the migration write time.

The client clock/freshness work is tracked in
[Trading Strategy issue #252](https://github.com/tradingstrategy-ai/trading-strategy/issues/252).
The local client remains unchanged; the DOEZOE report compares its behaviour with
the corrected producer selector.

Price columns are a point-in-time convenience projection. In v1 the tail can lag
an API response received after the newest portfolio point; the next eligible
price row exposes it. Old v1 consumers cannot consume between-price changes or
apply every uncertainty interval correctly. Do not enable live sidecar reliance
until the authenticated endpoint, Trading Strategy client and executor use the
sidecar together, preserve unknowns, and check original permission/capacity age.

`HYPERCORE_PERMISSION_MANIFEST_V2=true` publishes a **separate**
`vault-scan-manifest-v2.json` binding content-addressed immutable prices and
permission objects, exact ETags/hashes and permission schema version 1. It remains
disabled by default until that coordinated rollout. V1 wire format stays version
1. Publication checks local hashes against uploaded metadata and conditional
server copies prevent selecting a mixed generation.

## Focused checks

```shell
source .local-test.env && PYTHONPATH=. timeout 180s poetry run pytest tests/hyperliquid/test_permission_recovery.py tests/hyperliquid/test_high_freq_metrics.py tests/hyperliquid/test_high_freq_export.py tests/hyperliquid/test_daily_metrics_resume.py tests/vault/test_duckdb_backup.py tests/erc_4626/test_export_data_files.py tests/vault/test_scan_manifest.py -q
```

For the real private R2 round trip, additionally load the operator's private R2
configuration and set `RUN_R2_BACKUP_INTEGRATION=true`, then run
`tests/vault/test_duckdb_backup.py::test_real_r2_backup_round_trip`. All objects,
including compatibility copies, are confined to a random integration prefix and
removed afterwards. Record the command/result/date as a PR comment if opening a
PR; do not include credentials in that record.
