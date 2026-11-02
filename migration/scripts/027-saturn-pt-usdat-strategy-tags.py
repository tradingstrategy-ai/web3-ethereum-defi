"""Refresh Saturn PT-USDat's persisted strategy tags.

Purpose: Add lending looping, carry trade and AMM venue classifications.
Scope: Only _strategy_tags for Saturn's share token on Monad (143).
Branch: check-saturn-pt-vault-description-source
PR title: fix: explain and categorise Saturn PT-USDat strategy
PR: Pending; no pull request opened.
Preserves: All other metadata, unrelated rows, prices, reader state and timestamp caches.
Expected: At most one existing row and one changed field; report absent rows.
Run after merge using the production reminder in the Saturn research note.
No network reads or historical scanning are required.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Reviewed Saturn share-token deployment on Monad.
TARGET = VaultSpec(143, "0x1a5b77cef5e4b6f806aeac6f6ea9213a617dc7e3")


def migrate(context: MigrationContext) -> None:
    """Refresh only the reviewed vault through the maintained Accountable resolver.

    Restrict collection to the target row and validate its protocol evidence
    before staging changes. The runner owns preview, backups and persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing or previewing the repair.
    :raises ValueError: If the target is not classified as Accountable
        or its strategy tags cannot be resolved.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, missing_specs=[TARGET.as_string_id()], updated_fields=0)
        context.commit_metadata()
        return

    detection = row.get("_detection_data")
    if row.get("Protocol") != "Accountable" or not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.accountable_like not in detection.features:
        raise ValueError(f"Saturn PT-USDat target {TARGET} is not classified as Accountable")

    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows={TARGET: row}))
    if skipped:
        raise ValueError(f"Could not resolve Saturn PT-USDat strategy tags for {TARGET}")
    for update in updates:
        database.rows[update.spec]["_strategy_tags"] = None if update.new_tags is None else set(update.new_tags)
    context.report(inspected_rows=1, missing_specs=[], updated_fields=len(updates), legacy_tag_rows=invalid)
    context.commit_metadata()
