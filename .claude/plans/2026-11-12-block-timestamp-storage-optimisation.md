# Block timestamp storage optimisation

Status: implemented and locally qualified on disposable copies; production preview/apply remains an operator operation.
Date: 2026-11-12. Scope: the per-chain caches under `~/.tradingstrategy/block-timestamp/`.

## Objective and sequence

Confirm the reported space savings independently, then preserve them through writer and reader changes and a recoverable numbered migration. Keep all block/timestamp pairs, integer widths, gaps, repeated timestamps and caller-visible behaviour. Complete stages 1–4 on the current supported dependency first; stage 5 is conditional and must not block gains available without an upgrade. This plan is not an instruction to deploy or apply production changes.

## Evidence to reproduce

The initial local audit found 32 files, 1,652,922,325 rows and 23.88 GiB of logical file size. DuckDB reported 15.01 GiB of internally free blocks; eight older databases still had primary-key constraints. These are local observations, not a production inventory.

| Complete cache | Original MiB | Fresh unsorted MiB, 1.5.0 | Fresh sorted MiB | Reduction from original |
|---|---:|---:|---:|---:|
| Ethereum | 533.51 | 164.76 | 22.01 | 95.87% |
| Base | 797.51 | 199.76 | 1.26 | 99.84% |
| Arbitrum | 7652.01 | Not measured | 99.51 | 98.70% |

Sorted copies used `ORDER BY block_number`, retained `UBIGINT`/`UINTEGER`, and passed full ordered Arrow comparisons of both columns, including types and multiplicities, under 1.5.0 and 2.0.0. Sorted sizes were identical between versions. These striking results are hypotheses for the implementation smoke test, not acceptance evidence for production replacement.

Initial artefacts are local and temporary: `/tmp/timestamp-layout-audit/` contains `report.md`, `inventory.json`, `benchmark.py`, `queries.py`, versioned result JSON and a separate successful Arbitrum 2.0 validation. Do not depend on these files surviving. One concurrent benchmark attempt collided in a shared spill directory and was rerun; the new harness must use a unique directory per process/run and run version comparisons sequentially.

