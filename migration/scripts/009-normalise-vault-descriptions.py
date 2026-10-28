"""Normalise persisted descriptions and shorten listing summaries.

Purpose: Normalise persisted descriptions and shorten listing summaries.
Scope: All existing description fields; runs after the protocol copy repairs.
Branch: fix-vault-description-newline
PR title: fix: normalise vault descriptions and shorten listing summaries
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1681
Branch: explain-llama-lend-wsteth-usdc-2-performance-bump
PR title: fix: correct Llama Lend liquidity and utilisation
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1682
Preserves: Unrelated row fields, leads, historical prices, reader state and timestamp caches.
Expected: Report changed/missing entries; repeated successful applies are skipped.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input.
"""

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.descriptions import repair_vault_descriptions


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed repair and report its committed or proposed fields.

    The context owns backup, persistence and preview staging for the batch.

    :param context: Guarded migration attempt and shared database.
    :return: ``None`` after validation and commit/preview.
    """
    repair_vault_descriptions(context.metadata(), dry_run=False)
    context.commit_metadata()
