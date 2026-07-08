"""Lagoon settlement event reader.

This module scans Lagoon vault settlement events and stores them in the generic
vault settlement DuckDB database. It records when settlement logs happened;
queue sizes and per-user accounting stay out of this storage layer.
"""

import logging

from web3.contract.contract import ContractEvent
from web3.datastructures import AttributeDict

from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.vault.settlement_data import VaultSettlement, VaultSettlementDatabase
from eth_defi.vault._settlement_events import (
    build_settlement_rows_from_logs as build_generic_settlement_rows_from_logs,
    fetch_vault_settlement_logs,
    get_event_topic,
)

logger = logging.getLogger(__name__)

LAGOON_PROTOCOL_NAME = "Lagoon Finance"


def fetch_lagoon_settlements(
    vault: LagoonVault,
    start_block: int,
    end_block: int,
    use_hypersync: bool | None = None,
    chunk_size: int = 50_000,
) -> list[VaultSettlement]:
    """Fetch Lagoon settlement transactions for a vault.

    A settlement is any log that emits ``SettleDeposit`` or
    ``SettleRedeem``. If a transaction emits both events, both logs are stored.
    ``TotalAssetsUpdated`` alone is deliberately ignored because Lagoon can emit
    valuation-only updates.

    :param vault:
        Lagoon vault adapter.
    :param start_block:
        Inclusive start block.
    :param end_block:
        Inclusive end block.
    :param use_hypersync:
        Whether to use Hypersync. ``None`` auto-detects based on
        ``HYPERSYNC_API_KEY``.
    :param chunk_size:
        JSON-RPC ``eth_getLogs`` chunk size used by the fallback reader.
    :return:
        Generic settlement rows sorted by block and transaction hash.
    """
    event_by_topic = get_settlement_events_by_topic(vault)
    logs = fetch_vault_settlement_logs(
        web3=vault.web3,
        address=vault.address,
        topic0_list=list(event_by_topic.keys()),
        start_block=start_block,
        end_block=end_block,
        use_hypersync=use_hypersync,
        chunk_size=chunk_size,
    )

    return build_settlement_rows_from_logs(vault, logs, event_by_topic=event_by_topic)


def update_lagoon_settlement_database(
    database: VaultSettlementDatabase,
    vault: LagoonVault,
    start_block: int,
    end_block: int,
    use_hypersync: bool | None = None,
    chunk_size: int = 50_000,
) -> int:
    """Fetch and store Lagoon settlements for one vault.

    :param database:
        Generic settlement database.
    :param vault:
        Lagoon vault adapter.
    :param start_block:
        Inclusive start block.
    :param end_block:
        Inclusive end block.
    :param use_hypersync:
        Whether to use Hypersync. ``None`` auto-detects.
    :param chunk_size:
        JSON-RPC fallback chunk size.
    :return:
        Number of settlement rows written.
    """
    settlements = fetch_lagoon_settlements(
        vault=vault,
        start_block=start_block,
        end_block=end_block,
        use_hypersync=use_hypersync,
        chunk_size=chunk_size,
    )
    inserted = database.upsert_settlements(settlements)
    logger.info(
        "Stored %d Lagoon settlements for %s on chain %d",
        inserted,
        vault.address,
        vault.chain_id,
    )
    return inserted


def get_settlement_events_by_topic(vault: LagoonVault) -> dict[str, ContractEvent]:
    """Return Lagoon settlement event classes keyed by topic0.

    :param vault:
        Lagoon vault adapter.
    :return:
        Mapping from topic0 to event class.
    """
    events = vault.vault_contract.events
    return {
        get_event_topic(events.SettleDeposit): events.SettleDeposit,
        get_event_topic(events.SettleRedeem): events.SettleRedeem,
    }


def build_settlement_rows_from_logs(
    vault: LagoonVault,
    logs: list[AttributeDict],
    event_by_topic: dict[str, ContractEvent | str] | None = None,
) -> list[VaultSettlement]:
    """Build generic settlement rows from Lagoon settlement logs.

    :param vault:
        Lagoon vault adapter.
    :param logs:
        Web3-compatible logs containing ``SettleDeposit`` and/or
        ``SettleRedeem`` events.
    :param event_by_topic:
        Optional event topic0 to event class/name mapping used to populate
        ``event_name``.
    :return:
        Settlement rows sorted by block and transaction hash.
    """
    return build_generic_settlement_rows_from_logs(
        chain_id=vault.chain_id,
        address=vault.address,
        web3=vault.web3,
        protocol=LAGOON_PROTOCOL_NAME,
        logs=logs,
        event_by_topic=event_by_topic,
    )
