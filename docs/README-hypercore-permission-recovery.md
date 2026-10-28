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

Use `scripts/hyperliquid/recover-permissions.py`. **`DRY_RUN` is the only
migration-specific input.** The script handles both daily and HF databases in
the normal pipeline directory, `~/.tradingstrategy/vaults`. It automatically
selects available scanner snapshots dated 2026-09-29 through 2026-10-05,
newest first, and `backups/2026-10-05/vault-prices-1h.parquet`. Later archives
and today's manual backup are outside the reviewed source scope.

```shell
DRY_RUN=true poetry run python scripts/hyperliquid/recover-permissions.py
DRY_RUN=false poetry run python scripts/hyperliquid/recover-permissions.py
```

Dry run is the default. It opens both targets read-only and logs source selection,
row counts, conflicts, constraint removal and inferred permissions. It creates
no persistent reports, backups or lock files. Apply takes the shared
`scan-pipeline` writer lock. No RPC or R2 access, backup reservation, external
snapshot key, or operator-supplied archive list is required. Normal R2 backups
retain their independent two-day cadence. `PIPELINE_DATA_DIR` remains the
standard infrastructure override for testing with isolated copies; it is not
needed for the production commands. A shared Poetry environment in a worktree
needs `PYTHONPATH=.` to select that checkout's source.

Missing source files are logged and available evidence is used. Both live
databases must exist; an absent target aborts before either is changed. Archive
sources must be checkpointed, with no outstanding WALs. Raw Parquet has lower
priority than scanner databases. Existing matching target
values win; differing archive alternatives, including alternatives for restored
keys, remain in `hypercore_price_conflicts`. Cleaned/resampled Parquets cannot
restore exact scanner points and are refused. Old flags, capacity inputs, source
hashes, original write times and raw rows remain in the evidence tables.

The target's own verified backup also provides a deny-only bound for its closures.
A genuine later coherent response takes precedence over archive-only evidence.

Under the operator's recovery policy, historical backup flags use the **price-row
timestamp** when an independent permission observation clock is unavailable.
These snapshots have `legacy_price_timestamp` provenance and preserve their
source path/hash and full original row. They can establish historical Open or
Closed under this approximation; they are not authenticated API receipts or
independent measurements. Only the fixed pre-repair backup sources supply flags:
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
values and their `written_at` remain unchanged. Recorded leader shares and
policy caps are retained even when the legacy schema lacks a capacity clock.
The original response clock, when present, stays unchanged; publication and
price-only updates do not create a new measurement. Native observations already
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

Automatic backups, checksum receipts and `recovery-report.json` are saved under
`migration-backups/hypercore-permissions-1628/` in the pipeline directory.
The report is updated after each database succeeds. The two databases have
separate transactions: if the second fails, the first database's completed
report remains available, and rerunning safely finishes the repair. This script
repairs cached permission metadata in the same databases; it does not require a
separate metadata migration or historical API backfill.

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
RPC accounting, vault post feeds and existing resolved settlement/Core3/Xerberus/currency paths.
The feed path uses `FEED_DB_PATH`, then `DB_PATH`, then
`~/.tradingstrategy/vaults/vault-post-database.duckdb`.
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

From the host checkout `/root/vault-scanner/web3-ethereum-defi`, use the merged
code and rebuild the image with `scripts/build-container.sh`. Stop the database
owners, optionally make an additional full-directory backup, and open a
maintenance shell with the production state mounted:

```shell
source /root/vault-scanner/vault-rpc.env
docker compose --profile oneshot stop vault-scanner-looped post-scanner vault-scanner-oneshot
cp -r /root/.tradingstrategy "/root/.tradingstrategy-backup-$(date -u +%Y%m%d-%H%M%S)"
docker compose run --rm --entrypoint /bin/bash vault-scanner-oneshot
```

Inside that shell:

```shell
DRY_RUN=true poetry run python scripts/hyperliquid/recover-permissions.py
DRY_RUN=false poetry run python scripts/hyperliquid/recover-permissions.py
DRY_RUN=true poetry run python scripts/hyperliquid/recover-permissions.py
```

Review the first dry run before applying. The final dry run should report zero
missing price keys and zero ART constraints to remove for both databases;
retained archive conflicts may still appear. Each apply report must satisfy
`rows_after == rows_before + restore_missing`. Then exit the shell and restart
the previously running services with `docker compose up -d vault-scanner-looped
post-scanner`. The normal export republishes prices and permission history.
Both collectors and Hypercore post-processing refuse the old constrained price
tables until migration. Preserve reader states, existing Parquet and timestamp
caches, including old Monad rows.

Production apply completed on 2026-10-06 at 19:36 UTC with commit `e207778d6`.
The daily database retained all 41,620 rows. The HF database restored 212,124
missing rows, increasing from 1,497,653 to 1,709,777. Both verified automatic
backups were recorded in `migration-backups/hypercore-permissions-1628/`.
A subsequent read-only dry run reported zero missing keys and zero ART
constraints for both databases; archived conflicts remained as expected.

