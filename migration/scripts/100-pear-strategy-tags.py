# ruff: noqa: N999 - Numbered filenames are required by the migration runner.
"""Refresh the strategy classifications of the four reviewed Pear vaults.

Purpose: Publish the reviewed strategy categories for existing Pear rows.
Scope: Only _strategy_tags on the four listed HyperEVM deployments.
Branch: examine-pear-garden-vault-protocol
PR title: feat: add Pear vault support on HyperEVM
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1799
Preserves: Other metadata, historical prices, leads and reader state.
Expected: Update changed classifications and report absent catalogue rows.

LLM-generated operational migration. Run after merge with the scanner stopped.
This refresh needs no network calls or price backfill.
"""

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.pear.constants import PEAR_CHAIN_ID
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Freeze the addresses reviewed for this migration.
TARGETS = (
    VaultSpec(PEAR_CHAIN_ID, "0x65c9f4665c7d9ce96f53aafc67796dcad46f4096"),
    VaultSpec(PEAR_CHAIN_ID, "0xd38a67a4f54a9b4286d72f08440485a395ddfa70"),
    VaultSpec(PEAR_CHAIN_ID, "0xdf6917ee9a2d9b34075bb099a0a1e955bcfcdf84"),
    VaultSpec(PEAR_CHAIN_ID, "0x69f2ed780a4cec49e552dbcd35a60f41368bc362"),
)


def migrate(context: MigrationContext) -> None:
    """Stage the maintained tags for the reviewed Pear deployments.

    Validate the scanner identity of every present row before replacing any
    tags. Missing rows are reported so normal discovery can populate them.

    :param context: Numbered migration context and staged metadata owner.
    :return: None after staging and reporting the refresh.
    :raises ValueError: If a present target fails identity or tag resolution.
    """
    database = context.metadata()
    rows = {spec: database.rows[spec] for spec in TARGETS if spec in database.rows}
    for spec, row in rows.items():
        detection = row.get("_detection_data")
        if row.get("Protocol") != "Pear" or not isinstance(detection, ERC4262VaultDetection) or detection.get_spec() != spec or ERC4626Feature.pear_like not in detection.features:
            raise ValueError(f"Target {spec.as_string_id()} is not classified as Pear")

    updates, skipped, legacy_rows = collect_strategy_tag_updates(VaultDatabase(rows=rows))
    if skipped or any(update.new_tags is None for update in updates):
        message = "Could not resolve strategy tags for all reviewed Pear vaults"
        raise ValueError(message)

    for update in updates:
        rows[update.spec]["_strategy_tags"] = set(update.new_tags)

    context.report(
        inspected_rows=len(rows),
        updated_fields=len(updates),
        legacy_tag_rows=legacy_rows,
        missing_specs=[spec.as_string_id() for spec in TARGETS if spec not in rows],
    )
    context.commit_metadata()
