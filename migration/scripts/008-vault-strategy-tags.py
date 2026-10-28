"""Refresh persisted strategy tags through the shared current resolvers.

Purpose: Refresh persisted strategy tags through the shared current resolvers.
Scope: All existing rows with a supported resolver; unresolved rows retain their tags. Nova reclassification runs first.
Branch: feat/centaur-social-sentiment-tags
PR title: feat: add sentiment and social trading strategy tags for Centaur vaults
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1675
Branch: tag-grvt-high-frequency-vaults
PR title: feat: classify KangCFA and reviewed GRVT high-frequency strategies
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1683
Branch: improve-gtrade-vault-notes-curator
PR title: feat: separate Nova Markets vaults and refresh gTrade metadata
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1688
Preserves: Unrelated row fields, leads, historical prices, reader state and timestamp caches.
Expected: Report changed/missing entries; repeated successful applies are skipped.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input.
"""

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed repair and report its committed or proposed fields.

    The context owns backup, persistence and preview staging for the batch.

    :param context: Guarded migration attempt and shared database.
    :return: ``None`` after validation and commit/preview.
    """
    database = context.metadata()
    updates, skipped, invalid = collect_strategy_tag_updates(database)
    for update in updates:
        database.rows[update.spec]["_strategy_tags"] = None if update.new_tags is None else set(update.new_tags)
    context.report(inspected_rows=len(database.rows), skipped_rows=skipped, legacy_tag_rows=invalid)
    context.commit_metadata()
