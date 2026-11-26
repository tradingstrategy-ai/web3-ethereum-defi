# ruff: noqa: N999 - Numbered filenames are required by the migration runner.
"""Add the Looping classification to six reviewed vaults.

Purpose: Publish the documented looping strategy for IPOR Fusion, Upshift and Ember vaults.
Scope: Only _strategy_tags on the six Ethereum and Monad deployments listed below.
Branch: tag-confirmed-looping-vaults
PR title: fix: tag six confirmed looping vaults
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pulls?q=is%3Apr+head%3Atag-confirmed-looping-vaults
Preserves: Existing classifications, unrelated metadata, prices, leads and reader state.
Expected: Up to six changed rows and fields. Report missing deployments.

LLM-generated operational migration. Run after merge with the scanner stopped.
This metadata-only refresh requires no network calls or price backfill.
"""

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Freeze the reviewed chain, vault address, protocol and scanner feature.
TARGETS = {
    VaultSpec(1, "0xf373a4d4d200d709aa73d4e0be49b822b0cc866d"): ("IPOR Fusion", ERC4626Feature.ipor_like),
    VaultSpec(1, "0xf8f226da66244f89e70c5b5d1a5c5b0d505eb1d8"): ("IPOR Fusion", ERC4626Feature.ipor_like),
    VaultSpec(143, "0x42b3b62a6685e3a3f93483715ef4300f5e7336cc"): ("Upshift", ERC4626Feature.upshift_like),
    VaultSpec(1, "0xd000e6bcad5457e8f4de67eddefe50bcc4b3d743"): ("Upshift", ERC4626Feature.upshift_like),
    VaultSpec(1, "0xda89af5bf2eb0b225d787abfa9095610f2e79e7d"): ("Upshift", ERC4626Feature.upshift_like),
    VaultSpec(1, "0x373152feef81cc59502da2c8de877b3d5ae2e342"): ("Ember", ERC4626Feature.ember_like),
}


def migrate(context: MigrationContext) -> None:
    """Stage the looping classification using the maintained protocol tags.

    Validate the scanner identity of each present row before making changes.
    Missing rows receive their classifications through normal discovery.

    :param context: Migration attempt that owns preview and persistence.
    :return: None after staging and reporting the metadata changes.
    :raises ValueError: If a present row has conflicting detection or cannot be classified.
    """
    database = context.metadata()
    rows = {spec: database.rows[spec] for spec in TARGETS if spec in database.rows}
    for spec, row in rows.items():
        protocol, feature = TARGETS[spec]
        detection = row.get("_detection_data")
        if row.get("Protocol") != protocol or not isinstance(detection, ERC4262VaultDetection) or detection.get_spec() != spec or feature not in detection.features:
            raise ValueError(f"Target {spec.as_string_id()} is not classified as {protocol}")

    updates, skipped, legacy_rows = collect_strategy_tag_updates(VaultDatabase(rows=rows))
    if skipped or any(StrategyTag.lending_looping not in (update.new_tags or ()) for update in updates):
        message = "Could not resolve the looping tag for all reviewed vaults"
        raise ValueError(message)

    changed = 0
    for update in updates:
        # An earlier manual classification may still be present in the database.
        if StrategyTag.lending_looping not in (update.old_tags or ()):
            rows[update.spec]["_strategy_tags"] = set(update.old_tags or ()) | {StrategyTag.lending_looping}
            changed += 1

    context.report(
        inspected_rows=len(rows),
        updated_fields=changed,
        legacy_tag_rows=legacy_rows,
        missing_specs=[spec.as_string_id() for spec in TARGETS if spec not in rows],
    )
    context.commit_metadata()
