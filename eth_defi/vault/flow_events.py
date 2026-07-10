"""Asynchronous vault request event discovery helpers."""

import asyncio
import datetime
import enum
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import eth_abi
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.utils import from_unix_timestamp

try:
    import hypersync
    from hypersync import BlockField, LogField

    from eth_defi.hypersync.session import open_hypersync_stream
except ImportError:
    hypersync = None


class VaultFlowDirection(enum.Enum):
    """Direction of an asynchronous vault flow."""

    #: The flow moves denomination tokens into a vault and later claims shares.
    deposit = "deposit"

    #: The flow moves vault shares into escrow and later claims denomination tokens.
    redeem = "redeem"


@dataclass(slots=True, frozen=True)
class PendingVaultFlow:
    """An indexed asynchronous vault request event.

    This object is the protocol-neutral bridge between event discovery and
    higher-level accounting recovery. It intentionally carries both the raw
    request facts and a serialised ticket payload that can be fed back to the
    protocol-specific vault ticket reconstruction path.

    :param chain_id:
        EVM chain id where the event was emitted.

    :param vault_address:
        Vault contract that emitted the request event.

    :param owner:
        Economic owner of the request.

    :param controller:
        ERC-7540 controller address where applicable. Protocols without a
        separate controller use the owner address.

    :param direction:
        Deposit or redemption request.

    :param status:
        Status implied by the event. Request events are emitted before
        settlement, so this is normally ``pending``; callers can query the
        current status with the reconstructed ticket.

    :param request_id:
        Protocol request id, if any.

    :param settlement_id:
        Protocol settlement id, if any.

    :param raw_assets:
        Raw denomination token amount for deposits, when known.

    :param raw_shares:
        Raw vault share amount for redemptions, when known.

    :param request_tx_hash:
        Transaction hash that emitted the request event.

    :param request_block_number:
        Block number that emitted the request event.

    :param request_block_timestamp:
        Naive UTC timestamp for the request block, when provided by the event
        backend.

    :param log_index:
        Log index for stable event identity.

    :param ticket_data:
        Serialised protocol ticket data compatible with the manager's
        ``reconstruct_*_ticket()`` methods.
    """

    chain_id: int
    vault_address: HexAddress
    owner: HexAddress
    controller: HexAddress | None
    direction: VaultFlowDirection
    status: enum.Enum
    request_id: int | None
    settlement_id: int | None
    raw_assets: int | None
    raw_shares: int | None
    request_tx_hash: str
    request_block_number: int
    request_block_timestamp: datetime.datetime | None
    log_index: int
    ticket_data: dict


def create_pending_vault_flow(
    *,
    chain_id: int,
    vault_address: HexAddress,
    owner: HexAddress,
    controller: HexAddress | None,
    direction: VaultFlowDirection,
    status: enum.Enum,
    log: "IndexedVaultFlowLog",
    ticket_data: dict,
    request_id: int | None = None,
    settlement_id: int | None = None,
    raw_assets: int | None = None,
    raw_shares: int | None = None,
) -> PendingVaultFlow:
    """Create a pending vault flow from common indexed log metadata."""
    return PendingVaultFlow(
        chain_id=chain_id,
        vault_address=vault_address,
        owner=owner,
        controller=controller,
        direction=direction,
        status=status,
        request_id=request_id,
        settlement_id=settlement_id,
        raw_assets=raw_assets,
        raw_shares=raw_shares,
        request_tx_hash=log.transaction_hash,
        request_block_number=log.block_number,
        request_block_timestamp=log.block_timestamp,
        log_index=log.log_index,
        ticket_data=ticket_data,
    )


@dataclass(slots=True, frozen=True)
class IndexedVaultFlowLog:
    """Raw indexed log data returned by Hypersync for vault flow discovery."""

    #: Contract address that emitted the log.
    address: HexAddress

    #: Log topics as hex strings.
    topics: list[str | None]

    #: ABI-encoded non-indexed event data.
    data: str

    #: Emitting block number.
    block_number: int

    #: Optional block timestamp as naive UTC.
    block_timestamp: datetime.datetime | None

    #: Transaction hash as a hex string.
    transaction_hash: str

    #: Log index inside the transaction.
    log_index: int


