"""Persist the three user-reported illiquidity warnings.

Purpose: Refresh stored flags and notes so exports exclude the reviewed vaults.
Scope: Ethereum Silo 148 and Apostro USDC Core, and Sonic Mainstreet Greenhouse
USDC; exact deployments below, updating only _flags and _notes.
Branch: flag-illiquid-borrowable-usdc-siloid-148
PR title: notes: flag Silo 148, Apostro Core and Mainstreet Greenhouse as illiquid
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1708
Preserves: Unrelated metadata, leads, prices, reader state and timestamp caches.
Expected: Three validated targets, at most six updated fields; safe to retry.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input. No price backfill needed.
"""

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import get_notes, get_vault_special_flags
from eth_defi.vault_protocols.migration import MigrationContext

#: Exact chain/address inventory and expected persisted protocol identities.
TARGETS = (
    (VaultSpec(1, "0xa6f6887ed116f3c41497a837a0319264a2eef3fa"), "Silo Finance"),
    (VaultSpec(1, "0xff5b00730ba463994fe0dbf574c538da258bc2ca"), "Euler"),
    (VaultSpec(146, "0x17271da949bbd4713c7c599759e2bf30604fc8da"), "Euler"),
)


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed flags and notes after validating every target.

    Reject missing or conflicting deployments before changing any staged row.
    The context owns preview staging, backups and atomic persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing the proposed changes.
    """
    database = context.metadata()
    for spec, protocol in TARGETS:
        if spec not in database.rows:
            raise ValueError(f"Reviewed illiquid vault row is missing: {spec}")
        if database.rows[spec].get("Protocol") != protocol:
            raise ValueError(f"Unexpected protocol for reviewed illiquid vault: {spec}")

    changed_rows = 0
    updated_fields = 0
    for spec, protocol in TARGETS:
        row = database.rows[spec]
        updates = {
            "_flags": set(row.get("_flags") or ()) | get_vault_special_flags(spec.vault_address, protocol),
            "_notes": get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name=protocol),
        }
        changed_fields = {key: value for key, value in updates.items() if row.get(key) != value}
        changed_rows += bool(changed_fields)
        updated_fields += len(changed_fields)
        row.update(changed_fields)

    context.report(inspected_rows=len(TARGETS), changed_rows=changed_rows, updated_fields=updated_fields)
    context.commit_metadata()
