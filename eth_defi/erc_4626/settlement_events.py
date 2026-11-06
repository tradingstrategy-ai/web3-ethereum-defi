"""ERC-4626 event reader helpers for settlement-style events.

This module contains the chain-log mechanics shared by protocol-specific
settlement readers. Protocol modules decide which events matter; this helper
fetches logs and converts them to :class:`eth_defi.vault.settlement_data.VaultSettlement`
rows for the generic DuckDB store.
"""

import asyncio
import datetime
import logging
import os
from collections.abc import Iterator, Mapping

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract.contract import ContractEvent
from web3.datastructures import AttributeDict

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.timestamp import get_block_timestamp
from eth_defi.vault.flow_events import (
    IndexedVaultFlowLog,
    _fetch_vault_flow_logs_for_addresses_hypersync_async,
    run_vault_flow_log_fetch,
    hypersync,
)
from eth_defi.vault.settlement_data import VaultSettlement

try:
    from eth_defi.hypersync.utils import configure_hypersync_from_env
    from eth_defi.hypersync.session import ThrottledHypersyncClient
except ImportError:
    configure_hypersync_from_env = None

logger = logging.getLogger(__name__)


def get_event_topic(event: ContractEvent) -> str:
    """Return lower-case ``0x``-prefixed event topic0.

    :param event:
        Web3.py contract event class.
    :return:
        Normalised topic string.
    """
    return normalise_topic(get_topic_signature_from_event(event))


def normalise_topic(topic: str) -> str:
    """Normalise an event topic.

    :param topic:
        Topic as a string, with or without ``0x`` prefix.
    :return:
        Lower-case ``0x``-prefixed topic.
    """
    topic = topic.lower()
    if not topic.startswith("0x"):
        topic = "0x" + topic
    return topic


def should_use_hypersync(hypersync_api_key: str | None = None) -> bool:
    """Check whether Hypersync should be used for event reads.

    :param hypersync_api_key:
        Explicit credential overriding the legacy environment default.
    :return:
        ``True`` if the optional package and an explicit or environment key exist and
        ``USE_HYPERSYNC`` is not explicitly disabled.
    """
    requested = os.environ.get("USE_HYPERSYNC", "true").lower() not in {"0", "false", "no"}
    return requested and os.environ.get("SCAN_BACKEND", "auto") != "rpc" and hypersync is not None and bool(hypersync_api_key or os.environ.get("HYPERSYNC_API_KEY"))


def fetch_vault_settlement_logs(
    *,
    web3: Web3,
    address: HexAddress | str,
    topic0_list: list[str],
    start_block: int,
    end_block: int,
    use_hypersync: bool | None = None,
    chunk_size: int = 50_000,
    hypersync_api_key: str | None = None,
    hypersync_timeout: float | None = None,
) -> list[AttributeDict]:
    """Fetch vault settlement-style logs using Hypersync or JSON-RPC.

    :param web3:
        Web3 connection for the vault chain.
    :param address:
        Vault contract address.
    :param topic0_list:
        Event topic0 values to include.
    :param start_block:
        Inclusive start block.
    :param end_block:
        Inclusive end block.
    :param use_hypersync:
        Whether to use Hypersync. ``None`` auto-detects based on environment.
    :param hypersync_api_key:
        Explicit credential overriding the legacy environment default.
    :param chunk_size:
        JSON-RPC ``eth_getLogs`` chunk size used by the fallback reader.
    :param hypersync_timeout:
        Optional whole-read timeout; None retains legacy per-receive limits.
    :return:
        Web3-compatible log objects.
    """
    assert start_block <= end_block, f"Bad block range: {start_block:,} - {end_block:,}"

    return fetch_vault_settlement_logs_for_addresses(
        hypersync_api_key=hypersync_api_key,
        web3=web3,
        addresses=[address],
        topic0_list=topic0_list,
        start_block=start_block,
        end_block=end_block,
        use_hypersync=use_hypersync,
        chunk_size=chunk_size,
        hypersync_timeout=hypersync_timeout,
    )


