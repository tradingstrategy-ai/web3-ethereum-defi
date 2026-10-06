# Implementation review record

Grounded Claude CLI `claude-opus-5-5` reviews were completed on 2026-10-06 in
`chatty-flamingo`, branch `fix/hyperliquid-data-1628`. Each accepted review inspected
the correct non-empty worktree, used read-only tools and returned a successful
final result. Original streams and superseded drafts are retained outside Git
under the operator's recovery evidence directory.

Review fixes included independent coherent permission capture, original-clock
freshness, explicit Unknown handling, constrained-schema refusal before collector
initialisation, source auditing, own-backup safety, isolated backup failures,
remote namespaces and maintenance reservations, and managed multipart copying.
Production-Parquet rehearsal also found the old standalone Pandas merger changed
EVM NaNs to nulls; both standalone paths now delegate to the Arrow-native merger.

The operator's final policy uses `legacy_price_timestamp` for recoverable backup
flags. Genuine responses, including Unknown, win; inferred flags expire against
the original clock and provide no fresh capacity. Grounded follow-up reviews
covered clock units, scanner/raw source priority, canonical retraction and
idempotence. The final DOEZOE result is **Open through 21 September and Closed
from 22 September at UTC midnight**. Earlier receipt-only conclusions are
superseded and have been removed from the published documentation.

The [local results](../../docs/protocol-research/hypercore-permission-recovery-1628-implementation.md)
record the backed-up applies and preserved production-scale data. Production
migration and consumer rollout remain pending. The final grounded cleanup review found sparse in-batch value loss and backup
registry errors preceding price publication. Both were fixed, with real DuckDB
rollback, sparse duplicate, capacity/provenance, filtered bulk closure and invalid
registry tests. Unchanged bulk denials are suppressed when the latest genuine
snapshot is already closed, without refreshing its first clock. Client credential
precedence has unit and real-provider coverage. Own-backup receipts now fsync the
parent directory. The follow-up Opus 5.5 review returned **no blocking findings**
and confirmed AGENTS.md conformance; its remaining closure deduplication note
was fixed and covered for both bulk and vaultDetails sources.
