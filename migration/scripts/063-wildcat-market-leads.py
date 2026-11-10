"""Seed leads and metadata rows for the reviewed Wildcat version 2 markets.

Purpose: The incremental lead scan resumes from the stored Ethereum end block,
so it never sees the ``MarketAdded`` registry events of existing markets. Seed
one direct Wildcat lead and one scanner metadata row per market, so the next
migration can backfill prices and the scanner keeps them current.
Scope: Ethereum leads and rows for the 82 version 2 markets registered in the
WildcatArchController up to block 26,386,736; see
``eth_defi.vault_protocols.repairs.wildcat``.
Branch: index-wildcat-markets-as-vaults
PR title: feat: index Wildcat lending markets as read-only vaults
PR: to be added when the pull request is opened
Preserves: Existing metadata rows, other leads, the lead cursor, prices,
reader state and timestamp caches.
Expected: 82 inserted leads and rows on the first apply. Fails without
changes if the registry at the stored lead cursor has an unreviewed market.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input. Needs
``JSON_RPC_ETHEREUM`` (archive) for the registry check and metadata rows.
"""

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs import wildcat


def migrate(context: MigrationContext) -> None:
    """Insert the reviewed Wildcat leads and rows after checking registry coverage.

    The context owns preview staging, backups and atomic persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing the staged leads and rows.
    """
    wildcat.seed_market_leads(context)
