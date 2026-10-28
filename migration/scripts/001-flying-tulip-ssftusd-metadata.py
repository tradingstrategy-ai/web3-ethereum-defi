"""Refresh the reviewed Flying Tulip ssftUSD metadata.

Purpose: Refresh the reviewed Flying Tulip ssftUSD metadata.
Scope: Ethereum 0x232128844e5af56fd4b3f8bf97e08ab477282ab5 only.
Branch: investigate-flying-tulip-vault-protocol
PR title: feat: integrate Flying Tulip ssftUSD vault
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1679
Preserves: Unrelated row fields, leads, historical prices, reader state and timestamp caches.
Expected: Report changed/missing entries; repeated successful applies are skipped.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input.
"""

from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.flying_tulip import migrate_flying_tulip_metadata


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed repair and report its committed or proposed fields.

    The context owns backup, persistence and preview staging for the batch.

    :param context: Guarded migration attempt and shared database.
    :return: ``None`` after validation and commit/preview.
    """
    result = migrate_flying_tulip_metadata(context.metadata(), dry_run=False, target=VaultSpec(1, "0x232128844e5af56fd4b3f8bf97e08ab477282ab5"))
    context.report(inspected_rows=result.inspected_rows)
    context.commit_metadata()
