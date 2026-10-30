# Numbered database migrations

Use this system for one-off database repairs and refreshing values persisted
from Python accessors. Migrations are explicit maintenance operations; merging
a PR or starting the scanner does not execute them.

## Legacy standalone scripts

Standalone `migrate-*.py`, `fix-*.py` and one-off backfill scripts are the
**legacy migration format**. Do not create, copy, extend or run that format for
database repairs, data/schema migrations, historical lead repairs or persisted
Python accessor refreshes. Put each reviewed operation in a new numbered
`migration/scripts/NNN-name.py` script and run it through `vault-migrate`.

Existing standalone tools and their old runbook commands are retained for
historical reference and reusable implementation code. They are not the
supported workflow for these tasks. Before reusing an old repair, move its
transformation into a helper that uses `context.metadata()` or other
context-controlled storage and expose it through a new numbered script.
The runner owns the lifecycle and writer locks, journal and metadata commits;
helpers must not reacquire locks.

Use the next unused counter for every later refresh, including another change
to strategy tags or descriptions. A successful initial script such as 008 or
009 is skipped; do not edit it or rerun its old standalone counterpart to
apply a later change.

Recurring scanners, collectors and normal historical readers keep their
existing entry points. Read-only diagnostics and test-data extraction can use
ordinary scripts. A one-off operation that changes persisted production data
belongs in the numbered system, including when it reuses a historical reader.

## Commands and storage

```shell
poetry run vault-migrate list
DRY_RUN=true poetry run vault-migrate run
DRY_RUN=false poetry run vault-migrate run
```

The module equivalent is `poetry run python -m eth_defi.vault_protocols.migration`.
An installed console command is provided by Poetry. When testing a worktree
with the parent virtualenv, use the module form and `PYTHONPATH="$PWD"` so
imports and migration resources come from this worktree.

`run` defaults to preview. Only `true` and `false`, ignoring case, are accepted.
Scripts run in numeric order, stop at the first failure, and skip successful
entries. There is no force/selection option. Exit codes are 0 for success or
nothing pending, 1 for validation/execution failure, and 2 for CLI usage errors.

Scripts live in `migration/scripts/001-name.py` onwards. State defaults to
`~/.tradingstrategy/vaults/migration.json`. The runner resolves metadata and
state together through `get_pipeline_data_dir()`; `PIPELINE_DATA_DIR` supports
isolated snapshots. Conflicting legacy `VAULT_DB`/`VAULT_DB_PATH` values fail.
Do not redirect only the metadata file while retaining production state.

## The daemon must be stopped

Both preview and apply refuse an active `vault-scanner-looped`, even between
scan cycles or with no pending migrations. `list` remains available.

The daemon and runner hold an exclusive Linux `flock` on the shared pipeline
directory descriptor. This lifetime guard stays held during scanner sleep,
creates no persistent lock file, and works across local Docker bind mounts.
The fixed Compose marker `VAULT_SCANNER_DAEMON=true` also covers an accidentally
zero loop interval; standalone scanners with a positive interval are guarded.
Unsupported locking and inspection errors fail closed.

Apply also takes the existing `scan-pipeline` writer lock. Always acquire the
lifetime guard first, then the writer lock. Never take either lock again from
a migration. A daemon starting during maintenance exits without initialising
the pipeline. Kernel locks disappear after the owning descriptors close;
never infer liveness from PID files or lock-file existence.

The production launcher additionally checks **all** looped-service containers
in the current Compose project, plus the fixed production container name from
older checkouts/projects. Preflight and execution use the same base Compose
file. Running, paused, restarting, unknown and uninspectable services are
refused. Only conclusively absent, created or
exited service containers pass. It pins the one-shot to an inspected image ID.
It never stops or restarts services, and needs no container Docker socket.

Older images lack the lifetime guard. Stop the old daemon explicitly during
the first upgrade. Direct Compose commands are the standard production interface;
the host launcher remains available when Docker preflight and automatic image
pinning are needed. Also quiesce one-shot and cron metadata writers: the lifetime
guard only covers cooperating processes, while the writer lock protects apply
against existing scanner writers. Previews require quiet data because they do
not take the pipeline writer lock.

