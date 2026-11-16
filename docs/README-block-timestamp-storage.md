# Block timestamp storage

Timestamp caches store `UBIGINT` block numbers and `UINTEGER` Unix seconds in
one DuckDB file per chain. Equal timestamps and historical gaps are expected.
Ordered insertion improves integer compression and zonemap pruning; explicit
unsigned SQL bounds avoid a whole-column `HUGEINT` cast on DuckDB 1.5.0.
Historical gap fills can overlap older groups, so only explicit maintenance
restores global order. Opening a cache never performs whole-file compaction.

## Compatibility and earlier failures

The timestamp writer already removed legacy ART-backed primary keys in
[PR #1631](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1631),
using transactional updates and anti-joins to retain one row per block. This
compaction keeps that ingestion contract. It does not remove DuckDB's automatic
zonemaps; sorting improves their range pruning. Unexpected explicit indexes,
views and other schema objects stop maintenance instead of being discarded.

[PR #1511](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1511)
records a production backfill that still crashed after response-memory changes:
large file-backed ART indexes were the remaining failure surface. The regression
checks here therefore include file-backed migration, repeated ingestion and the
90-million-row case, rather than relying solely on small in-memory tests.

[PR #501](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/501)
introduced the timestamp slicer to avoid materialising an entire large chain in
memory. Range queries and the slicer remain available, with inclusive bounds,
sorted block indices and naive UTC timestamps. Exact missing-block lookup also
preserves the sparse-cache behaviour repaired in
[PR #1630](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1630):
the latest cached block does not prove that earlier blocks are present.

## Reproduce the savings

From the repository, using its installed Poetry environment:

```shell
PYTHONPATH="$PWD" timeout 900s poetry run python scripts/diagnostics/benchmark-timestamp-storage.py
```

The default checks complete Ethereum and Base files without changing sources.
`TIMESTAMP_CACHE` overrides the source directory, `OUTPUT_DIR` the report parent
(default `logs/timestamp-storage`), and `TIMESTAMP_BENCHMARK_CHAINS` the diagnostic
chain selection. For the wider qualification set use `1,8453,42161,143,130,5042`.
Reports contain exact row comparisons, unsorted/sorted sizes, typed/untyped warm
query medians, five append/replay/correction/reopen cycles and peak process RSS.
Four engine threads and a 2 GiB engine limit do not bound Arrow/Pandas RSS.
Each SQL operation has a heartbeat; every spill/run directory is independent.
Use a quiet source; DuckDB refuses conflicting writers. WAL-bearing sources need
a quiesced paired snapshot recovered in scratch before running this diagnostic.
Never delete a WAL to force an inspection to succeed.

The independent 12 November 2026 smoke test reproduced these complete-file
results on DuckDB 1.5.0, with exact ordered Arrow equality:

| Cache | Source MiB | Sorted MiB | After five 100,000-row append/replay/reopen cycles MiB |
|---|---:|---:|---:|
| Ethereum, 25,683,334 original rows | 533.51 | 22.01 | 23.26 |
| Base, 48,785,921 original rows | 797.51 | 1.26 | 2.51 |

The wider run also passed complete-file validation and five ingestion cycles:

| Cache | Original rows | Source MiB | Sorted MiB | After ingestion MiB |
|---|---:|---:|---:|---:|
| Arbitrum | 485,044,169 | 7,652.01 | 99.26 | 100.51 |
| Monad | 89,703,161 | 1,500.26 | 18.01 | 19.51 |
| Legacy constrained Unichain | 34,178,673 | 420.51 | 1.26 | 2.51 |
| Sparse chain 5042 | 3,849 | 0.76 | 0.51 | 1.51 |

These six local sources contained 683,399,107 rows. Native compression can vary
by a storage block between runs. All full-file comparisons passed and both
writer-growth gates passed. Peak process RSS in the wider run reached 4.09 GiB,
despite the 2 GiB engine limit. The wider four-chain run took about 250 seconds;
this is shared-host diagnostic time, not a production downtime promise. Final
Ethereum/Base checks additionally measured real Pandas query/slicer, sparse
lookup and gap-discovery paths, inserted a historical missing block, and reopened
candidates in a fresh process. Raw reports remain in ignored
`logs/timestamp-storage/run-*` directories.

An isolated copy of the complete 48,785,921-row Base file also passed numbered
runner preview (unchanged source digest), apply, exact post-publication comparison
and a second apply that skipped the successful migration. Its independent backup
and journal remain in the ignored `migration-canary-*` run directory. No original
local cache or production state was replaced.

The earlier isolated DuckDB 2.0.0 comparison found no extra sorted-storage
saving. These observations do not establish fleet-wide or production savings.
Free blocks left by legacy constraint removal are reusable inside an old file;
rebuilding into a new file is needed to release that space. There is insufficient
historical provenance to attribute every existing free block to one operation.
The smoke test checks ongoing writer growth separately.

The initial writer growth gates allow at most `max(16 MiB, 25% of live bytes)`
free space and `max(16 MiB, twice the freshly sorted equivalent size)` total
size after replay. Timing regressions need investigation on an idle host rather
than brittle CI timing assertions. Run the optional 90-million-row synthetic
file-backed check with:

```shell
source .local-test.env && RUN_LARGE_TIMESTAMP_TEST=1 PYTHONPATH="$PWD" timeout 180s poetry run pytest tests/rpc/test_timestamp_storage.py::test_large_timestamp_ingestion
```

## Numbered maintenance

For manual testing of selected local chains, run the numbered script directly:

```shell
TIMESTAMP_CHAIN_IDS=43114 PYTHONPATH="$PWD" poetry run python migration/scripts/065-compact-block-timestamp-caches.py
TIMESTAMP_CHAIN_IDS=43114 DRY_RUN=false PYTHONPATH="$PWD" poetry run python migration/scripts/065-compact-block-timestamp-caches.py
```

`TIMESTAMP_CHAIN_IDS` accepts comma-separated numeric IDs; 43114 is Avalanche.
The first command previews; the second applies. `TIMESTAMP_CACHE` optionally
overrides the normal cache directory. The runner stores a separate journal under
`<cache>/.timestamp-maintenance/<sorted-ids>/migration.json`, skips successful
manual runs and retains independent backups, exact validation and the exclusive
cache-directory lock.
Stop local scanners and all other timestamp users before either command. The
manual runner's lifecycle guard protects its own journal directory, not the
normal scanner's pipeline directory; the exclusive cache-directory lock still
rejects open cooperating cache connections. Retry an interrupted manual run
with the identical selector so it reuses the recorded recovery evidence. Keep
successful manual journals immutable: editing script 065 afterwards causes the
runner to reject a checksum mismatch; use a new numbered migration for a later
change rather than deleting or resetting its history.
The normal all-chain migration journal is unaffected. Do not set this selector
when running the normal pending migration batch. Manual execution is for local
testing; production still uses the stopped-service Compose workflow below.

On 12 November 2026, direct manual preview/apply on the local Avalanche cache
preserved all 90,617,032 rows and reduced the active file from 1,559,506,944
to 47,722,496 bytes (1,487.26 to 45.51 MiB, 96.94%). The independent backup
remains under `.timestamp-recovery`; these are active-file savings, with no net
host space reclaimed until backup retirement. Application checks matched 2,003
sampled rows against pre-migration results, exercised slicer access, exact sparse
lookup, empty ranges and idempotent ingestion on the canonical file, then
recompared every row against the backup. Correction, append, replay and reopen
also passed on a disposable copy. All 31 other chain files retained their size,
modification time and inode. Raw results are in the ignored
`logs/timestamp-storage/avalanche-manual-check.json`. This was local maintenance;
the production deployment was not changed.

Migration `065-compact-block-timestamp-caches.py` preflights every canonical
cache, rejects unexpected schema/objects, nulls and duplicate block numbers,
then builds fresh sorted unconstrained copies. It preserves all block/timestamp
pairs and fails instead of inventing observations or silently deduplicating.
Candidates use storage version `v1.4.0`, supported by the currently locked
DuckDB 1.5.0. No dependency upgrade is needed: the layout and SQL changes provide
the measured gains without a major-version format transition.

Use the [numbered migration runbook](../.claude/docs/migration-scripts.md) for
image stamping, Compose inspection, mounts, guard ordering and the full pending
batch. Both preview and apply require the scanner daemon stopped. Also stop
post-scanner, one-shot/cron timestamp users and downstream projects sharing these
files. Cooperating `BlockTimestampDatabase` connections hold a shared lock on
the stable cache directory; migration requires its exclusive lock. Direct
DuckDB consumers outside that class still require operator quiescence. Do not
start any cache users after a failed or partial apply until recovery completes.

`TIMESTAMP_CACHE` selects the mounted timestamp directory. With an isolated
`PIPELINE_DATA_DIR`, an explicit `TIMESTAMP_CACHE` is mandatory: pipeline path
redirection alone does not isolate timestamp data. Preview uses disposable
snapshots and does not change source bytes or apply journal state.

**Run after merge**, from the production checkout after inspecting Compose and
building the stamped image:

```shell
source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
```

Inspect the preview's exact inventory and sizes before apply. Preview/apply each
perform complete reads, sorting and exact validation, and can take several
minutes per large chain. Use actual benchmark timings to schedule downtime;
allow space for a full backup, snapshots, candidate and spill. No prices or
vault reader states are changed; this migration performs no RPC reads/backfill.

## Recovery and actual disk savings

The existing migration journal durably records per-chain source/candidate
hashes, backup paths and publication phase before replacement. The runner
retains `storage_recovery` across unsuccessful attempts. Each apply backup is
an independent exclusive copy under `.timestamp-recovery/`, outside discovery.
WAL-bearing inputs retain paired recovery copies before the canonical file is
recovered/checkpointed. No WAL is manually deleted. Candidates are checkpointed,
closed, reopened and compared before atomic replacement and directory fsync.

There is no multi-chain transaction. Retry while all users remain stopped:
prepared candidate hashes identify a completed rename even if the completion
record was interrupted. Changed identities without a verified backup fail
closed. Preserve journal and backup files together. Do not reset the journal,
overwrite backups, or edit an already successful numbered script. Rollback is
an explicit maintenance operation under the same exclusion; verify the backup
and its WAL, restore the paired state and reconcile the journal according to
the migration runbook. A later correction requires a new numbered migration.

After all chains succeed, restart services with the same image and verify one
normal scanner cycle. Retain backups for at least seven days. Retirement is a
separate explicit operator decision: verify recorded backup hashes, enumerate
only that run's backup paths and record bytes freed. Never use wildcard cache
deletion or automatic backup removal. Until retirement, report smaller active
files, not net recovered host disk space.

## Dependencies

Keep the existing DuckDB constraint/lockfile for this change. The initial
isolated 2.0.0 comparison showed no extra sorted-storage savings. A future major
upgrade needs all consumers, optional imports, Python/Arrow APIs, CI/Docker
wheels, storage compatibility and rollback tested separately. Parquet Zstd
settings do not configure native DuckDB integer compression.

## Divergence and rollback procedure

A changed inventory, a modified published file, or a damaged backup stops retry
by design. Preserve the complete current cache root and journal, stop all users
again, and inspect the recorded per-chain phases and paired digests. Do not
mark an interrupted attempt successful or restore older rows over later scanner
writes. A reviewer must correct the unsuccessful numbered script with a
content-based reconciliation for that exact snapshot; the runner records its
previous checksum. Successful scripts remain immutable. This is the supported
route for unexpected divergence without hand-editing `migration.json`.

For a controlled rollback with no intervening writer, retain a separate copy of
the current canonical files and the matching journal first. Verify each recorded
backup and WAL digest, restore the paired database/WAL under full exclusion,
and restore the matching pre-migration journal snapshot only when every restored
database matches that snapshot. Otherwise use the reviewed unsuccessful-script
recovery above. Keep both generations until validation and restart succeed.
Rollback uses the original file format; new compacted files require a reader
supporting storage version v1.4.0 or later.

Preflight reports retained `.timestamp-compact-*` scratch from native process
termination; inspect and remove only the identified abandoned run directories
while users are stopped. Normal exceptions clean up each chain immediately.
The whole-run free-space check reserves all backup bytes plus four times the
largest source and a 64 MiB margin before touching any canonical database.
