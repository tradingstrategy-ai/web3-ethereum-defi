"""Populate reviewed native Neutral Trade curator, description and strategy fields.

Purpose: Replace missing native editorial metadata and unsafe name matching.
Scope: The thirteen exact Solana USDC accounts in NATIVE_VAULT_METADATA,
reviewed against official strategy pages on 2026-11-12. Existing rows only.
Branch: check-neutral-drade-api-docs
PR title: feat: collect Neutral Trade native vault history through the partner API
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1761
Preserves: Fees, risk overrides, visibility, EVM/Accountable records, source
history, prices, reader state and timestamp caches. No price history changes
are needed; migration 066 owns the initial fixed-scope history backfill.
Expected: Up to thirteen native rows gain curator declarations, descriptions,
provenance and reviewed strategy tags, including quarantined archival rows.

LLM-generated operational migration; changes require human review. Stop the
looped scanner before preview or apply. No API credentials or RPC are needed.
"""

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.neutral_trade.constants import SOLANA_DATASET_CHAIN_ID, make_solana_vault_address
from eth_defi.neutral_trade.vault_data_export import apply_reviewed_metadata
from eth_defi.neutral_trade.vault_metadata import NATIVE_VAULT_METADATA
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

# ruff: noqa: N999 - numbered operational migration


def migrate(context: MigrationContext) -> None:
    """Refresh reviewed public copy and tags through shared native resolvers.

    The runner owns preview, locking, backups and commits. Validate stored
    identities before supplementing their metadata; never create new rows or
    revive a hidden or quarantined price series.

    :param context: Guarded migration attempt and staged metadata database.
    :return: ``None`` after reporting and staging the metadata-only refresh.
    """
    database = context.metadata()
    inventory = {VaultSpec(SOLANA_DATASET_CHAIN_ID, make_solana_vault_address(address)): address for address in NATIVE_VAULT_METADATA}
    targets = {spec: database.rows[spec] for spec in inventory if spec in database.rows and database.rows[spec].get("Protocol") == "Neutral Trade"}
    for spec, row in targets.items():
        detection = row.get("_detection_data")
        assert isinstance(detection, ERC4262VaultDetection) and ERC4626Feature.neutral_trade_native in detection.features, f"Invalid native detection: {spec}"
        assert row.get("_neutral_trade", {}).get("vault_address") == inventory[spec], f"Invalid source identity: {spec}"
    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=targets))
    assert not skipped and all(update.new_tags for update in updates), "Reviewed native strategy tags could not be resolved"
    for spec, row in targets.items():
        assert apply_reviewed_metadata(row, inventory[spec])
    for update in updates:
        targets[update.spec]["_strategy_tags"] = set(update.new_tags)
    context.report(inspected_rows=len(targets), missing_specs=[spec.as_string_id() for spec in inventory if spec not in database.rows], legacy_tag_rows=invalid)
    context.commit_metadata()