The reference [Parquet guide at the inspected revision](https://github.com/tradingstrategy-ai/freqtrade-strategies/blob/4c40d9a584c50431caf8cfc1733f9eb10b32fbdb/.claude/docs/parquet.md) prioritises lossless validation, measured encodings and bounded memory. Apply that methodology here. Its Zstd 9 policy is specific to Parquet; native DuckDB files need their own layout experiments.

## 1. Smoke test: confirm real space savings first

Deliver a reproducible read-only diagnostic/benchmark and a durable report under an ignored run directory, with the committed result summary and exact reproduction instructions kept together in `docs/README-block-timestamp-storage.md`. This diagnostic may be an ordinary script because it never replaces source files. Use environment configuration and sane defaults, not a new command-line parser.

- Inventory file paths, chain IDs, sizes, allocated filesystem bytes, modification times, DuckDB/Python versions, schema, constraints, indexes, row counts, block/timestamp bounds, WAL presence, storage version, row groups, compression and internal free blocks. Inspect extra tables/views before assuming each file contains only the timestamp table.
- Begin with complete Ethereum and Base files; only after passing, repeat on Arbitrum, Monad, one legacy constrained file and a genuinely sparse/small cache. Test a copied production snapshot before production eligibility. Record missing targets explicitly; do not claim an all-chain result from a subset.
- Open sources with direct read-only DuckDB connections, never `BlockTimestampDatabase`, whose initialiser can migrate constraints. Hold a consistent source view and exclude writers while making a snapshot. Refuse lock conflicts; do not copy only the main file of an active database. For a quiescent WAL-bearing source, copy the database and WAL as a pair to an isolated directory, hash both, and replay/checkpoint the copy on the pinned engine before reading. Stage 3 defines controlled recovery of the original for apply; preview never modifies it. Never delete a WAL to make a source readable.
- Attribute free-space bloat before choosing a remedy: compare legacy-migrated and never-constrained files, confirm correction SQL updates only changed timestamps, and replay scanner-sized increments with its actual open/close/checkpoint cadence. Distinguish a one-off legacy migration footprint from growth caused by the ongoing writer.
- Create three separately identified baselines: existing file, fresh unconstrained unsorted copy, and fresh unconstrained sorted copy. Preserve original types and values. This separates free-space/index reclamation from sorting/compression benefits. Use default row-group and block sizes initially; defer parameter tuning until this baseline passes.
- Close/checkpoint candidate writers and reopen candidates read-only in a fresh process before measuring. Report logical bytes, allocated bytes, WAL and retained backup/scratch bytes separately. Theoretical/internal free blocks are not filesystem savings; actual host free space will not increase while source backups remain retained.
- Validate complete source and candidate streams sorted by `(block_number, timestamp)` with bounded Arrow batches, explicit null ordering and exact schema/value/null/multiplicity equality. Compare batch-independent row sequences and detect unequal stream lengths. Counts and hash sums alone are insufficient. Record duplicate block numbers, conflicting timestamps and nulls; abort replacement eligibility on these anomalies without silently deduplicating or coercing them.
- Measure both existing SQL and explicit unsigned-bound SQL against each layout, retaining the real `query()`/slicer path, including Pandas and naive UTC conversion, plus sparse missing-block joins, min/max/count, gap discovery, and ingestion. Use inclusive 1,000- and 1,000,000-block spans at fixed recorded locations. Record warm-cache medians over at least three repeats; use isolated cold-read measurements only when feasible without disrupting shared host caches. Do not label a fresh process as a cold OS cache.
- On disposable candidates, replay realistic 100,000-block appends, repeated chunks, corrected timestamps and out-of-order historical fills. Close/reopen and remeasure size, integrity, pruning and read time after repeated writes. Initial CTAS compression alone does not qualify the ongoing writer.
- Bound resources: start with one cache at a time, four DuckDB threads and a 2 GiB engine memory limit; measure actual process peak RSS, spill and total runtime because the engine limit does not bound Arrow/Pandas memory. Use per-run spill directories, free-space preflight, a wall-clock deadline, and progress/heartbeat output at least every minute.

Acceptance gate: complete equality and reopen checks pass for every candidate; each replacement candidate is smaller after accounting for its WAL; the aggregate saving is measured in actual bytes; repeated ingestion preserves correctness and satisfies two initial growth budgets after close/checkpoint: free bytes no greater than `max(16 MiB, 25% of live allocated bytes)`, and candidate bytes no greater than `max(16 MiB, 2 × a freshly sorted copy of the same post-ingestion contents)`. These are investigation gates, not production auto-compaction triggers; any failed budget needs an explained, measured revision in the report before proceeding. Treat >20% median regression in representative reads/writes as a trigger to investigate and rerun on an idle host, not a brittle pytest timing assertion. If the claimed savings fail reproduction, stop and explain before implementing the migration. Do not extrapolate a fleet saving from Base's unusually compressible data.

## 2. Schema, writer and reader updates

Primary implementation: `eth_defi/event_reader/timestamp_cache.py`; inspect callers in `eth_defi/hypersync/hypersync_timestamp.py`, `eth_defi/event_reader/multicall_timestamp.py` and direct consumers such as `eth_defi/erc_4626/vault_protocol/infinit/events.py`.

### Storage contract

Keep one file per chain and the existing logical schema:

```sql
CREATE TABLE block_timestamps (
    block_number UBIGINT,
    timestamp UINTEGER
);
```

The change is physical ordering/compaction and removal of legacy ART constraints, not a numeric downcast. Do not add `PRIMARY KEY` or `UNIQUE`, narrow block numbers, increase timestamp precision, impose timestamp uniqueness, or turn gaps into fabricated observations. Retain the existing schema compatibility path; ordinary cache opening must not unexpectedly trigger a whole-file compaction. Explicitly assess whether its existing legacy-table copy should sort output, without making it the operational migration mechanism.

### Writer

- Preserve deduplication within incoming chunks (`keep="last"`), correction updates, bounded hash joins and transactional update/insert semantics.
- Sort the final missing-row selection by `incoming.block_number` after the anti-join. Sorting the input DataFrame alone does not establish join output order.
- Retain `wal_autocheckpoint = '1TB'` for bulk writes. Define and measure checkpoint/close boundaries so durable ingestion does not leave uncontrolled WAL growth; do not checkpoint every small chunk without evidence.
- Global sorting is a migration property. Historical gap fills append older blocks and can degrade that property; document this and measure it. Do not introduce automatic full-cache rewrites during normal reads or imports. Later compaction requires a new explicit maintenance operation.
- During stage 1, audit exclusion for file replacement across every cache writer and long-lived accessor, including non-scanner utilities. Audit existing locks first. If daemon shutdown cannot cover every opener, add a stable per-cache coordination lock honoured by all relevant open paths; never lock only the DuckDB inode being replaced. Define acquisition order and test contention before shipping. Choose and document the least invasive proven mechanism before stage 3, including downstream projects sharing the directory. A source-inode DuckDB lock alone cannot exclude stale readers or new-inode openers across rename. Treat any new coordination lock as migration infrastructure with its own compatibility review. The numbered migration must not reacquire the runner's lifetime/writer locks.

### Reader and SQL typing

The 1.5.0 audit observed `CAST(block_number AS HUGEINT)` for signed Python bounds. Compare `EXPLAIN ANALYZE` before/after explicit unsigned parameters:

```sql
WHERE block_number BETWEEN ?::UBIGINT AND ?::UBIGINT
```

Audit all range bounds, update/anti-join filters and registered DataFrame join-key types. Preserve inclusive endpoints, ordered results, empty-cache behaviour and naive `datetime64[s]` output. Specify handling of negative/out-of-range input before unsigned casting so callers do not acquire accidental overflow failures; retain compatible behaviour or document deliberate validation changes. Test block numbers above signed 64-bit range without allocating a dense range. Do not promise exact plan text across DuckDB versions.

### Focused checks

Extend `tests/rpc/test_timestamp_cache_migration.py` and relevant `tests/hypersync/test_hypersync_block_timestamp.py` coverage. Include unchanged legacy reads, corrected duplicates, repeated imports, unordered chunks, equal timestamps, gaps, null/duplicate migration rejection, empty files, maximum type bounds, inclusive ranges, slicer results, sparse joins and reopen behaviour. Always close DuckDB handles with fixtures/finally.

Use real production-scale copies in the manual harness; the existing million-row test alone cannot qualify allocator behaviour. Add an explicitly opted-in large test using synthetic file-backed `range()` data at representative production row counts, with a documented environment guard and dedicated resource budget; routine CI runs bounded correctness cases, not a 485-million-row fixture. Keep production data out of Git and CI. Keep live Hypersync integration checks focused and credential guarded if its integration is materially changed; record actual command, date and provider result. Do not fetch events through JSON-RPC or initiate historical state backfills.

## 3. Numbered migration and recovery

Follow [the migration guide](../docs/migration-scripts.md). Allocate the next unused `migration/scripts/NNN-compact-block-timestamp-caches.py` at implementation time. Add a focused reusable compaction helper near timestamp storage or the existing repair helpers; avoid a generic storage framework. Do not edit a successful numbered migration or add a standalone `migrate-*.py` repair.

- Declare database-wide scope: all existing canonical per-chain timestamp files in the configured cache root, with a frozen, reviewed inventory per run. `DRY_RUN` remains the only migration-specific production switch. Infrastructure path configuration must be explicit: timestamp caches are outside `MigrationContext.data_dir`; changing `PIPELINE_DATA_DIR` alone does not isolate them. Use the established cache-path configuration where available and prove snapshot previews resolve only snapshot paths.
- Apply is self-contained: freeze its own start-of-run inventory and compare source identities again before publication; it does not trust previous preview output. Run schema/anomaly/WAL preflight for every chain before publishing any. Nulls, duplicate blocks or unknown objects abort the whole migration, with an actionable report and a separately reviewed repair if needed; do not silently skip and mark success.
- Preview builds and validates candidates in scratch space, reports per-chain rows/bytes/constraints and leaves all source files and the apply journal unchanged. Apply repeats source identity checks and validation; never publish an old preview candidate against changed sources.
- Preflight permissions, unexpected symlinks/objects, available space for candidate, spill and backup, exact schema, supported storage version and writer exclusion. Preserve unrelated tables/objects exactly or fail explicitly; never publish a database containing only the expected table after dropping unknown contents.
- All migration connections, including post-publication validation, are direct DuckDB connections rather than `BlockTimestampDatabase`. Pin the engine and explicitly select a storage format qualified by stage 1; use the current locked 1.5.0 environment for the baseline implementation, with any patch upgrade separately qualified.
- WAL recovery: under full exclusion, preview replays only paired copies. Apply first creates and durably records independent database-plus-WAL recovery copies, then opens the original on the pinned engine to recover and checkpoint it. Journal this as an actual mutation, close it cleanly, verify logical equality to the validated recovery copy and establish a new source fingerprint. No canonical WAL may remain at compaction publication. Keep pre-recovery backups for interrupted recovery; never manually unlink the WAL. Test crashes before and after checkpoint.
- For each chain: record source identity/digest and planned paths through `context.report()`; build a fresh sorted unconstrained candidate on the same filesystem; checkpoint/close; reopen and validate every row; retain a durable uniquely named source backup in a dedicated recovery subdirectory excluded from cache discovery; use an independent file copy (not a hard link), so an unexpected stale-inode writer cannot mutate the backup; preserve ownership/mode; fsync files and directory as appropriate; atomically replace the canonical path; reopen and check the published result before marking that chain complete.
- Use `context.record_modified_file()` only after actual backup creation/publication. Persist recovery evidence before replacement through `context.report()` and `record_modified_file()`: inspection confirms apply invokes `_persist` on these calls, rather than only at run completion. Verify that the journal persistence is atomic and fsynced, including its parent directory, and add that guarantee if absent. Use this existing journal rather than a second manifest system. The per-chain record must include source/candidate hashes, rows, storage version, paths and per-chain phase. Keep all candidate/source handles closed at rename and ensure excluded readers cannot retain stale inodes. Do not overwrite an existing backup or delete it automatically.
- There is no cross-file transaction. Design retry for failure before replacement, after replacement but before journalling, and halfway through chains. Reconcile journal evidence with actual file hashes/schema/content; skip already validated published candidates, resume unprocessed files, and fail on unexplained divergence. Never infer success from filename or smaller size alone. Keep every cache user stopped after partial apply or failure until reconciliation completes; byte-hash recovery assumes no intervening read-write opener. If that exclusion was broken, fail closed and require a separately reviewed content-based recovery. A later correction uses a new migration.
- Backup retirement is a separate explicit operator action after all chains succeed, the restarted scanner completes one normal cycle, exact validation and source/backup digests are recorded, and a minimum seven-day recovery window has elapsed. Verify backup digests again; a mismatch blocks deletion. Enumerate only this run's recorded backup paths, obtain the operator's explicit retirement decision and record bytes actually freed. No wildcard cache deletion or automatic retention cleanup. While backups remain, report compacted active size rather than net disk savings.
- Rollback restores verified backups under the same exclusion, preserves the failed candidates for diagnosis and reconciles migration state through the documented recovery process. Never erase/reset the journal. An already successful migration remains immutable.
- Prioritise preview isolation, publication-boundary crashes and partial-apply retry as mandatory release gates. Also test failure injection at each publication boundary, native-process interruption, partial multi-chain apply/retry, backup retention, lock contention, insufficient disk space, unknown schema, permissions and corrupted input. Include existing migration-runner tests as applicable.

Production run reminder for the eventual implementation PR: **Run after merge**. Inspect `docker-compose.yml`, persistent mounts and entrypoints; build the stamped image; stop `vault-scanner-looped` and `post-scanner`, quiesce all other cache users, and keep the lifetime-guard-before-writer-lock ordering. Preview and apply use the direct Compose `vault-scanner-oneshot` commands in the guide with `--entrypoint vault-migrate`; inspect all pending migrations because the runner executes the batch. Production preview on quiesced mounted data is the final eligibility evidence; an earlier production snapshot needs its own scheduled writer stop. Use stage 1 per-chain timings plus backup, recovery and validation costs to publish preview/apply downtime estimates and a scratch-space budget before scheduling maintenance. Keep normal price scanning out of this timestamp-only operation. Record expected inventory, paths, validation and recovery evidence, then restart the persistent services with the same image and verify timestamp reads and ingestion. No vault price backfill or reader-state reset is required by this storage-only transformation.

## 4. Documentation updates

- Add the engineering runbook `docs/README-block-timestamp-storage.md` for the smoke test, storage contract, measured before/after sizes, typing/pruning mechanism, maintenance triggers, preview/apply, writer exclusion and recovery. Distinguish reclaimed internal space from actual filesystem savings while backups exist.
- Update `.claude/docs/migration-scripts.md` with the timestamp-cache path/isolation and recovery specifics, linking to the runbook rather than duplicating it. Add the relevant reference to repository agent guidance only where future timestamp work needs it.
- Update timestamp module/class/method docstrings to explain sorted insertion, unsigned bounds, corrections, gaps and explicit maintenance. Keep Sphinx API source entries/index references current if adding a public helper; do not edit generated autosummary files or build Sphinx locally.
- Record a concise dated changelog entry when the implementation ships. Engineering documentation uses British English and sentence-case headings; no public vault/protocol descriptions change.
- Keep the benchmark summary and reproduction method in that same runbook; large source data, scratch databases and raw logs remain ignored. Record limitations and failures as well as successful results.

## 5. Library updates only if justified

The inspected checkout declares DuckDB `^1.4.2` and locks 1.5.0. Initial isolated 2.0.0 tests improved parameter filtering but did not further shrink sorted files. Do not bundle a major upgrade solely to obtain the reported compression benefit.

At implementation time, verify current official releases and Python 3.14 wheel availability. Compare the installed/locked version, an appropriate maintained patch release within the existing constraint and a candidate major release in isolated environments. Reproduce the same layout and reader/writer checks sequentially, including duplicate ingestion and a production-scale file.

If upgrading is warranted, inventory all DuckDB consumers/extras, update `pyproject.toml` with a rationale comment and regenerate the lockfile. Check removed Python APIs, Arrow/Pandas compatibility, optional-import behaviour, Docker/CI wheels and extension compatibility. Confirm every producer/consumer can read the chosen file format. DuckDB 2.0 defaults new files to a format older readers cannot open: explicitly choose and test a supported storage version when mixed-version readers or rollback require it. Never silently upgrade production files while testing a library.

If there is no demonstrated benefit beyond the SQL/layout fixes, leave dependencies unchanged and record that decision. A major-version change should have its own compatibility evidence and rollout/rollback decision.

## Completion and handoff

For implementation, follow the worktree virtualenv and `.local-test.env` rules. Use `source .local-test.env && PYTHONPATH="$PWD" timeout 180s poetry run pytest <focused paths>`; copy the existing environment loader from the main checkout if missing and never edit it. Format only changed Python paths through Poetry. Do not run the full suite or build Sphinx.

Before implementation handoff, run `clean-up-for-pr` once, resolve independent findings and include smoke results, exact validation counts, ingestion results, migration recovery checks, documentation changes and the dependency decision. Keep implementation separate from production execution. This plan itself receives the requested Claude CLI review with Fable 5.2; record the completed result and substantive amendments below.

## Plan review

Initial inline-text review completed successfully on 2026-11-12 with Claude CLI `--model claude-fable-5-2`: **approve with amendments**. Incorporated bloat attribution, explicit ingestion growth budgets, typed/untyped benchmark controls, all-chain anomaly preflight, WAL recovery, durable journal phases, independent backup copies and retirement, downtime estimates and opt-in large tests. The primary agent verified that `MigrationContext.report()` persists immediately in apply; a second manifest was therefore unnecessary, while durable journal writes remain an implementation acceptance check. Final inline-text review of the amended plan completed successfully with the same exact model: **no blocking findings**. The review did not independently inspect the repository; code facts were checked by the primary agent.


## Implementation evidence

Implemented the read-only diagnostic, unsigned readers, ordered writers, stable
cache-directory access lock, exact compaction helper, numbered migration 065,
WAL/backup recovery records and runbook. Retained the current dependency version.
Full-source smoke tests passed for Ethereum, Base, Arbitrum, Monad, legacy
Unichain and sparse chain 5042. The 90-million-row synthetic file-backed check
includes correction and duplicate ingestion. An isolated complete Base copy
passed the actual numbered runner preview/apply/skip cycle. See
`docs/README-block-timestamp-storage.md` for committed results and reproduction.

The implementation review additionally required per-chain scratch cleanup,
whole-run free-space budgeting, backup WAL hashes and tests using the real
runner across an interrupted multi-chain attempt. Unexpected source/inventory
divergence remains deliberately fail-closed; the runbook describes correction
of an unsuccessful numbered script with reviewed content-based recovery, never
manual journal deletion or silent acceptance of unknown writes.


Final implementation checks: 108 focused tests passed, including the opted-in
90-million-row case. Ruff formatting, import/undefined-name checks and whitespace
checks passed. The independent Claude CLI Opus 5.5 follow-up found no blocking
correctness issues after recovery, scratch-space and validation fixes. Public
website description style review is not applicable; only engineering text changed.
Production application and backup retirement remain separate operator actions.

The requested cleanup follow-up added explicit-index/view rejection and legacy
primary-key acceptance coverage, documented the earlier PR failures, and made
the frozen WAL backup check unconditional. Missing or corrupted WAL backups now
abort before checkpointing or publication; tests also cover tampering with a
real retained backup WAL. The focused storage and migration-runner suite passed
83 tests, with the previously passed opt-in large test skipped in this run.
Formatting, import/undefined-name and whitespace checks passed. Claude CLI
Opus 5.5 completed the follow-up and confirmed the WAL findings were fixed with
no regression in the inspected changes. No PR or production action was taken.

Manual per-chain testing was subsequently requested and implemented through the
numbered script's direct entry point (`TIMESTAMP_CHAIN_IDS`) with separate
per-selection journals. Local Avalanche preview/apply preserved 90,617,032 rows
and reduced the active file from 1,559,506,944 to 47,722,496 bytes (96.94%).
The paired recovery evidence and independent backup remain on disk. Application
range/slicer/sparse lookup and replay checks passed against the canonical file;
all rows were compared against the backup again after replay. Correction,
append and reopen passed on a disposable copy. The other 31 chain files were
unchanged. The storage suite passed 29 tests (one opt-in skip), three existing
batch/optional-import regressions passed, and the strengthened manual-entry test
passed separately. Claude Opus 5.5 completed the scoped cleanup review and
confirmed the manual locking documentation finding resolved. The runbook records
that manual users must stop local scanners and reuse the same selector on retry.
