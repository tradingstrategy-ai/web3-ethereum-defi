"""Read Chainlink Data Feeds bundle aggregators and their report history.

Chainlink bundle feeds publish multiple values in a single opaque ``bytes``
payload.  The proxy exposes the current bundle through ``latestBundle()``,
while the underlying ``DataFeedsCache`` emits every accepted update as a
``BundleReportUpdated`` event.  Historical event discovery is performed with
Hypersync, avoiding JSON-RPC log-range limitations.

The bundle schema is feed-specific.  Callers must know the word index and
decimal scale for the value they consume; this module deliberately does not
guess field meanings.  See the verified `DataFeedsCache contract
<https://etherscan.io/address/0x16b53825c8ceaea593507274d4c1aaec9e261433#code>`__
and an example `BundleReportUpdated transaction
<https://etherscan.io/tx/0x162d17b2e8e340f0c7396059a524d411ed91a28948bbd5b3123ababe74f734de#eventlog>`__.
"""

import asyncio
import datetime
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal

import eth_abi
from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.utils import from_unix_timestamp

try:
    import hypersync
    from hypersync import BlockField, LogField

    from eth_defi.hypersync.session import open_hypersync_stream
except ImportError:
    hypersync = None


#: Solidity signature emitted by Chainlink's DataFeedsCache for bundle updates.
BUNDLE_REPORT_UPDATED_EVENT_SIGNATURE = "BundleReportUpdated(bytes16,uint256,bytes)"

#: Topic zero for :data:`BUNDLE_REPORT_UPDATED_EVENT_SIGNATURE`.
BUNDLE_REPORT_UPDATED_TOPIC0 = "0x" + Web3.keccak(text=BUNDLE_REPORT_UPDATED_EVENT_SIGNATURE).hex()

