# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Persist the user-reported Peapods USDC metavault illiquidity warning.

Purpose: Refresh the stored flag and note to exclude the vault from investable rankings.
Scope: One Peapods deployment on Base; update only _flags and _notes.
Branch: notes/peapods-metavault-usdc-illiquid
PR title: notes: mark the Peapods USDC metavault on Base as illiquid
PR: Pending creation.
Preserves: Unrelated flags and metadata, prices, leads, reader state and
timestamp caches. No historical price backfill is needed.
Expected: One validated target and up to two changed fields. Safe to retry.

LLM-generated operational migration. Changes require human review.

Run after merge
---------------

Follow the production runbook in `.claude/docs/migration-scripts.md` and
the commands in the PR. Build the git-stamped image from merged master,
stop the scanner and other metadata writers, and preview all pending
migrations before applying them. After restarting the scanner, confirm
that the published vault carries the flag and note and is excluded from
investable rankings.
"""

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import get_notes, get_vault_special_flags
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed Peapods Metavault for USD Coin deployment on Base.
TARGET = VaultSpec(8453, "0x0887354c58af4392f6012c8cb96354342912902c")


def migrate(context: MigrationContext) -> None:
    """Stage the maintained warning for the existing vault row.

    Resolve the flag and note from the manual table and keep unrelated flags.
    The context owns preview staging, backups and atomic persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: None after reporting and committing or previewing the changes.
    :raises ValueError: If the reviewed vault is missing or has another protocol.
    """
    database = context.metadata()
    if TARGET not in database.rows:
        raise ValueError(f"Reviewed Peapods vault row is missing: {TARGET}")
    row = database.rows[TARGET]
    if row.get("Protocol") != "Peapods":
        raise ValueError(f"Unexpected protocol for reviewed Peapods vault: {TARGET}")

    updates = {
        "_flags": set(row.get("_flags") or ()) | get_vault_special_flags(TARGET.vault_address, "Peapods"),
        "_notes": get_notes(TARGET.vault_address, chain_id=TARGET.chain_id, protocol_name="Peapods"),
    }
    changed_fields = {key: value for key, value in updates.items() if row.get(key) != value}
    row.update(changed_fields)
    context.report(inspected_rows=1, changed_rows=int(bool(changed_fields)), updated_fields=len(changed_fields))
    context.commit_metadata()