def fetch_vault_settlement_logs_for_addresses(
    *,
    web3: Web3,
    addresses: list[HexAddress | str],
    topic0_list: list[str],
    start_block: int,
    end_block: int,
    use_hypersync: bool | None = None,
    chunk_size: int = 50_000,
    hypersync_api_key: str | None = None,
    hypersync_timeout: float | None = None,
) -> list[AttributeDict]:
    """Fetch vault settlement-style logs for multiple vaults as one address batch.

    :param web3:
        Web3 connection for the vault chain.
    :param addresses:
        Vault contract addresses.
    :param topic0_list:
        Event topic0 values to include.
    :param start_block:
        Inclusive start block.
    :param end_block:
        Inclusive end block.
    :param use_hypersync:
        Whether to use Hypersync. ``None`` auto-detects based on environment.
    :param hypersync_api_key:
        Explicit credential overriding the legacy environment default.
    :param chunk_size:
        JSON-RPC ``eth_getLogs`` chunk size used by the fallback reader.
    :param hypersync_timeout:
        Optional whole-read timeout; None retains legacy per-receive limits.
    :return:
        Web3-compatible log objects.
    """
    assert start_block <= end_block, f"Bad block range: {start_block:,} - {end_block:,}"
    assert addresses, "Vault address list cannot be empty"

    use_hypersync = should_use_hypersync(hypersync_api_key) if use_hypersync is None else use_hypersync
    if use_hypersync:
        return fetch_vault_settlement_logs_hypersync_for_addresses(
            hypersync_api_key=hypersync_api_key,
            web3=web3,
            addresses=addresses,
            topic0_list=topic0_list,
            start_block=start_block,
            end_block=end_block,
            hypersync_timeout=hypersync_timeout,
        )

    return fetch_vault_settlement_logs_rpc(
        web3=web3,
        address=addresses,
        topics=topic0_list,
        start_block=start_block,
        end_block=end_block,
        chunk_size=chunk_size,
    )


def fetch_vault_settlement_logs_hypersync(
    *,
    web3: Web3,
    address: HexAddress | str,
    topic0_list: list[str],
    start_block: int,
    end_block: int,
    hypersync_api_key: str | None = None,
    hypersync_timeout: float | None = None,
) -> list[AttributeDict]:
    """Fetch vault settlement-style logs using Hypersync.

    :param web3:
        Web3 connection for the vault chain.
    :param address:
        Vault contract address.
    :param topic0_list:
        Event topic0 values.
    :param start_block:
        Inclusive start block.
    :param end_block:
        Inclusive end block.
    :param hypersync_api_key:
        Explicit HyperSync credential; disabled or absent credentials use RPC in executor callers.
    :param hypersync_timeout:
        Optional whole-read timeout; None retains legacy per-receive limits.
    :return:
        Web3-compatible log objects.
    """
    return fetch_vault_settlement_logs_hypersync_for_addresses(
        hypersync_api_key=hypersync_api_key,
        web3=web3,
        addresses=[address],
        topic0_list=topic0_list,
        start_block=start_block,
        end_block=end_block,
        hypersync_timeout=hypersync_timeout,
    )


def fetch_vault_settlement_logs_hypersync_for_addresses(
    *,
    web3: Web3,
    addresses: list[HexAddress | str],
    topic0_list: list[str],
    start_block: int,
    end_block: int,
    hypersync_api_key: str | None = None,
    hypersync_timeout: float | None = None,
) -> list[AttributeDict]:
    """Fetch vault settlement-style logs for multiple vaults using Hypersync.

    :param web3:
        Web3 connection for the vault chain.
    :param addresses:
        Vault contract addresses.
    :param topic0_list:
        Event topic0 values.
    :param start_block:
        Inclusive start block.
    :param end_block:
        Inclusive end block.
    :param hypersync_api_key:
        Explicit HyperSync credential; disabled or absent credentials use RPC in executor callers.
    :param hypersync_timeout:
        Optional whole-read timeout; None retains legacy per-receive limits.
    :return:
        Web3-compatible log objects.
    """
    if hypersync_api_key is None:
        # Preserve the native SDK retry defaults for existing bulk/library callers.
        client = hypersync.HypersyncClient(
            hypersync.ClientConfig(
                url=get_hypersync_server(web3),
                api_token=os.environ["HYPERSYNC_API_KEY"],
            )
        )
    else:
        if configure_hypersync_from_env is None:
            raise RuntimeError("HyperSync package is unavailable")
        client = configure_hypersync_from_env(web3, hypersync_api_key=hypersync_api_key).hypersync_client
        if client is None:
            raise RuntimeError("HyperSync is unavailable for settlement reads")
    indexed_logs = run_vault_flow_log_fetch(
        _fetch_complete_settlement_logs(
            client,
            addresses,
            topic0_list,
            start_block,
            end_block,
            hypersync_timeout,
        )
    )
    logger.info(
        "Fetched %d vault settlement logs for %d vaults using Hypersync from blocks %d - %d",
        len(indexed_logs),
        len(addresses),
        start_block,
        end_block,
    )
    return [indexed_log_to_web3_log(log) for log in indexed_logs]