#: Minimal Chainlink bundle proxy ABI used for current and historical state reads.
BUNDLE_AGGREGATOR_PROXY_ABI = [
    {"inputs": [], "name": "aggregator", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "bundleDecimals", "outputs": [{"type": "uint8[]"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "description", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "latestBundle", "outputs": [{"type": "bytes"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "latestBundleTimestamp", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
]

#: Number of bytes in a Chainlink bundle data identifier.
BUNDLE_DATA_ID_SIZE = 16

#: Number of bytes in an EVM event topic.
EVENT_TOPIC_SIZE = 32

#: Topic count for ``BundleReportUpdated``.
BUNDLE_REPORT_UPDATED_TOPIC_COUNT = 3


def _decode_hypersync_int(value: int | str) -> int:
    """Decode a Hypersync integer field.

    :param value: Integer or hexadecimal integer returned by Hypersync.
    :return: Decoded Python integer.
    """

    if isinstance(value, int):
        return value
    return int(value, 16) if value.startswith("0x") else int(value)


def _event_data_to_bytes(value: bytes | str) -> bytes:
    """Normalise event data to bytes.

    :param value: Raw bytes or a ``0x``-prefixed hexadecimal string.
    :return: Decoded event data.
    """

    if isinstance(value, bytes):
        return value
    return bytes.fromhex(value[2:] if value.startswith("0x") else value)


def encode_bundle_data_id_topic(data_id: bytes) -> str:
    """Encode an indexed ``bytes16`` feed identifier as an event topic.

    Solidity right-pads indexed fixed-size byte arrays to a 32-byte topic.

    :param data_id: Chainlink bundle feed identifier.
    :return: ``0x``-prefixed 32-byte event topic.
    :raise ValueError: If ``data_id`` is not exactly 16 bytes.
    """

    if len(data_id) != BUNDLE_DATA_ID_SIZE:
        raise ValueError(f"Chainlink bundle data id must be 16 bytes, got {len(data_id)}")
    return "0x" + data_id.hex() + "0" * 32


def decode_bundle_data_id_topic(topic: str) -> bytes:
    """Decode an indexed ``bytes16`` feed identifier.

    :param topic: ``0x``-prefixed 32-byte event topic.
    :return: The first 16 bytes containing the feed identifier.
    :raise ValueError: If the topic is not 32 bytes.
    """

    raw = _event_data_to_bytes(topic)
    if len(raw) != EVENT_TOPIC_SIZE:
        raise ValueError(f"Chainlink event topic must be 32 bytes, got {len(raw)}")
    return raw[:BUNDLE_DATA_ID_SIZE]


def decode_bundle_decimal(bundle: bytes, index: int, decimals: int, *, signed: bool = False) -> Decimal:
    """Decode one fixed-width numeric field from a Chainlink bundle.

    Bundle schemas may mix numeric and dynamic fields.  The caller is
    responsible for selecting a numeric word and supplying the corresponding
    scale from ``bundleDecimals()``.

    :param bundle: Raw bundle returned by ``latestBundle()`` or its update event.
    :param index: Zero-based 32-byte word index.
    :param decimals: Decimal scale for this field.
    :param signed: Decode the word as a signed two's-complement integer.
    :return: Human-readable decimal value.
    :raise ValueError: If the index, decimal scale, or payload length is invalid.
    """

    if index < 0:
        raise ValueError(f"Chainlink bundle index cannot be negative: {index}")
    if decimals < 0:
        raise ValueError(f"Chainlink bundle decimal scale cannot be negative: {decimals}")
    start = index * 32
    end = start + 32
    if len(bundle) < end:
        raise ValueError(f"Chainlink bundle has {len(bundle)} bytes, cannot decode word {index}")
    raw_value = int.from_bytes(bundle[start:end], byteorder="big", signed=signed)
    return Decimal(raw_value) / Decimal(10**decimals)


@dataclass(slots=True, frozen=True)
class ChainlinkLatestBundleData:
    """Current state returned by a Chainlink bundle aggregator proxy."""

    #: Bundle proxy contract.
    proxy: Contract

    #: Feed-specific opaque bundle payload.
    bundle: bytes

    #: Chainlink report timestamp as Unix seconds.
    updated_at: int

    #: Decimal metadata for feed fields.
    decimals: tuple[int, ...]

    #: Human-readable feed description.
    description: str

    #: Underlying DataFeedsCache contract emitting report events.
    aggregator_address: HexAddress

    @property
    def update_time(self) -> datetime.datetime:
        """Return the report timestamp as a naive UTC datetime."""

        return native_datetime_utc_fromtimestamp(self.updated_at)

    def decode_decimal(self, index: int, *, signed: bool = False) -> Decimal:
        """Decode a numeric field using the proxy-provided decimal scale.

        :param index: Zero-based bundle field index.
        :param signed: Decode a signed integer field.
        :return: Human-readable decimal value.
        :raise IndexError: If the proxy has no decimal metadata for ``index``.
        """

        try:
            decimals = self.decimals[index]
        except IndexError as exc:
            raise IndexError(f"Chainlink bundle has {len(self.decimals)} decimal entries, cannot decode index {index}") from exc
        return decode_bundle_decimal(self.bundle, index, decimals, signed=signed)


@dataclass(slots=True, frozen=True)
class ChainlinkBundleReport:
    """A historical Chainlink ``BundleReportUpdated`` event."""

    #: DataFeedsCache contract that emitted the event.
    aggregator_address: HexAddress

    #: Feed-specific Chainlink data identifier.
    data_id: bytes

    #: Chainlink report timestamp as Unix seconds.
    updated_at: int

    #: Feed-specific opaque bundle payload.
    bundle: bytes

    #: Block containing the accepted report.
    block_number: int

    #: Naive UTC timestamp of the containing block, when returned by Hypersync.
    block_timestamp: datetime.datetime | None

    #: Transaction hash containing the report.
    transaction_hash: str

    #: Event position in the transaction receipt.
    log_index: int

    @property
    def update_time(self) -> datetime.datetime:
        """Return the report timestamp as a naive UTC datetime."""

        return native_datetime_utc_fromtimestamp(self.updated_at)

    def decode_decimal(self, index: int, decimals: int, *, signed: bool = False) -> Decimal:
        """Decode a feed-specific numeric bundle field.

        :param index: Zero-based bundle field index.
        :param decimals: Feed-specific decimal scale for this field.
        :param signed: Decode a signed integer field.
        :return: Human-readable decimal value.
        """

        return decode_bundle_decimal(self.bundle, index, decimals, signed=signed)


def create_bundle_aggregator_proxy(web3: Web3, proxy_address: HexAddress | str) -> Contract:
    """Create a minimal Chainlink bundle proxy contract instance.

    :param web3: Web3 connection for the proxy's chain.
    :param proxy_address: Chainlink bundle aggregator proxy address.
    :return: Web3 contract instance.
    """

    return web3.eth.contract(address=Web3.to_checksum_address(proxy_address), abi=BUNDLE_AGGREGATOR_PROXY_ABI)


def fetch_chainlink_latest_bundle(
    web3: Web3,
    proxy_address: HexAddress | str,
    block_identifier: BlockIdentifier = "latest",
) -> ChainlinkLatestBundleData:
    """Fetch a Chainlink bundle from its proxy at a specific block.

    :param web3: Web3 connection for the proxy's chain.
    :param proxy_address: Chainlink bundle aggregator proxy address.
    :param block_identifier: Current or historical block identifier.
    :return: Bundle, report timestamp, decimal metadata, description and cache address.
    :raise ValueError: If the proxy returns an empty or untimestamped bundle.
    """

    proxy = create_bundle_aggregator_proxy(web3, proxy_address)
    bundle = bytes(proxy.functions.latestBundle().call(block_identifier=block_identifier))
    updated_at = proxy.functions.latestBundleTimestamp().call(block_identifier=block_identifier)
    if not bundle or updated_at <= 0:
        raise ValueError(f"Chainlink bundle proxy {proxy.address} returned an empty observation at block {block_identifier}")
    decimals = tuple(proxy.functions.bundleDecimals().call(block_identifier=block_identifier))
    description = proxy.functions.description().call(block_identifier=block_identifier)
    aggregator_address = HexAddress(proxy.functions.aggregator().call(block_identifier=block_identifier))
    return ChainlinkLatestBundleData(
        proxy=proxy,
        bundle=bundle,
        updated_at=updated_at,
        decimals=decimals,
        description=description,
        aggregator_address=aggregator_address,
    )


def decode_bundle_report_event(
    *,
    aggregator_address: HexAddress | str,
    topics: list[str | None],
    data: bytes | str,
    block_number: int,
    block_timestamp: datetime.datetime | None,
    transaction_hash: str,
    log_index: int,
) -> ChainlinkBundleReport:
    """Decode one ``BundleReportUpdated`` event.

    :param aggregator_address: DataFeedsCache contract emitting the event.
    :param topics: Event topics containing signature, data id and report timestamp.
    :param data: ABI-encoded non-indexed ``bytes bundle`` value.
    :param block_number: Block containing the event.
    :param block_timestamp: Naive UTC timestamp of the containing block.
    :param transaction_hash: Transaction containing the event.
    :param log_index: Event position within the receipt.
    :return: Decoded bundle report.
    :raise ValueError: If the topics do not match the expected event.
    """

    populated_topics = [topic for topic in topics if topic is not None]
    if len(populated_topics) != BUNDLE_REPORT_UPDATED_TOPIC_COUNT:
        raise ValueError(f"BundleReportUpdated must have three populated topics, got {len(populated_topics)}")
    if populated_topics[0].lower() != BUNDLE_REPORT_UPDATED_TOPIC0.lower():
        raise ValueError(f"Unexpected Chainlink bundle event topic: {populated_topics[0]}")
    data_id = decode_bundle_data_id_topic(populated_topics[1])
    updated_at = _decode_hypersync_int(populated_topics[2])
    (bundle,) = eth_abi.decode(["bytes"], _event_data_to_bytes(data))
    return ChainlinkBundleReport(
        aggregator_address=HexAddress(Web3.to_checksum_address(aggregator_address)),
        data_id=data_id,
        updated_at=updated_at,
        bundle=bytes(bundle),
        block_number=block_number,
        block_timestamp=block_timestamp,
        transaction_hash=transaction_hash,
        log_index=log_index,
    )


async def fetch_chainlink_bundle_reports_hypersync_async(
    hypersync_client,
    *,
    aggregator_address: HexAddress | str,
    start_block: int,
    end_block: int,
    data_ids: set[bytes] | None = None,
    recv_timeout: float = 90.0,
) -> list[ChainlinkBundleReport]:
    """Fetch Chainlink bundle reports through Hypersync.

    The query targets the underlying DataFeedsCache address, not the bundle
    proxy: the cache emits reports while the proxy only forwards state reads.

    :param hypersync_client: Configured native or throttled Hypersync client.
    :param aggregator_address: DataFeedsCache contract emitting report events.
    :param start_block: Inclusive first block.
    :param end_block: Inclusive final block.
    :param data_ids: Optional set of 16-byte feed identifiers to include.
    :param recv_timeout: Maximum wait for each streamed response.
    :return: Reports sorted by block and log index.
    """

    if hypersync is None:
        message = "The hypersync package is required for Chainlink bundle history"
        raise ImportError(message)
    if start_block > end_block:
        raise ValueError(f"Bad Chainlink bundle block range: {start_block} - {end_block}")
    if data_ids is not None and not data_ids:
        return []

    topics: list[list[str]] = [[BUNDLE_REPORT_UPDATED_TOPIC0]]
    if data_ids is not None:
        topics.append([encode_bundle_data_id_topic(data_id) for data_id in sorted(data_ids)])
    query = hypersync.Query(
        from_block=start_block,
        to_block=end_block + 1,
        logs=[hypersync.LogSelection(address=[str(aggregator_address).lower()], topics=topics)],
        field_selection=hypersync.FieldSelection(
            block=[BlockField.NUMBER, BlockField.TIMESTAMP],
            log=[LogField.BLOCK_NUMBER, LogField.LOG_INDEX, LogField.ADDRESS, LogField.TRANSACTION_HASH, LogField.TOPIC0, LogField.TOPIC1, LogField.TOPIC2, LogField.DATA],
        ),
    )

    receiver = await open_hypersync_stream(hypersync_client, query)
    reports: list[ChainlinkBundleReport] = []
    while True:
        response = await asyncio.wait_for(receiver.recv(), timeout=recv_timeout)
        if response is None:
            break
        block_timestamps = {_decode_hypersync_int(block.number): from_unix_timestamp(_decode_hypersync_int(block.timestamp)) for block in response.data.blocks or [] if block.number is not None and block.timestamp is not None}
        for log in response.data.logs or []:
            block_number = _decode_hypersync_int(log.block_number)
            reports.append(
                decode_bundle_report_event(
                    aggregator_address=log.address,
                    topics=log.topics,
                    data=log.data or "0x",
                    block_number=block_number,
                    block_timestamp=block_timestamps.get(block_number),
                    transaction_hash=log.transaction_hash,
                    log_index=_decode_hypersync_int(log.log_index),
                )
            )
    reports.sort(key=lambda report: (report.block_number, report.log_index))
    return reports


def fetch_chainlink_bundle_reports_hypersync(
    hypersync_client,
    *,
    aggregator_address: HexAddress | str,
    start_block: int,
    end_block: int,
    data_ids: set[bytes] | None = None,
    recv_timeout: float = 90.0,
) -> list[ChainlinkBundleReport]:
    """Synchronously fetch Chainlink bundle reports through Hypersync.

    :param hypersync_client: Configured native or throttled Hypersync client.
    :param aggregator_address: DataFeedsCache contract emitting report events.
    :param start_block: Inclusive first block.
    :param end_block: Inclusive final block.
    :param data_ids: Optional set of 16-byte feed identifiers to include.
    :param recv_timeout: Maximum wait for each streamed response.
    :return: Reports sorted by block and log index.
    """

    coroutine = fetch_chainlink_bundle_reports_hypersync_async(
        hypersync_client=hypersync_client,
        aggregator_address=aggregator_address,
        start_block=start_block,
        end_block=end_block,
        data_ids=data_ids,
        recv_timeout=recv_timeout,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coroutine).result()
