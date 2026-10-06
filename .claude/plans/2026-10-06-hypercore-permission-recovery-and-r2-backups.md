# Hypercore permission repair and recovery plan

Final policy for [issue #1628](https://github.com/tradingstrategy-ai/web3-ethereum-defi/issues/1628), updated on 2026-10-06.
Implementation and local recovery are complete; production deployment and apply remain pending.

## Problem and evidence

Repeated scans mixed current vault permission flags with historical portfolio prices,
overwrote historical state and lost dense points when the provider downsampled its history.
A price write clock cannot authenticate a permission measurement. The API cannot
reconstruct every missing point or historical deposit-policy transition.

Retained scanner snapshots and raw private R2 Parquet are restoration evidence.
Cleaned/resampled Parquet cannot restore exact scanner points. Preserve original
files, hashes, prices, write clocks and conflicting alternatives before repair.

## Collector and storage

- Append coherent permission receipts before portfolio, flow or cutoff checks.
  Successful responses with missing flags explicitly mean Unknown. Record failed
  requests separately. Record bulk-list closures before catalogue filtering;
  bulk open flags do not certify deposit permission.
- Keep permission/capacity clocks independent of portfolio and write timestamps.
  Price overlap updates preserve original write clocks and permission flags.
- Refuse constrained legacy price tables before collector initialisation.
  Recovery transactionally removes ART constraints, retaining every row and
  nullability. Use staging/hash joins and serialised writes for repeat ingestion.
- Publish an independent permission sidecar and select coherent whole snapshots.
  Unknowns and uncertainty boundaries prevent carrying earlier flags through gaps.
  Preserve non-Hypercore data with the existing Arrow-native merge.

## Recovery policy

- Default to a read-only dry run and JSON report. Apply checkpoints the stopped
  target and creates its own durable, SHA-256-verified backup before mutation.
  Production additionally verifies an exact immutable private R2 snapshot.
- Restore missing keys from ordered scanner sources, then raw Parquet. Retain
  existing target economics on conflicts and archive differing alternatives.
  Reject duplicate source/target keys before applying the repair.
- When no independent permission clock exists, use a selected backup's **original
  price timestamp**, labelled `legacy_price_timestamp`. Corrupted target flags
  cannot supply truth. Scanner keys suppress raw-Parquet inferred observations,
  even when scanner flags are null. HF wins over daily at identical inferred clocks.
- Genuine observations, including explicit Unknown, take precedence. Inferred
  flags expire two days after their original clock and cannot cross an unrecovered
  gap. Raw-only flags may already be carried forwards and remain approximate.
  Recovery supplies no fresh capacity. Unrecoverable status remains Unknown.
- Stable evidence identities and canonical inferred replacement make repeated
  apply idempotent and permit retraction of invalidated inferred annotations.

## Backup cadence and production order

Registered Hyperliquid, other native vault, risk, settlement, currency, context and
accounting DuckDBs share a conditional remote gate: at most one attempt every
48 hours, including failures. Bootstrap honours recent legacy flat/daily copies.
Deferred cycles avoid database hashing/checkpoint/copy/upload. Unknown files are
logged; independently owned nested databases require explicit registration.

Reserve the daily/HF maintenance pair, wait until both gates are due, stop owners,
then take gated offhost snapshots. Run fresh production dry runs, review reports,
and apply against those exact snapshots. Verify counts, economics, evidence,
idempotence and exports before restarting. Preserve production mounts, HOME,
reader states and dense timestamp caches; do not deploy a local rehearsal file.
See the [operator runbook](../../docs/README-hypercore-permission-recovery.md).

## Consumer rollout and validation

Manifest v2 remains opt-in until authenticated serving, client and executor
consume the paired sidecar. Track original permission age separately from capacity;
legacy clocks fall back to the source price timestamp. Client work is tracked in
[Trading Strategy #252](https://github.com/tradingstrategy-ai/trading-strategy/issues/252).

Validate missing-key recovery, own-backup failure, genuine Unknown precedence,
clock rounding and age, source priority, retraction/idempotence, threaded ingestion,
remote concurrency/cadence and failure isolation. Rehearse on production-scale
file-backed copies and the production Parquet, and run an isolated authenticated R2
round trip. Review the implementation with Claude CLI Opus 5.5 after reading local
CLI guidance. The [local results](../../docs/protocol-research/hypercore-permission-recovery-1628-implementation.md)
and [DOEZOE daily report](../../docs/protocol-research/doezoe-deposit-status-2026-recovered.md)
record the corrected policy's outcome.