async def _fetch_complete_settlement_logs(client: "ThrottledHypersyncClient | hypersync.HypersyncClient", addresses: list[HexAddress | str], topics: list[str], start_block: int, end_block: int, timeout: float | None) -> list[IndexedVaultFlowLog]:
    """Refuse partial settlement evidence before accounting advances its cursor.

    The conservative height boundary follows existing vault managers. Check
    advertised stream progress before accepting an empty result, following
    https://docs.envio.dev/docs/HyperSync/hypersync-query. This detects reported
    truncation, but still relies on the indexer returning the correct logs.

    :param client:
        Native legacy client or configured throttled client.
    :param addresses:
        Vault contracts selected in one query.
    :param topics:
        Settlement event signatures.
    :param start_block:
        Inclusive first block.
    :param end_block:
        Inclusive final block, which must already be indexed.
    :param timeout:
        Optional total read timeout. None retains the existing per-receive limit.
    :return:
        Indexed logs in block/log order after checking reported range completion.
    """

    async def read_range() -> list[IndexedVaultFlowLog]:
        if await client.get_height() - 1 < end_block:
            raise RuntimeError("HyperSync has not indexed the settlement range")
        return await _fetch_vault_flow_logs_for_addresses_hypersync_async(
            hypersync_client=client,
            vault_addresses=addresses,
            topic0_list=topics,
            start_block=start_block,
            end_block=end_block,
            require_complete=True,
        )

    return await asyncio.wait_for(read_range(), timeout=timeout)


def indexed_log_to_web3_log(log: IndexedVaultFlowLog) -> AttributeDict:
    """Convert a Hypersync log to Web3.py log shape.

    :param log:
        Hypersync log wrapper.
    :return:
        Web3.py-style log.
    """
    return AttributeDict(
        {
            "address": Web3.to_checksum_address(log.address),
            "topics": [HexBytes(topic) for topic in log.topics if topic is not None],
            "data": HexBytes(log.data),
            "blockNumber": log.block_number,
            "transactionHash": HexBytes(log.transaction_hash),
            "transactionIndex": 0,
            "blockHash": HexBytes(b"\x00" * 32),
            "logIndex": log.log_index,
            "removed": False,
            "blockTimestamp": log.block_timestamp,
        }
    )


def fetch_vault_settlement_logs_rpc(
    *,
    web3: Web3,
    address: HexAddress | str | list[HexAddress | str],
    topics: list[str],
    start_block: int,
    end_block: int,
    chunk_size: int,
) -> list[AttributeDict]:
    """Fetch vault settlement-style logs using chunked JSON-RPC ``eth_getLogs``.

    :param web3:
        Web3 connection.
    :param address:
        Vault contract address or list of addresses.
    :param topics:
        Event topic0 values.
    :param start_block:
        Inclusive start block.
    :param end_block:
        Inclusive end block.
    :param chunk_size:
        Maximum block count per ``eth_getLogs`` call.
    :return:
        Web3-compatible logs.
    """
    logs: list[AttributeDict] = []
    current = start_block
    while current <= end_block:
        to_block = min(current + chunk_size - 1, end_block)
        logs.extend(_fetch_logs_range(web3, address, topics, current, to_block, chunk_size))
        current = to_block + 1
    return logs


