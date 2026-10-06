# Hypercore corruption investigation

[Issue #1628](https://github.com/tradingstrategy-ai/web3-ethereum-defi/issues/1628)
combined two problems: rescanning downsampled API history lost dense price points,
and attaching current deposit flags to historical prices overwrote past state.
The price-row write time was also mistaken for a permission observation clock.

The provider's present portfolio cannot recreate every lost historical point or
permission transition. Retained scanner backups and raw private R2 prices can
restore missing keys; cleaned/resampled files cannot restore exact scanner rows.
Conflicting alternatives must remain auditable rather than overwrite the current
target's economic values silently.

The operator's recovery policy uses a selected backup's original **price timestamp**
when no separate permission clock exists, labelled `legacy_price_timestamp`.
These flags are usable approximations under that policy, not authenticated API
receipts. Known-corrupted target flags provide no truth. Genuine Unknown responses
supersede inferred flags; unrecoverable status remains Unknown; capacity stays
uncertified. Raw-only flags can already have been carried forwards.

The corrected recovery, source counts and limitations are consolidated in
[the implementation results](hypercore-permission-recovery-1628-implementation.md).
Use the [operator runbook](../README-hypercore-permission-recovery.md) for production
maintenance and the [DOEZOE daily table](doezoe-deposit-status-2026-recovered.md) for
clock-by-clock backtest evidence. Superseded investigation drafts are archived
outside Git with the immutable source files.
