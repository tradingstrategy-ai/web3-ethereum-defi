"""Refresh persisted long-short portfolio tags for nine Hyperliquid vaults.

Purpose: Publish the reviewed long_short_portfolio classification.
Scope: Only _strategy_tags for the nine listed Hypercore (9999) deployments.
Branch: check-hyperliquid-long-short-vault-equity-curves
PR title: feat: classify more Hyperliquid long-short portfolios
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1794
Preserves: Other metadata, unrelated rows, prices, leads, reader state and timestamp caches.
Expected: Up to nine updated fields. Report absent deployments without creating rows.
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
    VaultSpec(HYPERCORE_CHAIN_ID, "0x060d01aa996003b3731a992462d7f0ba68bf3b04"),
    VaultSpec(HYPERCORE_CHAIN_ID, "0x07fd993f0fa3a185f7207adccd29f7a87404689d"),
    VaultSpec(HYPERCORE_CHAIN_ID, "0x115849ce84370f25cadcf0d348510d73837e1aa5"),
    VaultSpec(HYPERCORE_CHAIN_ID, "0x14ecb3f4932522b8d508fa6e237e2746a4999feb"),
    VaultSpec(HYPERCORE_CHAIN_ID, "0x35d92bd6571d0bdc5413250b28692e6be905d67b"),
    VaultSpec(HYPERCORE_CHAIN_ID, "0x9114a5161f18739ced233190861ea275c8b3a99b"),
    VaultSpec(HYPERCORE_CHAIN_ID, "0x9e02aca9865e1859bb7865f6f64801e804a173df"),
    VaultSpec(HYPERCORE_CHAIN_ID, "0xac26cf5f3c46b5e102048c65b977d2551b72a9c7"),
    VaultSpec(HYPERCORE_CHAIN_ID, "0xce71894b42f5c31a6ae6ca4fb77441c68ab15086"),
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
            raise ValueError(f"Target {spec.as_string_id()} is not classified as a Hyperliquid native vault")

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
