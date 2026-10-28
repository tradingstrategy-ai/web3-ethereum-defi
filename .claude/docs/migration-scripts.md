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

`DRY_RUN` is the only migration-specific input. Read `context.dry_run` for
non-metadata operations. Chains, addresses, fields and historical ranges are
constants, not environment scope switches. RPC/API credentials, logging,
worker limits and deployment provenance are infrastructure configuration.

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

Numbered historical backfills must call the reviewed reader/writer functions
with their fixed scope explicitly. The host launcher sets `SCAN_PRICES=false`
and `UPSHIFT_SCAN_PRICES=false` to suppress automatic maintenance scans. Do not
call a legacy CLI wrapper that silently skips the required price work under
those flags; refactor its helper to take the reviewed operation explicitly.
Before a historical price backfill, verify that its required dense timestamp
cache is present. Validate the written range and counts, and fail the migration
if required price work cannot run or was skipped. Never journal such a skip
as success. Preview must exercise the same work using isolated output storage.

Preview must not write databases, the journal, backups, provider caches or log
files. Put provider/token caches in `TemporaryDirectory`. Script imports must
not perform network reads or data writes. Keep long operations observable with
progress reporting; never include credentials in reports.

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
