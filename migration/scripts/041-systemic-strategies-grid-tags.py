"""Refresh Systemic Strategies L/S Grids strategy tags.

Purpose: Persist the requested high-frequency, high-win-rate classification
and its required algorithmic-trading parent using the maintained resolver's tags.
Scope: Only _strategy_tags for Hypercore 9999:0x07fd993f0fa3a185f7207adccd29f7a87404689d.
Branch: add-hft-high-win-rate-tags
PR title: notes: describe Systemic Strategies L/S Grids and classify its trading strategy
PR: Not opened.
Preserves: All other metadata, unrelated rows, prices, reader state and timestamp caches.
Expected: At most one existing row and one changed field; report a missing row.
No network reads or historical price backfill are required.

LLM-generated operational migration; changes require human review.
Run after merge using the production commands in .claude/docs/migration-scripts.md.
Stop the scanner and competing writers before both preview and apply. The
restarted scanner's normal exports and uploads publish the corrected tags.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Reviewed native Hyperliquid vault, using the synthetic Hypercore chain ID.
TARGET = VaultSpec(9999, "0x07fd993f0fa3a185f7207adccd29f7a87404689d")


def migrate(context: MigrationContext) -> None:
    """Refresh the reviewed vault through the maintained Hyperliquid resolver.

    Validate native protocol detection before staging the address-scoped update.
    The runner owns preview, backups and persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing or previewing the repair.
    :raises ValueError: If native detection is missing or tags cannot be resolved.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, changed_rows=0, missing_specs=[TARGET.as_string_id()])
        context.commit_metadata()
        return
    detection = row.get("_detection_data")
    if not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.hypercore_native not in detection.features:
        raise ValueError(f"Systemic Strategies target is not a native Hyperliquid vault: {TARGET}")
    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows={TARGET: row}))
    if skipped or any(update.new_tags is None for update in updates):
        raise ValueError(f"Cannot resolve Systemic Strategies strategy tags: {TARGET}")
    for update in updates:
        row["_strategy_tags"] = set(update.new_tags)
    context.report(inspected_rows=1, changed_rows=len(updates), updated_fields=len(updates), legacy_tag_rows=invalid)
    context.commit_metadata()