async def _fetch_vault_flow_logs_hypersync_async(
    *,
    hypersync_client,
    vault_address: HexAddress,
    topic0_list: list[str],
    start_block: int,
    end_block: int,
    recv_timeout: float = 90.0,
) -> list[IndexedVaultFlowLog]:
    """Fetch raw vault request logs with Hypersync.

    :param hypersync_client:
        Configured Hypersync client for the vault chain.

    :param vault_address:
        Vault contract address to scan.

    :param topic0_list:
        Event signature topics to include.

    :param start_block:
        Inclusive start block.

    :param end_block:
        Inclusive end block.

    :param recv_timeout:
        Timeout for each stream receive.

    :return:
        Raw logs sorted by ``(block_number, log_index)``.
    """
    return await _fetch_vault_flow_logs_for_addresses_hypersync_async(
        hypersync_client=hypersync_client,
        vault_addresses=[vault_address],
        topic0_list=topic0_list,
        start_block=start_block,
        end_block=end_block,
        recv_timeout=recv_timeout,
    )


async def _fetch_vault_flow_logs_for_addresses_hypersync_async(
    *,
    hypersync_client,
    vault_addresses: list[HexAddress | str],
    topic0_list: list[str],
    start_block: int,
    end_block: int,
    recv_timeout: float = 90.0,
) -> list[IndexedVaultFlowLog]:
    """Fetch raw vault request logs for multiple vaults with Hypersync.

    :param hypersync_client:
        Configured Hypersync client for the vault chain.

    :param vault_addresses:
        Vault contract addresses to scan in one chain-level request.

    :param topic0_list:
        Event signature topics to include.

    :param start_block:
        Inclusive start block.

    :param end_block:
        Inclusive end block.

    :param recv_timeout:
        Timeout for each stream receive.

    :return:
        Raw logs sorted by ``(block_number, log_index)``.
    """
    assert hypersync is not None, "hypersync package is required"
    assert start_block <= end_block, f"Bad block range: {start_block} - {end_block}"
    assert vault_addresses, "Vault address list cannot be empty"

    query = hypersync.Query(
        from_block=start_block,
        # Hypersync uses an exclusive to_block.
        to_block=end_block + 1,
        logs=[
            hypersync.LogSelection(
                address=[str(address).lower() for address in vault_addresses],
                topics=[topic0_list],
            )
        ],
        field_selection=hypersync.FieldSelection(
            block=[BlockField.NUMBER, BlockField.TIMESTAMP],
            log=[
                LogField.BLOCK_NUMBER,
                LogField.LOG_INDEX,
                LogField.ADDRESS,
                LogField.TRANSACTION_HASH,
                LogField.TOPIC0,
                LogField.TOPIC1,
                LogField.TOPIC2,
                LogField.TOPIC3,
                LogField.DATA,
            ],
        ),
    )

    receiver = await open_hypersync_stream(hypersync_client, query)
    events: list[IndexedVaultFlowLog] = []
    while True:
        res = await asyncio.wait_for(receiver.recv(), timeout=recv_timeout)
        if res is None:
            break

        block_timestamps = {int(block.number): from_unix_timestamp(decode_hypersync_int(block.timestamp)) for block in res.data.blocks or [] if block.number is not None and block.timestamp is not None}

        for log in res.data.logs or []:
            block_number = decode_hypersync_int(log.block_number)
            events.append(
                IndexedVaultFlowLog(
                    address=Web3.to_checksum_address(log.address),
                    topics=log.topics,
                    data=log.data or "0x",
                    block_number=block_number,
                    block_timestamp=block_timestamps.get(block_number),
                    transaction_hash=log.transaction_hash,
                    log_index=decode_hypersync_int(log.log_index),
                )
            )

    events.sort(key=lambda e: (e.block_number, e.log_index))
    return events


def decode_hypersync_int(value: int | str) -> int:
    """Decode a hex integer field returned by Hypersync."""
    if isinstance(value, int):
        return value
    if value.startswith("0x"):
        value = value[2:]
    return int(value, 16)


