"""Neutral Trade collection and Solana dataset identities."""

from pathlib import Path

# Compatibility exports keep existing collectors and imported helpers working.
from eth_defi.solana import PUBLIC_KEY_BYTES, SOLANA_CHAIN_ID, make_solana_vault_address

__all__ = ["NEUTRAL_TRADE_DATABASE", "PUBLIC_KEY_BYTES", "SOLANA_DATASET_CHAIN_ID", "is_public_metadata", "make_solana_vault_address"]

#: Compatibility name for the shared Solana mainnet dataset reservation.
SOLANA_DATASET_CHAIN_ID = SOLANA_CHAIN_ID

#: Default private source archive; scanner path follows PIPELINE_DATA_DIR.
NEUTRAL_TRADE_DATABASE = Path.home() / ".tradingstrategy/vaults/neutral-trade-vaults.duckdb"


def is_public_metadata(row: dict) -> bool:
    """Apply native catalogue approval without deleting archival metadata.

    Other protocols keep their established admission rules. Neutral rows need
    affirmative approval and verified continuous price history for publication.

    :param row: Shared vault metadata record.
    :return: Whether the public cleaner/JSON exporter may include this record.
    """
    if row.get("Protocol") != "Neutral Trade":
        return True
    decision = row.get("_neutral_trade") or {}
    return decision.get("visible") is True and decision.get("history_verified") is True
