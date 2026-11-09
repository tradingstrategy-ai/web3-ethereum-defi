"""Refresh strategy tags for FuturAI Labs' three reviewed Hypercore vaults.

Purpose: Persist the maintained address-specific strategy classifications.
Scope: Only _strategy_tags for the three fixed identities below.
Branch: add-curator-futurailabs-hyperliquid
PR title: feat: add FuturAI Labs Hyperliquid curator
PR: Not opened.
Preserves: Other metadata, unrelated rows, prices, reader state and timestamp caches.
Expected: At most three existing rows and changed fields; report missing rows.
Evidence: docs/protocol-research/futurai-labs-curator.md.

Curator ownership resolves at export time and needs no stored-value repair.
No network reads or price backfill are performed by this migration.

LLM-generated operational migration; changes require human review.
Run through vault-migrate after deployment; stop the scanner and competing
writers before preview and apply. See .claude/docs/migration-scripts.md.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Complete reviewed native vault inventory for the shared leader.
TARGETS = (
    VaultSpec(9999, "0xb65dd7c56afbf3b272ab5fc49be44b47dca18003"),
    VaultSpec(9999, "0x53375ca9a649f337d83d0834fec0db97640a2a06"),
    VaultSpec(9999, "0xbefed76a71682b1dc48cca5a10f397cbc492a216"),
)


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed classifications using the scanner's shared resolver.

    Validate protocol detection and all proposals before staging any changes.
    The runner owns locks, previews, backups and persistence.

    :param context: Guarded migration attempt and staged metadata database.
    :return: ``None`` after reporting and committing or previewing the changes.
    :raises ValueError: If a target has conflicting detection or cannot resolve.
    """
    database = context.metadata()
    rows = {spec: database.rows[spec] for spec in TARGETS if spec in database.rows}
    for spec, row in rows.items():
        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection) or (ERC4626Feature.hypercore_native not in detection.features):
            raise ValueError(f"FuturAI Labs target is not a native Hyperliquid vault: {spec}")
    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=rows))
    if skipped or any(update.new_tags is None for update in updates):
        message = "Could not resolve all reviewed FuturAI Labs strategy tags"
        raise ValueError(message)
    for update in updates:
        rows[update.spec]["_strategy_tags"] = set(update.new_tags)
    context.report(
        inspected_rows=len(rows),
        changed_rows=len(updates),
        updated_fields=len(updates),
        legacy_tag_rows=invalid,
        missing_specs=[spec.as_string_id() for spec in TARGETS if spec not in rows],
    )
    context.commit_metadata()
