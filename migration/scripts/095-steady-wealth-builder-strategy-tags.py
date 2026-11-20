"""Refresh Steady Wealth Builder's persisted strategy tags.

Purpose: Remove the grid trading tag from Steady Wealth Builder.
Scope: Only _strategy_tags for Lighter pool 281474976552443 on chain 9998.
Branch: remove-grid-trading-tag-steady-vault-builder
PR title: fix: remove grid trading tag from Steady Wealth Builder
PR: Pending.
Preserves: All other metadata, prices, reader state and timestamp caches.
Expected: At most one changed row and one updated field. Report a missing pool.

LLM-generated operational migration. Changes require human review.
Run after merge through vault-migrate with the scanner stopped.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Steady Wealth Builder's native Lighter pool.
TARGET = VaultSpec(9998, "lighter-pool-281474976552443")


def migrate(context: MigrationContext) -> None:
    """Refresh the target pool using the maintained Lighter tag registry.

    Validate the persisted protocol before resolving tags. A missing pool
    receives its current tags when the native exporter first collects it.

    :param context: Guarded migration attempt and shared metadata database.
    :return: None after reporting and committing or previewing the refresh.
    :raises ValueError: If the target lacks Lighter detection or resolvable tags.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, missing_specs=[TARGET.as_string_id()], updated_fields=0)
        context.commit_metadata()
        return

    detection = row.get("_detection_data")
    if row.get("Protocol") != "Lighter" or not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.lighter_native not in detection.features:
        raise ValueError(f"Target {TARGET.as_string_id()} is not classified as Lighter native")

    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows={TARGET: row}))
    if skipped or any(update.new_tags is None for update in updates):
        raise ValueError(f"Could not resolve strategy tags for {TARGET.as_string_id()}")

    for update in updates:
        row["_strategy_tags"] = set(update.new_tags)

    context.report(inspected_rows=1, missing_specs=[], updated_fields=len(updates), legacy_tag_rows=invalid)
    context.commit_metadata()
