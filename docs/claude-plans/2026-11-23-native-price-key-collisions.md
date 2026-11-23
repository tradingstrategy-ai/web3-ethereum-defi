# Repair native price publication after duplicate key failures

## Incident and scope

On 23 November 2026 the six native collectors finished, then the incremental price merge introduced in [PR #1812](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1812) rejected the ApeX history and the combined candidate. The top-vault JSON reached both buckets by 14:02 UTC. The subsequent raw price replacement failed. Private native price publication remained pending across three attempts. Repeated retries cost almost four minutes each.

The deployed commit was `bc2d1df3c`. The 10:24 UTC raw Parquet contained 24,881,194 rows. Two findings from the read-only audit determine the repair:

| Population | Evidence | Required outcome |
| --- | --- | --- |
| ApeX, synthetic chain 9995 | 109,361 excess rows across 686 vaults. Each duplicated millisecond key matched one microsecond DuckDB observation. The source had zero collisions at millisecond precision. | Reconcile one stored observation per key and preserve the price and available enrichment. |
| EVM history | 1,040 pairs on Arbitrum, Avalanche, BNB Chain, Robinhood and Base. Every pair had distinct block numbers at the same vault address and second. | Preserve both block observations in every pair and every other retained row. |

For example, one ApeX ranking observation at 14:17:00.907127 UTC on 24 July was stored at 14:17:00.907. The old merge compared the full source timestamp against the stored millisecond, retained both, then cast both to the same millisecond. Use these counts for the inspected snapshot. Recompute them from frozen inputs for a later deployment.

## Changes to the merge

Use the [native history merge](../../eth_defi/vault/post_processing.py) to compare fresh and stored ApeX observations at the millisecond precision of the raw schema. Keep the original source timestamps for point-in-time metric joins and collision diagnostics. Reject ambiguous inputs in full and incremental mode. A fresh correction wins over the stored row, while stored-only history survives.

Validate keys within the native replacement scopes using the existing combined sort. Keep the namespace checks for Lighter and case-sensitive Solana addresses. Retained EVM blocks can legitimately share a timestamp. The unchanged-source path continues to use revision checks, and a batch of successful changed scopes shares a single raw rewrite.

## One-time repair

[Migration 106](../../migration/scripts/106-apex-price-key-collisions.py) uses the frozen raw Parquet and matching checkpointed ApeX DuckDB. It preserves unique rows and compares all fields in each duplicate pair, including extension columns. Whenever duplicates exist, the repair requires a checkpointed source archive with unambiguous keys. An identical pair may be collapsed even when that particular observation is absent from the archive. A price or other core-field disagreement, a group larger than two, or ambiguous source observations stops the repair.

For pairs that differ in enrichment, reconstruct the quote asset, position data status and metrics observation time using the [scanner’s point-in-time join](../../eth_defi/perp_dex/parquet.py) over the affected vault’s full price timeline. The source observation and selected snapshot must resolve the conflict. Preserve the agreed stored price for the next normal merge to correct. Apply invalidates the ApeX revision and preserves unrelated footer metadata.

Preview and apply stage on the data volume. Before publication, compare retained non-ApeX rows against the original and the complete ApeX partition against the authorised removals and field updates. Apply publishes atomically and keeps a backup. A repeat against the repaired file skips the rewrite.

## Verification and rollout

On an isolated copy of the 23 November snapshot, the repair reduced 24,881,194 rows to 24,771,833 and reconstructed enrichment for 108,981 pairs. The next merge and both cleaning stages succeeded on the copy. The subsequent idle pass skipped native rebuilding and cleaning. External publication and production rollout require separate operator verification. The separate timing sign-off was cancelled.

Follow the [production runbook](../../.claude/docs/migration-scripts.md#production-runbook). Build the stamped image, stop all writers, preview the pending migrations, review the counts, apply, and recreate both services with the same image. Leave the pending marker in place. Check the stamped commit, native merge results, cleaning, private exports and marker clearance after restart. Retain the journal and backup until publication is verified.
