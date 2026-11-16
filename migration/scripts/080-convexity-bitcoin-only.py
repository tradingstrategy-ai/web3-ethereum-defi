"""Add Bitcoin-only trading to +convexity's persisted strategy tags.

Purpose: Apply the maintainer-assigned Bitcoin-only classification, retaining
positive convexity, algorithmic trading, directional trading and perpetual futures.
Scope: Only _strategy_tags for +convexity on Hypercore (9999).
Branch: fix/convexity-bitcoin-only
PR title: fix: add Bitcoin-only trading to +convexity
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1787
Preserves: Other metadata, unrelated rows, prices, leads, reader state and timestamp caches.
Expected: At most one existing row and changed field; report an absent row without creating it.
No network reads or historical price backfill are required for this tag-only refresh.

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

#: Reviewed +convexity native deployment, reconfirmed on 2026-11-16.
TARGET = VaultSpec(9999, "0x5661a070eb13c7c55ac3210b2447d4bea426cbf5")


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
    if row.get("Protocol") != "Hyperliquid" or not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.hypercore_native not in detection.features:
        raise ValueError(f"+convexity target {TARGET} is not classified as a Hyperliquid native vault")

    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows={TARGET: row}))
    if skipped or any(update.new_tags is None for update in updates):
        raise ValueError(f"Could not resolve +convexity strategy tags for {TARGET}")
    for update in updates:
        database.rows[update.spec]["_strategy_tags"] = set(update.new_tags)
    context.report(inspected_rows=1, missing_specs=[], updated_fields=len(updates), legacy_tag_rows=invalid)
    context.commit_metadata()
