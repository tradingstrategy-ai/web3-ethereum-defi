"""Refresh Neutral Trade Autopilot copy, notes and strategy tags.

Purpose: Replace scraped and earlier Autopilot copy with reviewed descriptions explaining the cross-linked Accountable feeder vaults, and tag them multi-strategy.
Scope: Ethereum and Monad Neutral Trade Autopilot deployments; descriptions, notes, Link and strategy tags only. Curator attribution is resolved at export time.
Branch: fix/neutral-trade-vault-curator-descriptions
PR title: fix: attribute Neutral Trade Autopilot vaults and rewrite their descriptions
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1694
Preserves: Names, fees, flags, manager names, unrelated metadata, leads, prices, reader state and timestamp caches.
Expected: Up to two existing rows and ten changed fields (nine on the 2026-10-28 snapshot, where the Ethereum Link is already current); missing rows reported, never created. Repeated successful applies are skipped.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input. No network access is required.
"""

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.handwritten_metadata import ACCOUNTABLE_HANDWRITTEN_VAULT_NOTES, NEUTRAL_AUTOPILOT_VAULT_METADATA
from eth_defi.vault.strategy_tag import lookup_curator_strategy_tags
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed deployment inventory frozen on 2026-10-29.
TARGETS = (
    VaultSpec(1, "0x909dadbca7955614a455d9e7447ad4adb4902c8e"),
    VaultSpec(143, "0xaabab7598be3c4fe58c593e73c2f5934b73b573e"),
)


def migrate(context: MigrationContext) -> None:
    """Refresh the public copy and tags for the frozen Autopilot inventory.

    Validate every existing target before staging any changes. The same
    maintained constants supply the Accountable adapter, so future scans keep
    the repaired values; the runner owns preview, backups and persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting the changes and committing or previewing them.
    :raises ValueError: If an existing target is not classified as Accountable.
    """
    database = context.metadata()
    missing = [spec.as_string_id() for spec in TARGETS if spec not in database.rows]
    updates = {}
    for spec in TARGETS:
        row = database.rows.get(spec)
        if row is None:
            continue
        if row.get("Protocol") != "Accountable":
            raise ValueError(f"Neutral Trade Autopilot target {spec} has unexpected protocol {row.get('Protocol')!r}")
        key = (spec.chain_id, spec.vault_address)
        metadata = NEUTRAL_AUTOPILOT_VAULT_METADATA[key]
        fields = {
            "_description": metadata.description,
            "_short_description": metadata.short_description,
            "Link": metadata.link,
            "_notes": ACCOUNTABLE_HANDWRITTEN_VAULT_NOTES[key],
            "_strategy_tags": lookup_curator_strategy_tags(spec.chain_id, spec.vault_address),
        }
        updates[spec] = {field: value for field, value in fields.items() if row.get(field) != value}

    for spec, fields in updates.items():
        database.rows[spec].update(fields)
    context.report(inspected_rows=len(updates), missing_specs=missing, updated_fields=sum(len(fields) for fields in updates.values()))
    context.commit_metadata()
