"""Reclassify the existing Nova pool without resetting its history.

Purpose: Reclassify the existing Nova pool without resetting its history.
Scope: Arbitrum 0x9ca80193712f372e2ff33a00127292884f6f8598 only.
Branch: improve-gtrade-vault-notes-curator
PR title: feat: separate Nova Markets vaults and refresh gTrade metadata
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1688
Preserves: Unrelated row fields, leads, historical prices, reader state and timestamp caches.
Expected: Report changed/missing entries; repeated successful applies are skipped.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input.
"""

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.nova import transform_nova


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed repair and report its committed or proposed fields.

    The context owns backup, persistence and preview staging for the batch.

    :param context: Guarded migration attempt and shared database.
    :return: ``None`` after validation and commit/preview.
    """
    transform_nova(context.metadata())
    context.commit_metadata()