The post-processing coordinator exports independent permission history from
both open scanner owners, including responses between price samples. Before
cleaning, it also inserts missing shared catalogue entries from both catalogues,
preserving existing metadata and manual reviews. Prices and their readiness
receipt are uploaded before sparkline rendering and protocol metadata export,
so a lengthy image-rendering phase cannot delay trading inputs. The consumer
rollout below is still required to use the independent history.
Catalogue restoration or permission-history export failure marks the Hypercore
merge as failed and retains the previous Hypercore price partition. Custom raw
input paths do not redirect the shared catalogue or private permission output.

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
sidecar together and preserve unknowns. Read recorded leader shares and caps
without demanding a separate capacity clock; use the original source price
clock as the legacy fallback when no other clock exists. Archived shares can
already be carried projections: where their measurement time is unavailable,
their age remains unknown. The repair retains those values without inventing
a measurement receipt or imposing a new capacity-clock eligibility rule.

`HYPERCORE_PERMISSION_MANIFEST_V2=true` publishes a **separate**
`vault-scan-manifest-v2.json` binding content-addressed immutable prices and
permission objects, exact ETags/hashes and permission schema version 1. It remains
disabled by default until that coordinated rollout. V1 wire format stays version
1. Publication checks local hashes against uploaded metadata and conditional
server copies prevent selecting a mixed generation.

## Restore leader shares and policy caps

[Issue #1633](https://github.com/tradingstrategy-ai/web3-ethereum-defi/issues/1633)
repairs historical policy inputs removed by the original permission migration.
The collector records the leader fraction and derived policy cap from each
response. The exporter retains these inputs through price-only updates. An
open normal vault below the 5.5% leader-share threshold has `max_deposit=0`;
this trading-policy block is separate from venue closure. An explicit archived
zero also survives when its share input is unavailable. Missing values remain
NULL, and a genuinely newer unknown response clears older projected inputs.

Inside the mounted maintenance container, with both database owners stopped:

```shell
DRY_RUN=true poetry run python scripts/hyperliquid/recover-leader-shares.py
DRY_RUN=false poetry run python scripts/hyperliquid/recover-leader-shares.py
DRY_RUN=true poetry run python scripts/hyperliquid/recover-leader-shares.py
```

The only migration-specific input is `DRY_RUN`, which defaults to true. The
standard pipeline directory is used automatically. Sources are the retained
pre-repair scanner backups (newest first), retained scanner evidence, the
original raw Parquet, then retained raw evidence. Conflicts are counted;
existing non-null target values win. Caps accompanied by a conflicting share
are not applied. Cleaned/resampled archives are refused. The migration fills
policy inputs without rewriting prices, permissions, receipt clocks or
original `written_at`. It also restores policy inputs in inferred permission
snapshots from their retained payloads; no separate capacity clock is invented.

Apply makes and verifies its own database backup before its transaction,
removes old ART constraints, and checks that every non-policy price field is
unchanged before committing. Backups, SHA-256 receipts and the recovery report
are saved under `migration-backups/hypercore-leader-shares-1633/`. A subsequent
dry run should report zero missing inputs to restore. No R2 or RPC credentials
are required; this script does not upload or fetch historical data. Restart
the normal scanner to republish the raw/cleaned prices, permission sidecar and
matching readiness receipt.

The reader changes are tracked in
[Trading Strategy #254](https://github.com/tradingstrategy-ai/trading-strategy/issues/254).
Clients must retain the repaired inputs, honour zero caps, preserve NULLs and
use the original price clock where no other clock is available.

### Local recovery on 7 October 2026

Dry run, apply and a second dry run completed against isolated copies of the
actual damaged production databases captured on 7 October. Six of the seven
pre-repair scanner archives were available; the 29 September files were missing.
The retained evidence and 5 October raw archive supplied the remaining inputs.
Both databases took their own verified backups. All price keys, economics,
permission flags and original write times were preserved. The final dry run
reported zero remaining inputs to restore.

| Database | Price rows preserved | Shares restored | Explicit caps restored | Permission records repaired |
| --- | ---: | ---: | ---: | ---: |
| Daily | 41,620 | 12,996 | 523 | 12,528 |
| HF | 1,717,947 | 1,647,465 | 16,516 | 441,416 |

The normal raw exporter and cleaner were run on these recovered copies. The
comparison used the available 5 October pre-repair raw archive, SHA-256
`b2cf602c48cbbe2d8c1c5dac1ed902cc1facfbd228db6394ce73849fe46f981c`.

| Raw HyperCore projection | Before repair archive | Recovered export |
| --- | ---: | ---: |
| Price rows | 1,743,217 | 1,759,567 |
| Non-NULL leader shares | 1,658,971 | 1,675,209 |
| Explicit deposit caps | 17,039 | 55,335 |

Every original price key survived. Every original non-NULL leader share and
all 17,039 zero caps matched exactly, in both raw and cleaned exports. The
37,775 additional caps on matching historical rows all came from known shares
below 5.5% in open normal vaults. This corrects the previous exporter dropping
the low-share policy on price-only updates. Counts describe exported rows, not
new API measurements. The Gucky example retains share `0.05000280943338046`,
`max_deposit=0`, Open permission and write time
`2026-04-11 04:22:06.999 UTC`, without requiring a separate capacity receipt.

The earlier permission repair had already left 397 matched rows Unknown where
the older archive published a carried Open or Closed flag. This migration did
not change those flags. The permission sidecar exported successfully and other
chains' raw price rows remained unchanged. Production databases were untouched;
deployment and a production migration are still required.

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
