"""Historical Ember operator-settlement event reader.

Only successful Ember ``RequestProcessed`` events are represented in the
generic settlement database. A skipped or cancelled queue request returns
shares rather than paying denomination assets, so it must not annotate a vault
price row as a successful settlement.
"""

import datetime

from web3.contract.contract import ContractEvent
from web3.datastructures import AttributeDict

from eth_defi.erc_4626.settlement_events import (
    build_settlement_rows_from_logs,
    fetch_vault_settlement_logs,
    get_event_topic,
)
from eth_defi.erc_4626.vault_protocol.ember.vault import EmberVault
from eth_defi.timestamp import get_block_timestamp
from eth_defi.vault.settlement_data import VaultSettlement

EMBER_PROTOCOL_NAME = "Ember"


def fetch_ember_settlements(
    vault: EmberVault,
    start_block: int,
    end_block: int,
    use_hypersync: bool | None = None,
    chunk_size: int = 50_000,
) -> list[VaultSettlement]:
    """Fetch successful Ember operator processing transactions.

    :param vault:
        Ember vault adapter.
    :param start_block:
        Inclusive event range start.
    :param end_block:
        Inclusive event range end.
    :param use_hypersync:
        Whether to use Hypersync; ``None`` auto-detects configuration.
    :param chunk_size:
        JSON-RPC fallback log chunk size.
    :return:
        One generic settlement marker per successful operator transaction.
    """
    event_by_topic = get_ember_settlement_events_by_topic(vault)
    logs = fetch_vault_settlement_logs(
        web3=vault.web3,
        address=vault.address,
        topic0_list=list(event_by_topic.keys()),
        start_block=start_block,
        end_block=end_block,
        use_hypersync=use_hypersync,
        chunk_size=chunk_size,
    )
    return build_ember_settlement_rows_from_logs(vault, logs, event_by_topic=event_by_topic)


def get_ember_settlement_events_by_topic(vault: EmberVault) -> dict[str, ContractEvent]:
    """Return Ember's successful-settlement candidate event topic mapping.

    This pure ABI helper intentionally includes only ``RequestProcessed``;
    whether an individual log represents an asset payout is determined after
    decoding its skipped and cancelled flags.

    :param vault:
        Ember vault adapter exposing the packaged Ember ABI.
    :return:
        Full topic0 to Web3 event class mapping.
    """
    event = vault.vault_contract.events.RequestProcessed
    return {get_event_topic(event): event}


def build_ember_settlement_rows_from_logs(
    vault: EmberVault,
    logs: list[AttributeDict],
    event_by_topic: dict[str, ContractEvent | str] | None = None,
) -> list[VaultSettlement]:
    """Convert successful Ember processing logs to transaction settlement rows.

    A single operator transaction can process several requests. The generic
    database models transaction timestamps, so duplicate successful logs are
    collapsed using ``(vault address, transaction hash)`` while distinct same
    block transactions remain independent rows.

    :param vault:
        Ember vault adapter.
    :param logs:
        Web3-compatible ``RequestProcessed`` logs.
    :param event_by_topic:
        Optional topic map used to populate the stable event name.
    :return:
        Successful settlement rows sorted by block and transaction hash.
    :raise ValueError:
        If a supposedly Ember processing log cannot be decoded by its ABI.
    """
    event = vault.vault_contract.events.RequestProcessed
    successful_logs: list[AttributeDict] = []
    for log in logs:
        decoded = event().process_log(log)
        args = decoded["args"]
        required_fields = {"owner", "receiver", "shares", "withdrawAmount", "skipped", "cancelled", "requestSequenceNumber"}
        missing_fields = required_fields - set(args.keys())
        if missing_fields:
            raise ValueError(f"Ember RequestProcessed ABI missing fields: {sorted(missing_fields)}")
        if not args["skipped"] and not args["cancelled"]:
            # Some RPC middleware decorates logs with a hexadecimal
            # ``blockTimestamp``. Generic settlement storage requires a naive
            # UTC datetime, so resolve canonical block time unless the indexed
            # backend already supplied one in the expected representation.
            timestamp = log.get("blockTimestamp")
            if not isinstance(timestamp, datetime.datetime):
                log = AttributeDict({**log, "blockTimestamp": get_block_timestamp(vault.web3, int(log["blockNumber"]))})
            successful_logs.append(log)

    rows = build_settlement_rows_from_logs(
        chain_id=vault.chain_id,
        address=vault.address,
        web3=vault.web3,
        protocol=EMBER_PROTOCOL_NAME,
        logs=successful_logs,
        event_by_topic=event_by_topic or get_ember_settlement_events_by_topic(vault),
    )
    unique_rows: dict[tuple[str, str], VaultSettlement] = {}
    for row in rows:
        unique_rows[(str(vault.address).lower(), str(row.tx_hash).lower())] = row
    return sorted(unique_rows.values(), key=lambda row: (row.block_number, str(row.tx_hash)))
