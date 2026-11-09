"""Refresh Titan Vault's persisted strategy tags.

Purpose: Apply the maintainer's high-win-rate classification and its required algorithmic tag.
Scope: Only _strategy_tags for Titan Vault on Hypercore (9999).
Branch: categorise-vault-strategy-high-win-rate
PR title: fix: classify Titan Vault as high-win-rate trading
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1748
Preserves: Other metadata, unrelated rows, prices, leads, reader state and timestamp caches.
Expected: At most one existing row and changed field; report an absent row without creating it.
No network reads or historical price backfill are required.

LLM-generated operational migration; changes require human review.
Run after merge using .claude/docs/migration-scripts.md. Stop the scanner and
competing writers before preview and apply; restart the same inspected image
for normal cleaning, exports and uploads to publish the corrected tags.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Reviewed Titan Vault deployment, frozen on 2026-11-09.
TARGET = VaultSpec(9999, "0x4b0eab9444a75a03f1ef340c8beac737afa5ab09")


def migrate(context: MigrationContext) -> None:
    """Refresh only the reviewed vault through the maintained native resolver.

    Restrict collection to the target row and validate its protocol evidence
    before staging changes. The runner owns preview, backups and persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing or previewing the repair.
    :raises ValueError: If the target is not classified as a Hyperliquid vault
        or its strategy tags cannot be resolved.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, missing_specs=[TARGET.as_string_id()], changed_rows=0, updated_fields=0)
        context.commit_metadata()
        return

    detection = row.get("_detection_data")
    if row.get("Protocol") != "Hyperliquid" or not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.hypercore_native not in detection.features:
        raise ValueError(f"Titan Vault target {TARGET} is not classified as a Hyperliquid native vault")

    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows={TARGET: row}))
    if skipped or any(update.new_tags is None for update in updates):
        raise ValueError(f"Could not resolve Titan Vault strategy tags for {TARGET}")
    for update in updates:
        database.rows[update.spec]["_strategy_tags"] = set(update.new_tags)
    context.report(inspected_rows=1, missing_specs=[], changed_rows=len(updates), updated_fields=len(updates), legacy_tag_rows=invalid)
    context.commit_metadata()
