"""Persist the reviewed Wrapped HLP illiquidity warning.

Purpose: Persist the reviewed Wrapped HLP illiquidity warning.
Scope: HyperEVM 0x06fd9d03b3d0f18e4919919b72d30c582f0a97e5 only; _flags and _notes.
Branch: notes/wrapped-hlp-illiquid
PR title: notes: mark Wrapped HLP as illiquid
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1677
Preserves: Unrelated row fields, leads, historical prices, reader state and timestamp caches.
Expected: Report changed/missing entries; repeated successful applies are skipped.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input.
"""

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import get_notes, get_vault_special_flags
from eth_defi.vault_protocols.migration import MigrationContext


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed repair and report its committed or proposed fields.

    The context owns backup, persistence and preview staging for the batch.

    :param context: Guarded migration attempt and shared database.
    :return: ``None`` after validation and commit/preview.
    """
    database = context.metadata()
    spec = VaultSpec(999, "0x06fd9d03b3d0f18e4919919b72d30c582f0a97e5")
    if spec not in database.rows:
        raise ValueError("Reviewed Wrapped HLP row is missing")
    row = database.rows[spec]
    if row.get("Protocol") != "Hyperlend":
        raise ValueError("Unexpected Wrapped HLP protocol")
    row["_flags"] = set(row.get("_flags") or ()) | get_vault_special_flags(spec.vault_address, row["Protocol"])
    row["_notes"] = get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name=row["Protocol"])
    context.commit_metadata()
