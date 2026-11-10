"""Backfill the daily price history of the reviewed Wildcat markets.

Purpose: The scanner starts newly added markets at the Ethereum price scan
cursor, so their history before it is missing. Read old onchain state and
fill only that gap.
Scope: Ethereum daily prices of the version 2 markets seeded by
``063-wildcat-market-leads.py``, from each market's ``MarketAdded`` block to
the Ethereum price cursor plus one, exclusive; markets registered after the
cursor are left to ordinary scanning. Rows go to the raw hourly price file,
sampled every 7,200 blocks (one day), as the live scanner reads Wildcat
markets at most daily.
Branch: index-wildcat-markets-as-vaults
PR title: feat: index Wildcat lending markets as read-only vaults
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1758
Preserves: Metadata, reader state, all other vaults and any rows at or after
the cursor.
Expected: Up to 82 markets gain history from their registry blocks
(February 2025 onwards); a preview reports the per-market ranges and rows.
Range end: unlike frozen backfills, these markets have no collected price row
to end at. The end is read from the stored reader state, the exact block at
which ordinary scanning continues, so the two never overlap.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input. Requires
``JSON_RPC_ETHEREUM`` (archive), Hypersync credentials and the dense Ethereum
timestamp cache. For a quick local check, MIGRATION_BACKFILL_BLOCKS limits
each range during preview only; see ``scripts/erc-4626/preview-wildcat-migrations.py``.
"""

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs import wildcat


def migrate(context: MigrationContext) -> None:
    """Backfill the reviewed markets through the shared price backfill helper.

    The helper stages, validates and publishes the raw price file; the runner
    owns preview, locking and the journal.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting the backfill outcome.
    """
    wildcat.backfill_market_prices(context)
