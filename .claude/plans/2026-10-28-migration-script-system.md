# Ad hoc database migration system

Approved implementation plan, 28 October 2026 UTC. The original plan and daemon
guard amendment were reviewed with Claude CLI `claude-opus-5-5`; both final
plan reviews reported “No blocking findings.” This file records the approved
scope; operational instructions live in [the migration guide](../docs/migration-scripts.md).

## Runner and script contract

- Add `eth_defi/vault_protocols/migration.py`, typed `MigrationSpec` and
  `MigrationContext` dataclasses, and the Poetry `vault-migrate list|run` command.
  Support module execution. Default `DRY_RUN` to true, accepting only true/false
  without case sensitivity. Syntax errors exit 2; execution/validation errors
  exit 1; success or nothing pending exits 0.
- Store scripts in `migration/scripts/001-name.py` onwards. Require unique
  positive counters padded to at least three digits. Discover and validate
  filenames, syntax, documentation and entry points through AST without imports.
  Execute the exact validated, checksummed bytes, sequentially in numeric order.
- Each LLM-generated, human-reviewed script has a leading docstring with purpose,
  reviewed scope, original branch, exact PR title and links, affected data,
  preservation requirements and expected outcome. It exports
  `migrate(context: MigrationContext) -> None`. `DRY_RUN` is the sole
  migration-specific operator parameter; targets and boundaries are reviewed
  constants. Credentials, deployment provenance and storage are infrastructure.
- Share one staged `VaultDatabase` across the initial metadata batch. Preview
  retains staged changes for later scripts without persistent writes. Apply
  backs up without overwriting, writes atomically and validates the committed
  data. Helpers operate on the supplied database without reacquiring locks.
- Explicitly package migration resources in wheels, source distributions and
  Docker. Resolve them independently of the working directory and verify that
  they come from the same installation as `eth_defi`.

## Exclusion and production launcher

- Both preview and apply refuse while `vault-scanner-looped` is running,
  including while sleeping; listing remains available.
- Add a non-blocking Linux `flock` on a read-only directory descriptor in
  `eth_defi/vault/scanner_lock.py`. Hold it for the complete daemon lifetime,
  before pipeline initialisation, database access or provider calls, across
  cycles, retries and sleep. Create no PID, heartbeat or lock file; fail closed.
- Enable daemon guarding for the fixed Compose `VAULT_SCANNER_DAEMON=true`
  marker or a positive `LOOP_INTERVAL_SECONDS`. Bind mounts share the directory
  inode between host and containers. A daemon starting during migration exits
  before initialising its pipeline.
- Every migration run acquires the guard before reading state or scripts, even
  for previews and empty batches. Apply then acquires the existing
  `scan-pipeline` writer lock with at most 60 seconds' wait. Ordering is always
  lifecycle guard then pipeline writer lock. Converted helpers do not relock.
- Add `scripts/run-vault-migrations.sh list|run`. Before run, inspect all looped
  scanner containers in the Compose project. Allow only absent, created or
  exited service instances; refuse running, paused, restarting, unknown status
  and Docker inspection failures. Never stop the daemon automatically.
- The launcher runs the one-shot service with its existing persistent mounts,
  migration entry point, forwarded `DRY_RUN` and host-inspected image ID. Do not
  mount a Docker socket. The lifecycle guard closes the startup race.
- Use the launcher in production, especially for rollout from old images without
  the guard. Quiesce other one-shot and host metadata writers during previews.

## Journal, provenance and recovery

- Store `~/.tradingstrategy/vaults/migration.json` as a dictionary keyed by full
  script filename. Resolve journal and data through `get_pipeline_data_dir()`;
  allow `PIPELINE_DATA_DIR` for isolation and reject conflicting per-file paths.
- Each entry contains `first_run_at`, `last_run_at`, `docker_image_hash`,
  `git_commit_hash`, `success`, `modified_files`, `other_data`, `script_sha256`.
  Use ISO naive UTC timestamps; preserve first attempt across retries.
