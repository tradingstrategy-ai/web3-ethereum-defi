"""Refresh HyperNeutral #1's persisted long-short portfolio classification.

Purpose: Add the maintainer-requested long_short_portfolio strategy tag.
Scope: Only _strategy_tags for HyperNeutral #1 on Hypercore (9999).
Branch: check-strategy-tag-long-short-portfolio
PR title: feat: add long-short portfolio tag and HyperNeutral curator
PR: Not opened.
Preserves: Other metadata, unrelated rows, prices, reader state and timestamp caches.
Expected: At most one updated row and field; report an absent target.
No historical backfill is needed: this changes strategy metadata only.

LLM-generated operational migration; changes require human review.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Reviewed HyperNeutral #1 native vault.
TARGET = VaultSpec(HYPERCORE_CHAIN_ID, "0x8bb033130be354eed1110ce7228d7095f001d9fe")


def migrate(context: MigrationContext) -> None:
    """Refresh the reviewed vault through the maintained Hyperliquid resolver.

    Validate the native protocol identity before staging the strategy field.
    The runner owns preview, backups and persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after committing or previewing the repair.
    :raises ValueError: If the target is not Hyperliquid or cannot be resolved.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, missing_specs=[TARGET.as_string_id()], updated_fields=0)
        context.commit_metadata()
        return

    detection = row.get("_detection_data")
    if row.get("Protocol") != "Hyperliquid" or not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.hypercore_native not in detection.features:
        raise ValueError(f"HyperNeutral target {TARGET} is not classified as Hyperliquid")

    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows={TARGET: row}))
    if skipped:
        raise ValueError(f"Could not resolve HyperNeutral strategy tags for {TARGET}")
    for update in updates:
        database.rows[update.spec]["_strategy_tags"] = None if update.new_tags is None else set(update.new_tags)
    context.report(inspected_rows=1, missing_specs=[], updated_fields=len(updates), legacy_tag_rows=invalid)
    context.commit_metadata()