## Authoring a migration

Allocate the next unused positive counter, padded to at least three digits.
Resolve counter collisions before merge. Gaps are allowed, but a previously
unseen counter below an applied migration is rejected. Never rename a recorded
script or edit a successful script; add a new numbered correction instead.
When changing accessory functions whose results are persisted, add another
migration even if an earlier script called the same helper.

LLM-generated scripts require human review. Freeze exact reviewed address
inventories, or explicitly describe a database-wide field transformation.
Use shared protocol resolvers rather than copying business rules.

```python
"""Refresh the reviewed persisted field.

Purpose: Explain the defect and correction.
Scope: Exact deployments and fields, or explicit database-wide scope.
Branch: feature-branch
PR title: fix: exact PR title
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/NUMBER
Preserves: Unrelated metadata, prices, leads, reader state and timestamp caches.
Expected: Reviewed target and changed-field counts; state is safe to retry.

LLM-generated operational migration; changes require human review.
"""

from eth_defi.vault_protocols.migration import MigrationContext


def migrate(context: MigrationContext) -> None:
    """Stage and commit the reviewed repair.

    Validate all proposed changes before updating the shared staged database.
    The context owns backups, preview staging and atomic persistence.

    :param context: Guarded attempt and shared metadata database.
    :return: None after successful validation and commit/preview.
    """
    database = context.metadata()
    # Apply the reviewed transformation to database.rows here.
    context.report(inspected_rows=len(database.rows))
    context.commit_metadata()
```

