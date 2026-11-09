"""Refresh Vectis UltraX Arb's persisted strategy tags.

Purpose: Classify the documented automated, hedged perpetual arbitrage strategy.
Scope: Only _strategy_tags for Ethereum share vault
0x05ed3da1deeaaa57c8e09986e95ac271572dcf89.
Branch: add-curator-vectis-ultrax-arb
PR title: feat: add Vectis Finance curator and classify UltraX Arb
PR: Not opened during authoring.
Preserves: All other metadata, unrelated rows, prices, reader state and timestamp caches.
Expected: At most one existing row and one changed field; report absent rows.

LLM-generated operational migration; changes require human review.

Curator attribution is resolved during export and needs no stored-value repair.
Run through vault-migrate after deployment, with scanner and competing writers
stopped for both preview and apply; see .claude/docs/migration-scripts.md.
This migration performs no network reads or historical price scans.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Reviewed Vectis UltraX Arb share-token deployment on Ethereum.
TARGET = VaultSpec(1, "0x05ed3da1deeaaa57c8e09986e95ac271572dcf89")


def migrate(context: MigrationContext) -> None:
    """Refresh the reviewed row through the maintained Accountable resolver.

    Validate protocol evidence and limit collection to this vault. The runner
    owns preview, backups and persistence; no adapter or RPC client is created.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and staging the repair.
    :raises ValueError: If protocol evidence conflicts or tags cannot be resolved.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, changed_rows=0, updated_fields=0, missing_specs=[TARGET.as_string_id()])
        context.commit_metadata()
        return

    detection = row.get("_detection_data")
    if row.get("Protocol") != "Accountable" or not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.accountable_like not in detection.features:
        raise ValueError(f"Vectis UltraX Arb target {TARGET} is not classified as Accountable")

    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows={TARGET: row}))
    if skipped or any(update.new_tags is None for update in updates):
        raise ValueError(f"Could not resolve Vectis UltraX Arb strategy tags for {TARGET}")
    for update in updates:
        row["_strategy_tags"] = set(update.new_tags)
    context.report(inspected_rows=1, changed_rows=len(updates), updated_fields=len(updates), legacy_tag_rows=invalid)
    context.commit_metadata()
