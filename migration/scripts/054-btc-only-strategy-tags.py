"""Refresh strategy tags for Bunny and BitRocket.

Purpose: Persist the maintainer-requested btc_only tag alongside existing strategy tags.
Scope: Only _strategy_tags for the two fixed identities below.
Branch: categorise-hyperliquid-vault-high-win-rate
PR title: feat: add Bitcoin-only strategy category for Bunny and BitRocket
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1751
Preserves: Other metadata, unrelated rows, prices, reader state and timestamp caches.
Expected: Inspect at most two existing rows; report missing rows. After 053,
Bunny is already current and only BitRocket changes (at most one field).
When previewed against an older isolated snapshot, both rows may change.
Evidence: Maintainer classification; live vault identities checked via Hyperliquid vaultDetails on 2026-11-09.

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

#: Reviewed native vault identities; only their strategy tags are refreshed.
TARGETS = (
    VaultSpec(9999, "0x5ce0ce4387679de0335a3e8a1c9a7abb4f150a71"),
    VaultSpec(9999, "0xa415c4ded17cac76a1d81a54d9f4aeb48ffd4dd0"),
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
        if row.get("Protocol") != "Hyperliquid" or not isinstance(detection, ERC4262VaultDetection) or (ERC4626Feature.hypercore_native not in detection.features):
            raise ValueError(f"Bitcoin-only target is not a native Hyperliquid vault: {spec}")
    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=rows))
    if skipped or any(update.new_tags is None for update in updates):
        message = "Could not resolve all reviewed Bitcoin-only strategy tags"
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
