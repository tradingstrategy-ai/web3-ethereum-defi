"""Historical Accountable redemption-settlement event reader.

Only ``RedeemClaimable`` marks Accountable settlement.  A subsequent ERC-4626
``Withdraw`` merely transfers already-claimable assets, so it must not be
treated as a new vault settlement.  Generic settlement rows are transaction
markers and intentionally do not retain individual request quantities.
"""

import datetime

from web3.contract.contract import ContractEvent
from web3.datastructures import AttributeDict

from eth_defi.erc_4626.settlement_events import (
    build_settlement_rows_from_logs,
    fetch_vault_settlement_logs,
    get_event_topic,
)
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.timestamp import get_block_timestamp
from eth_defi.vault.settlement_data import VaultSettlement

ACCOUNTABLE_PROTOCOL_NAME = "Accountable"


def fetch_accountable_settlements(
    vault: AccountableVault,
    start_block: int,
    end_block: int,
    use_hypersync: bool | None = None,
    chunk_size: int = 50_000,
) -> list[VaultSettlement]:
    """Fetch Accountable claimability settlement transaction markers.

    :param vault: Accountable vault adapter.
    :param start_block: Inclusive event range start.
    :param end_block: Inclusive event range end.
    :param use_hypersync: Whether to use Hypersync, or auto-detect it.
    :param chunk_size: JSON-RPC fallback log chunk size.
    :return: One marker per transaction containing claimability events.
    """
    event_by_topic = get_accountable_settlement_events_by_topic(vault)
    logs = fetch_vault_settlement_logs(
        web3=vault.web3,
        address=vault.address,
        topic0_list=list(event_by_topic.keys()),
        start_block=start_block,
        end_block=end_block,
        use_hypersync=use_hypersync,
        chunk_size=chunk_size,
    )
    return build_accountable_settlement_rows_from_logs(vault, logs, event_by_topic=event_by_topic)


def get_accountable_settlement_events_by_topic(vault: AccountableVault) -> dict[str, ContractEvent]:
    """Return Accountable settlement events indexed by topic zero.

    :param vault: Accountable vault adapter.
    :return: Mapping containing only ``RedeemClaimable``.
    """
    event = vault.vault_contract.events.RedeemClaimable
    return {get_event_topic(event): event}


def build_accountable_settlement_rows_from_logs(
    vault: AccountableVault,
    logs: list[AttributeDict],
    event_by_topic: dict[str, ContractEvent | str] | None = None,
) -> list[VaultSettlement]:
    """Build and de-duplicate Accountable settlement transaction markers.

    A strategy fulfilment can emit several ``RedeemClaimable`` logs in one
    transaction.  The generic settlement table has no amount columns, so this
    collapses such logs to one marker while preserving distinct transactions in
    the same block.

    :param vault: Accountable vault adapter.
    :param logs: Web3-compatible ``RedeemClaimable`` logs.
    :param event_by_topic: Optional topic map used for the stable event name.
    :return: Unique rows sorted by block and transaction hash.
    :raise ValueError: If a candidate log cannot be decoded by the ABI.
    """
    event = vault.vault_contract.events.RedeemClaimable
    normalised_logs: list[AttributeDict] = []
    for log in logs:
        decoded = event().process_log(log)
        required_fields = {"controller", "requestId", "assets", "shares"}
        missing_fields = required_fields - set(decoded["args"].keys())
        if missing_fields:
            raise ValueError(f"Accountable RedeemClaimable ABI missing fields: {sorted(missing_fields)}")
        timestamp = log.get("blockTimestamp")
        if not isinstance(timestamp, datetime.datetime):
            log = AttributeDict({**log, "blockTimestamp": get_block_timestamp(vault.web3, int(log["blockNumber"]))})
        normalised_logs.append(log)

    rows = build_settlement_rows_from_logs(
        chain_id=vault.chain_id,
        address=vault.address,
        web3=vault.web3,
        protocol=ACCOUNTABLE_PROTOCOL_NAME,
        logs=normalised_logs,
        event_by_topic=event_by_topic or get_accountable_settlement_events_by_topic(vault),
    )
    unique_rows: dict[tuple[str, str], VaultSettlement] = {}
    for row in rows:
        unique_rows[str(vault.address).lower(), str(row.tx_hash).lower()] = row
    return sorted(unique_rows.values(), key=lambda row: (row.block_number, str(row.tx_hash)))
