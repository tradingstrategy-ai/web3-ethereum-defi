"""D2 Finance settlement and phase event reader.

D2 vaults are epoch based. The contract emits lifecycle events that explain
historical phase transitions:

``EpochStarted``
    New funding/epoch schedule was published. The event payload contains the
    funding start, epoch start, and epoch end timestamps used to infer when
    deposits and redemptions are expected to be open.

``FundsCustodied``
    Vault funds were moved to the trading account for the epoch.

``FundsReturned``
    Vault funds were returned after the epoch, making redemption checks open
    again when ``notCustodiedAndNotDuringEpoch()`` is true.

``NewMaxDeposits``
    Deposit capacity changed.
"""

import logging

from web3.contract.contract import ContractEvent
from web3.datastructures import AttributeDict

from eth_defi.erc_4626.vault_protocol.d2.vault import D2Vault
from eth_defi.vault.settlement_data import VaultSettlement, VaultSettlementDatabase
from eth_defi.vault.settlement_event_reader import (
    build_settlement_rows_from_logs,
    fetch_vault_settlement_logs,
    get_event_topic,
)

logger = logging.getLogger(__name__)

D2_PROTOCOL_NAME = "D2 Finance"


def fetch_d2_settlements(
    vault: D2Vault,
    start_block: int,
    end_block: int,
    use_hypersync: bool | None = None,
    chunk_size: int = 50_000,
) -> list[VaultSettlement]:
    """Fetch D2 settlement and phase transition events for a vault.

    :param vault:
        D2 vault adapter.
    :param start_block:
        Inclusive start block.
    :param end_block:
        Inclusive end block.
    :param use_hypersync:
        Whether to use Hypersync. ``None`` auto-detects based on environment.
    :param chunk_size:
        JSON-RPC ``eth_getLogs`` chunk size used by the fallback reader.
    :return:
        Generic settlement rows sorted by block and transaction hash.
    """
    event_by_topic = get_d2_settlement_events_by_topic(vault)
    logs = fetch_vault_settlement_logs(
        web3=vault.web3,
        address=vault.address,
        topic0_list=list(event_by_topic.keys()),
        start_block=start_block,
        end_block=end_block,
        use_hypersync=use_hypersync,
        chunk_size=chunk_size,
    )
    return build_d2_settlement_rows_from_logs(vault, logs, event_by_topic=event_by_topic)


def update_d2_settlement_database(
    database: VaultSettlementDatabase,
    vault: D2Vault,
    start_block: int,
    end_block: int,
    use_hypersync: bool | None = None,
    chunk_size: int = 50_000,
) -> int:
    """Fetch and store D2 settlement and phase transition events.

    :param database:
        Generic settlement database.
    :param vault:
        D2 vault adapter.
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
    settlements = fetch_d2_settlements(
        vault=vault,
        start_block=start_block,
        end_block=end_block,
        use_hypersync=use_hypersync,
        chunk_size=chunk_size,
    )
    inserted = database.upsert_settlements(settlements)
    logger.info(
        "Stored %d D2 settlement/phase events for %s on chain %d",
        inserted,
        vault.address,
        vault.chain_id,
    )
    return inserted


def get_d2_settlement_events_by_topic(vault: D2Vault) -> dict[str, ContractEvent]:
    """Return D2 settlement and phase event classes keyed by topic0.

    :param vault:
        D2 vault adapter.
    :return:
        Mapping from topic0 to event class.
    """
    events = vault.vault_contract.events
    return {
        get_event_topic(events.EpochStarted): events.EpochStarted,
        get_event_topic(events.FundsCustodied): events.FundsCustodied,
        get_event_topic(events.FundsReturned): events.FundsReturned,
        get_event_topic(events.NewMaxDeposits): events.NewMaxDeposits,
    }


def build_d2_settlement_rows_from_logs(
    vault: D2Vault,
    logs: list[AttributeDict],
    event_by_topic: dict[str, ContractEvent | str] | None = None,
) -> list[VaultSettlement]:
    """Build generic settlement rows from D2 settlement and phase logs.

    :param vault:
        D2 vault adapter.
    :param logs:
        Web3-compatible logs.
    :param event_by_topic:
        Optional event topic0 to event class/name mapping used to populate
        ``event_name``.
    :return:
        Settlement rows sorted by block and transaction hash.
    """
    return build_settlement_rows_from_logs(
        chain_id=vault.chain_id,
        address=vault.address,
        web3=vault.web3,
        protocol=D2_PROTOCOL_NAME,
        logs=logs,
        event_by_topic=event_by_topic,
    )