`DRY_RUN` is the only migration-specific production input. Read
`context.dry_run` for non-metadata operations. Chains, addresses, fields and
historical ranges are constants, not environment scope switches. RPC/API
credentials, logging, worker limits and deployment provenance are
infrastructure configuration. The one exception is the development-only
`MIGRATION_BACKFILL_BLOCKS` limit for a local backfill preview, described in
[Historical price backfills](#historical-price-backfills).

`context.metadata()` returns the same staged database throughout the batch.
Metadata transformations run against it in both modes. `commit_metadata()`
keeps proposals in memory during preview, so later scripts see earlier changes.
During apply it creates a unique backup, writes atomically and validates the
written bytes. An unchanged database is not rewritten. Forgetting to commit
staged changes fails the attempt.

For other databases, implement idempotent/resumable writes using their existing
writers and transactions, respecting the repository's DuckDB/Parquet rules.
Use `context.report()` for JSON-safe metrics and `record_modified_file()` only
after actual commits. A script must never directly edit the journal. There is
no general cross-file transaction or storage virtualisation layer.

Historical price backfills have their own rules; see
[Historical price backfills](#historical-price-backfills).

Preview must not write databases, the journal, backups, provider caches or log
files. Put provider/token caches in `TemporaryDirectory`. Script imports must
not perform network reads or data writes. Keep long operations observable with
progress reporting; never include credentials in reports.

## Historical price backfills

#### When a backfill is needed

Uncovering, repairing, reclassifying or matching vault data often reveals a
vault whose price history starts late. The scanner only collects prices from
the point where it knew about the vault and could read it. Typical causes are a
protocol adapter added after the vault launched, a detection or activity filter
that skipped the vault, or a metadata repair that made it eligible. The earlier
prices still exist in onchain state, but nobody collected them.

A numbered migration closes that gap by reading old onchain state and writing
the missing rows to `vault-prices-1h.parquet` for the reviewed vaults only.
Put the backfill in the same migration as the repair that uncovered it, or in a
separate numbered migration that follows it.

#### Rules

- **Only the vaults and chains in question.** Freeze each vault's chain,
  address and half-open block range as constants. Usually the range runs from
  the vault's first-seen block to its first existing price row, so rows the
  scanner already collected stay untouched and the range never overlaps
  ordinary scanning. Never rescan a whole chain or reset reader state.
- **Fast.** Scan only the gap, at the hourly sampling of the raw file, with
  one connection per chain. A backfill of a few vaults should take minutes.
- **No production parameters.** Production runs the normal
  `vault-migrate run` with `DRY_RUN` only. RPC endpoints, Hypersync
  credentials, `MAX_WORKERS` and `TIMESTAMP_CACHE` remain infrastructure
  configuration.
- **Optional block limit for local development only.** To see that a new
  backfill works without scanning the whole range, set
  `MIGRATION_BACKFILL_BLOCKS=<blocks>` for a preview. Each range is limited to
  its first `<blocks>` blocks. The variable is refused when applying, so a
  partial history can never be journalled as a completed backfill.
- **Preserve data.** Only the reviewed vaults' rows inside each range are
  replaced. Metadata, reader state, other vaults and later rows are kept.
  Validate the row accounting before publishing and fail the migration if the
  required price work cannot run. Never journal a skipped backfill as success.
- **Dense timestamp cache first.** The per-chain cache at
  `~/.tradingstrategy/block-timestamp/{chain_id}-timestamps.duckdb` must exist
  and should cover the range. Gaps are fetched through Hypersync one sampled
  block at a time, which is slow under a low rate limit. Check with
  `DRY_RUN=true TEST_CHAINS=<chains> poetry run python scripts/erc-4626/heal-timestamps-all-chains.py`
  and heal before a production backfill.

#### Monad

[Monad full nodes retain all historical transaction data but only a
provider-dependent window of historical state](https://docs.monad.xyz/developer-essentials/historical-data).
The common writer probes Multicall and starts at the oldest block whose state
the configured RPC can read. Existing rows before that boundary are preserved;
a provider cannot reconstruct them once its state trie has been evicted. If
even the last block of a Monad range is unreadable, the backfill helper
reports `monad_state_unavailable` for that vault and keeps its rows. This is
the only accepted skip of a vault that exists. Do not retry old `eth_call` requests, start a Monad
scan at block 1, or delete rows in an attempt to rebuild unavailable state.
Run Monad backfills soon after a gap is found, while the state is still
readable.

#### Writing a backfill migration

Use `run_price_backfill()` from
[`eth_defi/vault_protocols/repairs/price_backfill.py`](../../eth_defi/vault_protocols/repairs/price_backfill.py).
It creates each vault's reader from its metadata detection record, scans each
range into one staged copy of the raw price file through
`scan_historical_prices_to_parquet(vault_addresses=...)`, and validates it:

- the scan covered the reviewed chain and range;
- only the reviewed vault's rows inside the range were deleted, and the staged
  file's total row count matches the deletions and new rows, so no other
  vault's rows were added or removed;
- every vault produced at least one share price;
- the vault's rows at or after the end block are unchanged, compared column by
  column with NaN-aware equality;
- reader state is unchanged.

Apply keeps the original file as a hard-link backup, atomically publishes the
staged file once and journals both paths. Preview performs the same reads on
copies of the price file and timestamp caches and discards them. A target
vault absent from the metadata database is reported as `missing_vault` and not
scanned. When the reviewed vaults are required, the migration must check that
their rows exist and fail otherwise before calling the helper, as
`016-neutral-trade-autopilot-price-backfill.py` does.

```python
from eth_typing import HexAddress

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.price_backfill import PriceBackfillTarget, run_price_backfill

#: Reviewed gaps: first-seen block to the first collected price row (exclusive).
TARGETS = (
    PriceBackfillTarget(1, HexAddress("0x..."), 25_846_919, 25_973_565),
)


def migrate(context: MigrationContext) -> None:
    """Backfill the reviewed price gaps."""
    run_price_backfill(context, TARGETS)
```

Find the range from the production snapshot: the vault's
`_detection_data.first_seen_at_block` and the first `block_number` of its rows
in `vault-prices-1h.parquet`. Record both, with the snapshot date, in the
migration docstring. See
[`016-neutral-trade-autopilot-price-backfill.py`](../../migration/scripts/016-neutral-trade-autopilot-price-backfill.py)
for a complete example.

Vaults whose readers need extra context, such as event observations or
protocol-specific DuckDB tables, must prefill that context first, as the
legacy GMX, YieldBasis, Rysk and Flying Tulip backfills below do. Then call
the common writer with the same address scope.

#### Testing a backfill locally

Copy the production snapshot into an isolated directory and preview with a
small block limit:

```shell
mkdir -p /tmp/backfill-snapshot
cp ~/.tradingstrategy/vaults/{vault-metadata-db.pickle,migration.json,vault-prices-1h.parquet,vault-reader-state-1h.pickle} /tmp/backfill-snapshot/
source .local-test.env && PIPELINE_DATA_DIR=/tmp/backfill-snapshot DRY_RUN=true MIGRATION_BACKFILL_BLOCKS=20000 HYPERSYNC_RPM=14 \
  PYTHONPATH="$PWD" poetry run python -m eth_defi.vault_protocols.migration run
```

The preview runs every pending migration, so earlier pending scripts may need
their own credentials. The `price_backfill` report lists, per vault, the
planned and scanned ranges, replaced, written and preserved row counts, and
the first and last written timestamps. The helper requires free space of twice
the raw price file plus the timestamp caches of the chains in scope.

Set `HYPERSYNC_RPM` to your key's quota. The client defaults to 80 requests a
minute; with a 15-request development key the server rejects requests whenever
timestamps are missing from the local cache, and the scan backs off until it
fails. If a preview is killed, delete the leftover `price-backfill-*` scratch
directory from the snapshot directory.

Unit-test the migration's fixed scope with the helper replaced, as
`tests/vault/test_numbered_migrations.py` does. The helper itself is covered by
`tests/vault/test_price_backfill.py`.

#### Legacy backfill examples

These standalone scripts predate the numbered system. They are legacy format:
do not run them for a new repair. Port the operation into a numbered migration,
reusing their reader and context helpers, and keep their lessons.

| Legacy script | What it backfills | Lessons for a numbered migration |
|---|---|---|
| [`backfill-kamui-vault-prices.py`](../../scripts/erc-4626/backfill-kamui-vault-prices.py) | Three Ethereum Kamui Lagoon vaults skipped by the activity filter until [#1606](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1606) | Fixed range from first-seen block to the first collected price; staged copy; exact preservation of later rows, including NaN values. `run_price_backfill()` generalises this script. |
| [`backfill-frankencoin-shares.py`](../../scripts/erc-4626/backfill-frankencoin-shares.py) | Ethereum Frankencoin Shares metadata and hourly prices from deployment block 25,852,506 | Metadata reclassification and price backfill in one fixed-scope operation; publish only a non-empty scan. |
| [`migrate-rysk-vaults.py`](../../scripts/erc-4626/migrate-rysk-vaults.py) | Eight Rysk Premium pools on Ethereum and HyperEVM, prices reconstructed from epoch events | Metadata stage before history stage; protocol context table written beside the price file; preflight free space for the Parquet copy and its rewrite. |
| [`backfill-history.py`](../../scripts/enzyme/backfill-history.py) | All Enzyme Blue and Onyx vaults on four chains | One chain-wide historical Multicall instead of per-vault scans. Classify `missing trie node`, `metadata is not found` and `layer stale` responses as `MulticallHistoricalDataUnavailable`, rotate once through the other providers for that block, and stop rather than publish a partial history. Resumable checkpoint for long scans. |
| [`backfill-gmx-vault-prices.py`](../../scripts/erc-4626/backfill-gmx-vault-prices.py) | GMX V2 GM and GLV tokens on Arbitrum and Avalanche from block 1 | Prefill event observations through bounded Hypersync pages; DuckDB context tables without `PRIMARY KEY` or `UNIQUE`; examiner script to validate source linkage and price identities. |
| [`backfill-yield-basis-vault-prices.py`](../../scripts/erc-4626/backfill-yield-basis-vault-prices.py) | Four Ethereum YieldBasis LT markets | Prefill the protocol context table, then one address-scoped write; reuse the dense timestamp cache even in preview; structural and performance examiners before publishing. |
| [`backfill-flying-tulip-history.py`](../../scripts/erc-4626/backfill-flying-tulip-history.py) | Flying Tulip sftUSD source events and Curve price provenance | Store replayable source evidence; the contextual reader writes prices later. |
| [`backfill-antarctic-vaults.py`](../../scripts/erc-4626/backfill-antarctic-vaults.py) | Arbitrum AMLP and AHLP metadata and settlement prices | Fixed two-address scope across metadata, context DuckDB and price file; never resets reader state. |
| [`backfill-tokenised-funds.py`](../../scripts/backfill-tokenised-funds.py) and [`backfill-tokenised-fund-prices.py`](../../scripts/erc-4626/backfill-tokenised-fund-prices.py) | Tokenised-fund NAV histories, such as Securitize products with RedStone push feeds | Daily sampling for slowly changing NAVs; `write_all_samples` keeps freshness for flat prices; RedStone's REST API cannot serve history older than 30 days, so read push feeds onchain. |
| [`backfill-forgeyields.py`](../../scripts/erc-4626/backfill-forgeyields.py) | ForgeYields TVL from the provider API | Offchain history fills a column the chain cannot supply; match rows by nearest timestamp. |

## Journal and recovery

The top-level dictionary is keyed by full filename. An entry has this shape:

```json
{
  "001-flying-tulip-ssftusd-metadata.py": {
    "first_run_at": "2026-10-28T20:00:00",
    "last_run_at": "2026-10-28T20:00:00",
    "docker_image_hash": null,
    "git_commit_hash": "<40 hexadecimal characters>",
    "success": true,
    "modified_files": ["/root/.tradingstrategy/vaults/vault-metadata-db.pickle"],
    "script_sha256": "<64 hexadecimal characters>",
    "other_data": {
      "status": "succeeded",
      "changed_rows": 1,
      "backup_paths": ["<reported backup path>"],
      "modified_files_all_attempts": ["<reported paths>"]
    }
  }
}
```

Timestamps are naive UTC attempt-start times. First-run time survives retries;
latest-attempt fields are replaced. Recovery paths accumulate without storing
a full attempt history. Changed-entry examples are limited to 100; complete
counts and full log details remain available.

In Docker, `git_commit_hash` is read from `GIT_VERSION_HASH.txt` inside the
image, written by `scripts/build-container.sh`. No host image hash or exported
provenance variable is needed. The legacy `docker_image_hash` field remains
null for new attempts; existing journal entries with image hashes stay valid.

The runner saves `running`/`success=false` before executing a script, reports
progress and commits, then marks success only after validation. Failure stops
the batch. A stale running entry becomes an interrupted prior attempt on retry.
A crash between a database commit and journal update requires idempotent retry;
the system does not claim exactly-once execution. Failed scripts may be corrected,
with their previous checksum retained. Successful scripts must remain unchanged.

Malformed JSON and incompatible state abort. Never delete/reset migration.json
to work around an error. Preserve it with the databases when backing up or
restoring a snapshot. If restoring data to before a migration, restore the
matching journal too, or add an explicitly reviewed recovery migration.

Use the reported backup paths for recovery; no automatic rollback or backup
retention deletion is performed. Preserve historical prices and reader state.

## Production runbook

The standard production apply command, run from
`~/vault-scanner/web3-ethereum-defi`, is:

```shell
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
```

Before maintenance, inspect `docker-compose.yml` and verify its persistent
mounts. Update the production checkout to merged master and build with
`scripts/build-container.sh`, which stamps the git revision. The engine reads
this commit from inside the image; no Docker image hash or additional environment
export is required. An unstamped image cannot apply migrations. Do not rebuild
or retag `vault-scanner:local` during maintenance. A host checkout update alone
does not update the code or console entrypoint inside the image.

List migrations:

```shell
source ../vault-rpc.env && docker compose run --rm --no-deps --entrypoint vault-migrate vault-scanner-oneshot list
```

Stop the persistent writers, and finish or stop other one-shot/cron metadata
writers before preview or apply:

```shell
source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
```

Preview all pending migrations, then review the reported targets and changes
before using the apply command above:

```shell
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
```

Both preview and apply take the lifecycle guard before reading migration state
or importing scripts. An updated looped scanner holds that guard even while
sleeping. During the first upgrade, stop the old unguarded daemon explicitly;
the engine cannot detect an older daemon that does not hold the shared lock.
These commands override the normal scanner entrypoint and run only the pending
numbered migrations, using the service's existing persistent mounts.

After a successful apply, recreate the persistent services with the same image:

```shell
source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner
docker compose exec vault-scanner-looped cat /usr/src/web3-ethereum-defi/GIT_VERSION_HASH.txt
```

Confirm the printed commit matches `git_commit_hash` in migration.json.
Keep normal cleaning, post-processing, protocol export, top-vault JSON export
and uploads enabled. **Publication is performed by restarted
`vault-scanner-looped`, not by the migration runner.** Validate the repaired
fields after its successful cycle. Migration success alone is not publication.
On failure, inspect the journal and backups before retrying or restoring data.

The optional `scripts/run-vault-migrations.sh list|run` host launcher performs
Docker status preflight, automatically pins the image ID, and disables
incidental maintenance price scanning. It uses the same one-shot service and
mounts; journal provenance still comes from the image's git stamp. Outside
Docker, source runs record the checkout commit and dirty status, or null with
a diagnostic when the source commit is unavailable.

## Initial batch: 28 October 2026

| Counter | Repair | Source PRs |
|---|---|---|
| 001 | Ethereum Flying Tulip ssftUSD metadata | #1679 |
| 002 | Arbitrum Nova identity, preserving its compatible reader state | #1688 |
| 003 | 24 Gains-family deployments, excluding Nova | #1688 |
| 004 | 36 AlphaPing descriptions | #1686 |
| 005 | Ten Accountable chain/address-specific descriptions, notes and links | #1687 |
| 006 | Missing/label-only descriptions across existing Arcadia rows | #1685 |
| 007 | Wrapped HLP illiquidity flag and note | #1677 |
| 008 | Shared strategy tags across supported existing rows | #1675, #1683, #1688 |
| 009 | Description normalisation across existing rows | #1681, #1682 |

Nova precedes the strategy refresh; normalisation runs last. Accountable's
manager attribution limitation remains outside this repair. Missing required
single targets fail. Partial historical inventories are reported; absent
required inventories fail. An empty Arcadia population is a valid no-op.

Liquid Euro (#1684) needs the normal clean/export cycle, not a backfill.
Llama Lend (#1682) uses metadata-version refresh and future observations.
Anvil (#1674) needs no database migration. No historical price migration or
reader reset is introduced. Exact source branches, titles and links live in
each numbered script's docstring.

## Pull request reminder and checks

Preserve the repository's Why, Lessons learnt, Summary, Related issues and PRs,
and Unrelated CI and test fixes sections. State whether migrations are needed
or N/A. Include a production reminder comment with numbered filenames, exact
launcher commands, expected counts, affected files, validation criteria and
**Run after merge**. Record the result after operation; do not imply merge ran it.

Focused local checks:

```shell
source .local-test.env && PYTHONPATH="$PWD" timeout 180s poetry run pytest tests/vault/test_migration_runner.py tests/vault/test_numbered_migrations.py tests/vault/test_migration_launcher.py
source .local-test.env && PYTHONPATH="$PWD" RUN_MIGRATION_LIVE_TEST=1 timeout 180s poetry run pytest tests/vault/test_numbered_migrations.py::test_live_flying_tulip_and_arcadia_preview
source .local-test.env && PYTHONPATH="$PWD" RUN_MIGRATION_DOCKER_TEST=1 timeout 180s poetry run pytest tests/vault/test_migration_launcher.py::test_guard_contends_across_docker_bind_mounts
```

The live check uses supplied `JSON_RPC_ETHEREUM` and the public Arcadia API;
all state is temporary. Record command, UTC date, result and redacted provider
in the implementation PR comment. Docker tests mount only temporary test data.
