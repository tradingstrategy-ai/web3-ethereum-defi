"""Backfill the missing early price history of the Neutral Trade Autopilot vaults.

Purpose: The scanner began collecting prices for both Accountable feeder vaults weeks after they were first seen, so their charts start late. Read old onchain state and fill only that gap.
Scope: Ethereum 0x909dadbca7955614a455d9e7447ad4adb4902c8e, blocks [25,846,919, 25,973,565); Monad 0xaabab7598be3c4fe58c593e73c2f5934b73b573e, blocks [107,330,102, 108,068,133). Raw hourly prices only.
Branch: neutral-trade-price-backfill
PR title: feat: add historical price backfills to numbered migrations
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1704
Preserves: Metadata, reader state, all other vaults, and both vaults' existing rows at or after their first collected price block.
Expected: Ethereum gains about 18 days of history from 2026-08-27; Monad gains up to 3 days from 2026-09-23 if the provider still holds that state, otherwise it is reported as monad_state_unavailable and preserved.
Preview evidence: a local MIGRATION_BACKFILL_BLOCKS=20000 preview on 2026-10-30 against the 2026-10-28 snapshot read both vaults at their first-seen blocks, replaced no rows and preserved all later rows. Run it soon: Monad state ages out of the provider's window.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input. Requires JSON_RPC_ETHEREUM (archive), JSON_RPC_MONAD, Hypersync credentials and the dense timestamp caches for chains 1 and 143.
For a quick local check, MIGRATION_BACKFILL_BLOCKS limits each range during preview only.
"""

from eth_typing import HexAddress

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.price_backfill import PriceBackfillTarget, run_price_backfill

#: Reviewed ranges frozen on 2026-10-30 from the 2026-10-28 production snapshot.
#: Each starts at the vault's first-seen block and ends, exclusively, at its first
#: collected price row on 2026-09-14 (Ethereum) and 2026-09-26 (Monad).
TARGETS = (
    PriceBackfillTarget(1, HexAddress("0x909dadbca7955614a455d9e7447ad4adb4902c8e"), 25_846_919, 25_973_565),
    PriceBackfillTarget(143, HexAddress("0xaabab7598be3c4fe58c593e73c2f5934b73b573e"), 107_330_102, 108_068_133),
)


def migrate(context: MigrationContext) -> None:
    """Backfill the reviewed gaps through the shared price backfill helper.

    Validate that both targets are still Accountable vaults before any RPC
    work. The helper stages, validates and publishes the raw price file; the
    runner owns preview, locking and the journal.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting the backfill outcome.
    :raises ValueError: If a target is missing or not an Accountable vault.
    """
    database = context.metadata()
    for target in TARGETS:
        row = database.rows.get(target.as_spec())
        if row is None or row.get("Protocol") != "Accountable":
            raise ValueError(f"Neutral Trade Autopilot backfill target {target.as_spec().as_string_id()} is missing or not an Accountable vault")
    run_price_backfill(context, TARGETS)
