"""Refresh the persisted strategy tags for Emporium Ventures SPC.

Purpose: Remove the index tag from the active long-short strategy.
Scope: Only _strategy_tags for Emporium Ventures SPC on HyperEVM.
Branch: fix/pear-vault-display-names
PR title: fix: shorten Pear vault display names
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1803
Preserves: Other metadata, prices, leads, reader state and timestamp caches.
Expected: At most one changed row and one changed field. Report a missing vault.

LLM-generated operational migration. Changes require human review.
Run after merge through vault-migrate with the scanner stopped.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.pear.constants import PEAR_CHAIN_ID
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Emporium Ventures SPC on HyperEVM.
TARGET = VaultSpec(PEAR_CHAIN_ID, "0xd38a67a4f54a9b4286d72f08440485a395ddfa70")


def migrate(context: MigrationContext) -> None:
    """Refresh the target vault using the maintained Pear strategy tags.

    Validate the saved vault identity before staging the change. A missing
    vault will receive its current tags when the scanner discovers it.

    :param context: Guarded migration attempt and staged metadata database.
    :return: None after staging the preview or committing the refresh.
    :raises ValueError: If the target lacks Pear identity or resolvable tags.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, updated_fields=0, missing_specs=[TARGET.as_string_id()])
        context.commit_metadata()
        return

    detection = row.get("_detection_data")
    if row.get("Protocol") != "Pear" or not isinstance(detection, ERC4262VaultDetection) or detection.get_spec() != TARGET or ERC4626Feature.pear_like not in detection.features:
        raise ValueError(f"Target {TARGET.as_string_id()} is not classified as Pear")

    updates, skipped, legacy_rows = collect_strategy_tag_updates(VaultDatabase(rows={TARGET: row}))
    if skipped or any(update.new_tags is None for update in updates):
        raise ValueError(f"Could not resolve strategy tags for {TARGET.as_string_id()}")

    for update in updates:
        row["_strategy_tags"] = set(update.new_tags)

    context.report(inspected_rows=1, updated_fields=len(updates), legacy_tag_rows=legacy_rows, missing_specs=[])
    context.commit_metadata()
