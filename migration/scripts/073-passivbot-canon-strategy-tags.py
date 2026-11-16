"""Refresh Passivbot Canon's persisted strategy tags.

Purpose: Remove the grid trading classification at the maintainer's request.
Scope: Only _strategy_tags for Passivbot Canon on Hypercore (9999).
Branch: remove-grid-trading-tag-passivbot-canon
PR title: fix: remove grid trading tag from Passivbot Canon
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1776
Preserves: All other metadata, unrelated rows, prices, reader state and timestamp caches.
Expected: At most one existing row and one changed field; report absent rows.
Run after merge using the production reminder in the Passivbot research note.
No network reads or historical scanning are required.

LLM-generated operational migration; changes require human review.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Reviewed Passivbot Canon native vault on Hypercore.
TARGET = VaultSpec(9999, "0x490af7d4a048a81db0f677517ed6373565b42349")


def migrate(context: MigrationContext) -> None:
    """Refresh only the reviewed vault through the maintained Hyperliquid resolver.

    Restrict collection to the target row and validate its protocol evidence
    before staging changes. The runner owns preview, backups and persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing or previewing the repair.
    :raises ValueError: If the target is not classified as Hyperliquid
        or its strategy tags cannot be resolved.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, missing_specs=[TARGET.as_string_id()], updated_fields=0)
        context.commit_metadata()
        return

    detection = row.get("_detection_data")
    if row.get("Protocol") != "Hyperliquid" or not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.hypercore_native not in detection.features:
        raise ValueError(f"Passivbot Canon target {TARGET} is not classified as Hyperliquid")

    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows={TARGET: row}))
    if skipped:
        raise ValueError(f"Could not resolve Passivbot Canon strategy tags for {TARGET}")
    for update in updates:
        database.rows[update.spec]["_strategy_tags"] = None if update.new_tags is None else set(update.new_tags)
    context.report(inspected_rows=1, missing_specs=[], updated_fields=len(updates), legacy_tag_rows=invalid)
    context.commit_metadata()
