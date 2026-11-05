# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Persist the user-reported Peapods USDC-65 illiquidity warning.

Purpose: Refresh stored flags and notes so exports exclude the reviewed vault.
Scope: Base 8453:0x2e5109aa82a4ebcc776e89abeb68f5d0b91ccea1; only _flags and _notes.
Branch: notes/peapods-usdc-65-illiquid
PR title: notes: mark Peapods USDC-65 as illiquid
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1731
Preserves: Unrelated flags, metadata, prices, leads, reader state and timestamp caches.
Expected: At most one existing row and two changed fields; missing targets are
reported without creation. Idempotent transformation.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific input. No network reads or price backfill
are needed for this manual warning.
Run after merge using the stopped-scanner Compose workflow in
.claude/docs/migration-scripts.md; preview all pending migrations before apply.
"""

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import get_notes, get_vault_special_flags
from eth_defi.vault_protocols.migration import MigrationContext

#: Exact Peapods deployment reported as illiquid on 2026-11-05.
TARGET = VaultSpec(8453, "0x2e5109aa82a4ebcc776e89abeb68f5d0b91ccea1")


def migrate(context: MigrationContext) -> None:
    """Stage the manual warning for the reviewed Base vault.

    Preserve existing flags and unrelated fields. The context owns preview
    staging, backups and atomic persistence; missing rows are not created.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing or previewing the repair.
    :raises ValueError: If the target row has an unexpected protocol.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, missing_specs=[TARGET.as_string_id()], updated_fields=0)
        context.commit_metadata()
        return
    if row.get("Protocol") != "Peapods":
        raise ValueError(f"Unexpected protocol for reviewed Peapods vault: {TARGET}")
    fields = {
        "_flags": set(row.get("_flags") or ()) | get_vault_special_flags(TARGET.vault_address, "Peapods"),
        "_notes": get_notes(TARGET.vault_address, chain_id=TARGET.chain_id, protocol_name="Peapods"),
    }
    updates = {field: value for field, value in fields.items() if row.get(field) != value}
    row.update(updates)
    context.report(inspected_rows=1, missing_specs=[], updated_fields=len(updates))
    context.commit_metadata()
