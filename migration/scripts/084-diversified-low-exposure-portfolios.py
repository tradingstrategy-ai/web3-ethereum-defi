"""Refresh persisted diversified low-exposure long-short tags for two Hyperliquid vaults.

Purpose: Publish the diversified low-exposure long-short portfolio classification.
Scope: Only _strategy_tags for the two listed Hypercore (9999) deployments.
Branch: check-hyperliquid-long-short-vault-equity-curves
PR title: feat: classify more Hyperliquid long-short portfolios
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1794
Preserves: Other metadata, unrelated rows, prices, leads, reader state and timestamp caches.
Expected: Up to two updated fields. Report absent deployments without creating rows.
The tag refresh requires no network calls or historical price backfill.

LLM-generated operational migration; changes require human review.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Reviewed deployments, frozen independently of future tag registry changes.
TARGETS = (
    VaultSpec(HYPERCORE_CHAIN_ID, "0x8bb033130be354eed1110ce7228d7095f001d9fe"),  # HyperNeutral #1
    VaultSpec(HYPERCORE_CHAIN_ID, "0x14ecb3f4932522b8d508fa6e237e2746a4999feb"),  # Quantitative Market Neutral Strategy
)


def migrate(context: MigrationContext) -> None:
    """Refresh the reviewed vaults through the maintained native resolver.

    Validate the protocol evidence for every present target before staging tag
    changes. The runner manages preview, backups and atomic persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing or previewing the refresh.
    :raises ValueError: If a target lacks Hyperliquid identity or resolvable tags.
    """
    database = context.metadata()
    target_rows = {spec: database.rows[spec] for spec in TARGETS if spec in database.rows}
    for spec, row in target_rows.items():
        detection = row.get("_detection_data")
        if row.get("Protocol") != "Hyperliquid" or not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.hypercore_native not in detection.features:
            raise ValueError(f"Target {spec.as_string_id()} is not classified as Hyperliquid native")

    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=target_rows))
    if skipped or any(update.new_tags is None for update in updates):
        message = "Could not resolve strategy tags for all reviewed Hyperliquid vaults"
        raise ValueError(message)

    for update in updates:
        database.rows[update.spec]["_strategy_tags"] = set(update.new_tags)

    context.report(
        inspected_rows=len(target_rows),
        missing_specs=[spec.as_string_id() for spec in TARGETS if spec not in target_rows],
        updated_fields=len(updates),
        legacy_tag_rows=invalid,
    )
    context.commit_metadata()