def _fetch_logs_range(
    web3: Web3,
    address: HexAddress | str | list[HexAddress | str],
    topics: list[str],
    start_block: int,
    end_block: int,
    chunk_size: int,
) -> Iterator[AttributeDict]:
    """Fetch one JSON-RPC log range, splitting on provider range errors."""
    if isinstance(address, list):
        checksum_address: str | list[str] = [Web3.to_checksum_address(item) for item in address]
    else:
        checksum_address = Web3.to_checksum_address(address)
    params = {
        "fromBlock": start_block,
        "toBlock": end_block,
        "address": checksum_address,
        "topics": [topics],
    }
    try:
        logs = web3.eth.get_logs(params)
        logger.info("Fetched %d vault settlement logs from blocks %d - %d", len(logs), start_block, end_block)
        yield from logs
    except ValueError:
        if start_block == end_block or chunk_size <= 1:
            raise
        midpoint = (start_block + end_block) // 2
        logger.warning("eth_getLogs failed for blocks %d - %d, splitting range", start_block, end_block)
        yield from _fetch_logs_range(web3, address, topics, start_block, midpoint, max(1, chunk_size // 2))
        yield from _fetch_logs_range(web3, address, topics, midpoint + 1, end_block, max(1, chunk_size // 2))


def build_settlement_rows_from_logs(
    *,
    chain_id: int,
    address: HexAddress | str,
    web3: Web3 | None,
    protocol: str,
    logs: list[AttributeDict],
    event_by_topic: Mapping[str, ContractEvent | str] | None = None,
) -> list[VaultSettlement]:
    """Build generic settlement rows from vault logs.

    :param chain_id:
        Vault chain id.
    :param address:
        Vault address.
    :param web3:
        Web3 connection, needed when logs do not contain timestamps or block
        hashes.
    :param protocol:
        Protocol name stored in DuckDB.
    :param logs:
        Web3-compatible logs.
    :param event_by_topic:
        Optional event topic0 to event class/name mapping used to populate
        ``event_name``.
    :return:
        Settlement rows sorted by block and transaction hash.
    """
    if not logs:
        return []

    rows: list[VaultSettlement] = []
    block_timestamp_cache: dict[int, datetime.datetime] = {}
    block_hash_cache: dict[int, HexBytes] = {}
    event_by_topic = event_by_topic or {}

    for log in sorted(logs, key=lambda item: (int(item["blockNumber"]), int(item["logIndex"]))):
        tx_hash = "0x" + HexBytes(log["transactionHash"]).hex()
        event_name = get_log_event_name(log, event_by_topic)

        block_number = int(log["blockNumber"])
        timestamp = log.get("blockTimestamp")
        if isinstance(timestamp, str):
            timestamp = native_datetime_utc_fromtimestamp(int(timestamp, 16))
        elif isinstance(timestamp, int):
            timestamp = native_datetime_utc_fromtimestamp(timestamp)
        if timestamp is None:
            assert web3 is not None, "web3 is needed to resolve missing block timestamps"
            timestamp = block_timestamp_cache.get(block_number)
            if timestamp is None:
                timestamp = get_block_timestamp(web3, block_number)
                block_timestamp_cache[block_number] = timestamp

        block_hash = HexBytes(log.get("blockHash", b""))
        if not block_hash or block_hash == HexBytes(b"\x00" * 32):
            assert web3 is not None, "web3 is needed to resolve missing block hashes"
            block_hash = block_hash_cache.get(block_number)
            if block_hash is None:
                block_hash = HexBytes(web3.eth.get_block(block_number)["hash"])
                block_hash_cache[block_number] = block_hash

        rows.append(
            VaultSettlement(
                chain_id=chain_id,
                address=address,
                block_number=block_number,
                protocol=protocol,
                block_hash=block_hash,
                timestamp=timestamp,
                tx_hash=tx_hash,
                event_name=event_name,
            )
        )

    return sorted(rows, key=lambda row: (row.block_number, str(row.tx_hash), row.event_name))


def get_log_event_name(log: AttributeDict, event_by_topic: Mapping[str, ContractEvent | str]) -> str:
    """Resolve a Web3 log event name.

    :param log:
        Web3-compatible log.
    :param event_by_topic:
        Mapping from normalised topic0 to event class or event name.
    :return:
        Event name, or empty string if it cannot be resolved.
    """
    event_name = log.get("eventName") or log.get("event")
    if event_name:
        return str(event_name)

    topics = log.get("topics") or []
    if not topics:
        return ""

    topic0 = normalise_log_topic(topics[0])
    event = event_by_topic.get(topic0)
    if event is None:
        return ""
    if isinstance(event, str):
        return event

    event_name = getattr(event, "event_name", None)
    if event_name:
        return str(event_name)

    abi = getattr(event, "abi", None)
    if isinstance(abi, dict):
        return str(abi.get("name") or "")

    return ""


def normalise_log_topic(topic: HexBytes | bytes | str) -> str:
    """Normalise a Web3 log topic value to lowercase ``0x`` hex.

    :param topic:
        Topic as bytes, HexBytes, or string.
    :return:
        Lower-case ``0x``-prefixed topic.
    """
    if hasattr(topic, "hex") and not isinstance(topic, str):
        topic = topic.hex()
    topic = str(topic).lower()
    if not topic.startswith("0x"):
        topic = "0x" + topic
    return topic