def normalise_event_topic(topic: str | bytes) -> str:
    """Normalise an event topic to a lower-case ``0x``-prefixed hex string.

    :param topic:
        Topic value from Web3.py or Hypersync.

    :return:
        Lower-case ``0x``-prefixed topic.
    """
    if isinstance(topic, bytes):
        topic = topic.hex()
    if not topic.startswith("0x"):
        topic = "0x" + topic
    return topic.lower()


def event_data_to_bytes(data: str | bytes) -> bytes:
    """Convert ABI event data to bytes.

    :param data:
        Hex string or bytes returned by the event backend.

    :return:
        Raw ABI payload bytes.
    """
    if isinstance(data, bytes):
        return data
    if data.startswith("0x"):
        data = data[2:]
    return bytes.fromhex(data)


def decode_indexed_event_address(topic: str | bytes) -> HexAddress:
    """Decode an indexed address topic.

    :param topic:
        ABI event topic containing a right-aligned address.

    :return:
        Checksummed address.
    """
    normalised = normalise_event_topic(topic)
    return Web3.to_checksum_address("0x" + normalised[-40:])


def decode_indexed_event_uint(topic: str | bytes) -> int:
    """Decode an indexed unsigned integer topic.

    :param topic:
        ABI event topic.

    :return:
        Decoded integer.
    """
    normalised = normalise_event_topic(topic)
    return int(normalised, 16)


def decode_single_uint256_event_data(data: str | bytes) -> int:
    """Decode a single non-indexed ``uint256`` event argument.

    :param data:
        ABI event data payload.

    :return:
        Decoded integer.
    """
    return int(eth_abi.decode(["uint256"], event_data_to_bytes(data))[0])


def fetch_vault_flow_logs_hypersync(
    *,
    hypersync_client,
    vault_address: HexAddress,
    topic0_list: list[str],
    start_block: int,
    end_block: int,
) -> list[IndexedVaultFlowLog]:
    """Fetch raw vault request logs with Hypersync.

    This synchronous wrapper keeps vault manager APIs threaded and blocking,
    matching the rest of the vault manager interface.

    :param hypersync_client:
        Configured Hypersync client for the vault chain.

    :param vault_address:
        Vault contract address to scan.

    :param topic0_list:
        Event signature topics to include.

    :param start_block:
        Inclusive start block.

    :param end_block:
        Inclusive end block.

    :return:
        Raw logs sorted by ``(block_number, log_index)``.
    """
    return fetch_vault_flow_logs_for_addresses_hypersync(
        hypersync_client=hypersync_client,
        vault_addresses=[vault_address],
        topic0_list=topic0_list,
        start_block=start_block,
        end_block=end_block,
    )


def fetch_vault_flow_logs_for_addresses_hypersync(
    *,
    hypersync_client,
    vault_addresses: list[HexAddress | str],
    topic0_list: list[str],
    start_block: int,
    end_block: int,
) -> list[IndexedVaultFlowLog]:
    """Fetch raw vault request logs for multiple vaults with Hypersync.

    This synchronous wrapper keeps vault manager APIs threaded and blocking,
    matching the rest of the vault manager interface.

    :param hypersync_client:
        Configured Hypersync client for the vault chain.

    :param vault_addresses:
        Vault contract addresses to scan in one Hypersync request.

    :param topic0_list:
        Event signature topics to include.

    :param start_block:
        Inclusive start block.

    :param end_block:
        Inclusive end block.

    :return:
        Raw logs sorted by ``(block_number, log_index)``.
    """
    coroutine = _fetch_vault_flow_logs_for_addresses_hypersync_async(
        hypersync_client=hypersync_client,
        vault_addresses=vault_addresses,
        topic0_list=topic0_list,
        start_block=start_block,
        end_block=end_block,
    )
    return run_vault_flow_log_fetch(coroutine)


def run_vault_flow_log_fetch(coroutine) -> list[IndexedVaultFlowLog]:
    """Run a Hypersync log fetch coroutine from sync code.

    :param coroutine:
        Coroutine returning indexed vault flow logs.

    :return:
        Raw logs sorted by ``(block_number, log_index)``.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coroutine)
        return future.result()
