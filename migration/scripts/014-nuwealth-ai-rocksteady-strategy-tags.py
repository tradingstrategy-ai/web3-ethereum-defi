"""Refresh Agent RockSteady's persisted strategy tags.

Purpose: Classify NuWealth.ai's reviewed fixed-rule strategy as algorithmic trading.
Scope: Only _strategy_tags for Agent RockSteady on Hypercore (9999). Curator attribution resolves at export time.
Branch: add-curator-nuwealth-ai-rocksteady
PR title: feat: add NuWealth.ai curator and attribute Agent RockSteady
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1701
Preserves: All other metadata, unrelated rows, leads, prices, reader state and timestamp caches.
Expected: At most one existing row and one changed field; report absent rows without creating them.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input. No network access is required.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Reviewed Agent RockSteady deployment, frozen on 2026-10-30.
TARGET = VaultSpec(9999, "0xd321ad79041bdad719ba5ff42fe950e1977df439")


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
        context.report(inspected_rows=0, missing_specs=[TARGET.as_string_id()], updated_fields=0)
        context.commit_metadata()
        return

    detection = row.get("_detection_data")
    if row.get("Protocol") != "Hyperliquid" or detection is None or ERC4626Feature.hypercore_native not in detection.features:
        raise ValueError(f"Agent RockSteady target {TARGET} is not classified as a Hyperliquid native vault")

    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows={TARGET: row}))
    if skipped:
        raise ValueError(f"Could not resolve Agent RockSteady strategy tags for {TARGET}")
    for update in updates:
        database.rows[update.spec]["_strategy_tags"] = None if update.new_tags is None else set(update.new_tags)
    context.report(inspected_rows=1, missing_specs=[], updated_fields=len(updates), legacy_tag_rows=invalid)
    context.commit_metadata()