- Journal running/unsuccessful status before execution, progress and committed
  file reports during execution, and success only after commit validation.
  Preserve partial progress on failure and stop the batch. Retain cumulative
  backup/modified paths but no full attempt history. Include complete counts,
  at most 100 example identifiers and full change details in logs.
- Atomically replace JSON through a same-directory temporary file, file fsync,
  replacement and directory fsync. Reject corruption/incompatible state.
  Preview and guard refusal do not modify the journal.
- Skip successful scripts; retry failed/interrupted scripts idempotently.
  Treat stale running status as interrupted only after acquiring the guard.
  Reject counter reuse under another name, newly inserted lower counters and
  successful script checksum changes. Record corrections of failed scripts.
  Future accessor refreshes need a new number. No cross-file transaction or
  exactly-once guarantee, force option, selection or rollback engine.
- Docker apply requires a stamped commit and actual host-inspected image ID.
  Outside Docker record checkout commit and dirty status, with null image ID.

## Initial batch

Implement against master containing the twelve PRs merged on 28 October 2026.

| Script | Source PRs | Scope |
| --- | --- | --- |
| 001-flying-tulip-ssftusd-metadata.py | #1679 | Fixed Ethereum ssftUSD |
| 002-nova-markets-vaults.py | #1688 | Fixed Arbitrum Nova pool |
| 003-gtrade-metadata.py | #1688 | 24 Gains-family deployments excluding Nova |
| 004-alphaping-descriptions.py | #1686 | Frozen 36-deployment inventory |
| 005-accountable-reviewed-metadata.py | #1687 | Ten chain/address-aware descriptions, notes and links |
| 006-arcadia-vault-metadata.py | #1685 | Existing missing or label-only Arcadia descriptions |
| 007-wrapped-hlp-illiquid.py | #1677 | Fixed HyperEVM illiquid flag and note |
| 008-vault-strategy-tags.py | #1675, #1683, #1688 | Supported row-based strategy refresh after Nova |
| 009-normalise-vault-descriptions.py | #1681, #1682 | Global description normalisation last |

Preserve field restrictions and identity checks. Required missing single targets
fail. Partially missing historical inventories retain skip/report behaviour;
wholly missing required inventories fail. No Arcadia rows is a no-op.
Accountable preserves the known manager-attribution limitation. Nova preserves
reader cursors, compatible reader state and prices. Add no historical backfill:
Liquid Euro #1684 uses ordinary cleaning/export; Llama Lend #1682 uses its
metadata-version refresh and future observations; Anvil #1674 needs no migration.
Retire superseded standalone commands, retaining broader legacy tools only where
the numbered repair covers a fixed subset.

## Documentation and rollout

Write the authoring/operations guide; link it from the vault scripts README,
root README, agent instructions, PR template and protocol runbooks. Preserve PR
sections and the production reminder. Add API stubs without building Sphinx.
Production: inspect Compose; deploy the guarded scanner and build the stamped
image; stop the daemon and competing writers; use the launcher to list, preview
and apply with maintenance price scanning disabled and mounts preserved;
restart/recreate the daemon with the same inspected image ID; let the normal
cycle clean/export/upload; validate repaired fields afterwards.
**Do not run production migrations during implementation.**

## Required validation

- Guard exclusion during scanning/sleep, before provider/data/journal access;
  listing available, daemon startup blocked, simultaneous startup single owner,
  process exit releases lock, cross-container shared-mount contention, lock order.
- Docker preflight active/paused/restarting/multiple/unknown/error cases; denied
  launches never start one-shot containers.
- Discovery, counters, checksums, journal validation, recovery and retries;
  full ordered batch, staged previews, frozen scopes, repeat applies; preserve
  unrelated metadata, prices, reader state and timestamp caches; Nova ordinary
  scanner semantics and compatible reader state.
- Focused real Flying Tulip and Arcadia provider checks; resource discovery and
  CLI from the worktree, installed wheel and Docker image.
- Use parent Poetry with worktree-first `PYTHONPATH`, source `.local-test.env`,
  180-second focused pytest timeouts and format changed Python files.
