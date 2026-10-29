"""Restore the Flying Tulip strategy explanations and refresh public copy.

Purpose: Restore source-linked strategy notes and apply the description writing guide.
Scope: Ethereum, BNB Chain and Sonic sftUSD; Ethereum ssftUSD. Only descriptions and notes.
Branch: research/flying-tulip-vault-strategies-prs
PR title: notes: restore Flying Tulip strategy descriptions
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1693
Preserves: Names, flags, fees, unrelated metadata, leads, prices, reader state and timestamp caches.
Expected: Up to four existing rows and fifteen changed fields; missing rows reported, never created.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input. No network access is required.
"""

from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_DESCRIPTION, FLYING_TULIP_NOTES, FLYING_TULIP_SHORT_DESCRIPTION, FLYING_TULIP_SSFTUSD_DESCRIPTION, FLYING_TULIP_SSFTUSD_NOTES, FLYING_TULIP_SSFTUSD_SHORT_DESCRIPTION
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext

#: Frozen reviewed inventory; future deployments need a separate migration.
SFTUSD_TARGETS = (
    VaultSpec(1, "0xeb48218a4c35c814c7678cbcae88c6ee037f7625"),
    VaultSpec(56, "0xe1716796d6bf37e4049bdb6e1150cb713800feee"),
    VaultSpec(146, "0xd1e5a86f1005f6356bd022c587de0f430cd2aeb1"),
)

#: Reviewed Ethereum auto-compounder; its notes do not use FT reward equivalence.
SSFTUSD_TARGET = VaultSpec(1, "0x232128844e5af56fd4b3f8bf97e08ab477282ab5")


def migrate(context: MigrationContext) -> None:
    """Refresh only public copy for the frozen Flying Tulip inventory.

    Validate every existing target before staging any changes. Missing rows
    are reported without discovery or network calls; the runner owns preview,
    backups and atomic persistence. The maintained constants also supply the
    adapters, so future scans retain the repaired text.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting the changes and committing or previewing them.
    :raises ValueError: If an existing target is not classified as Flying Tulip.
    """
    database = context.metadata()
    sftusd_copy = {
        "_description": FLYING_TULIP_DESCRIPTION,
        "_short_description": FLYING_TULIP_SHORT_DESCRIPTION,
        "_notes": FLYING_TULIP_NOTES,
        "_protocol_notes": FLYING_TULIP_NOTES,
    }
    copies = dict.fromkeys(SFTUSD_TARGETS, sftusd_copy)
    copies[SSFTUSD_TARGET] = {
        "_description": FLYING_TULIP_SSFTUSD_DESCRIPTION,
        "_short_description": FLYING_TULIP_SSFTUSD_SHORT_DESCRIPTION,
        "_notes": FLYING_TULIP_SSFTUSD_NOTES,
    }
    missing = [spec.as_string_id() for spec in copies if spec not in database.rows]
    updates = {}
    for spec, fields in copies.items():
        row = database.rows.get(spec)
        if row is None:
            continue
        if row.get("Protocol") != "Flying Tulip":
            raise ValueError(f"Flying Tulip copy target {spec} has unexpected protocol {row.get('Protocol')!r}")
        updates[spec] = {field: value for field, value in fields.items() if row.get(field) != value}

    for spec, fields in updates.items():
        database.rows[spec].update(fields)
    context.report(inspected_rows=len(updates), missing_specs=missing, updated_fields=sum(len(fields) for fields in updates.values()))
    context.commit_metadata()
