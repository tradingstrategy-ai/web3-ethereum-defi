"""Read GMX V2 liquidity-provider value-and-supply events.

The ratio of value to corresponding supply provides a sparse USD share-price
equivalent without treating a change in TVL or share count as profit. GM emits
``MarketPoolValueUpdated`` after a deposit and GLV emits ``GlvValueUpdated``
after execution. GM withdrawal observations are deliberately excluded because
GMX values deposits and withdrawals with different PnL-factor contexts. The
common vault scanner later applies its normal change threshold.
"""

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from eth_abi import encode
from eth_typing import HexAddress
from eth_utils import keccak, to_checksum_address
from hexbytes import HexBytes
from web3 import Web3

from eth_defi.gmx.events import EVENT_LOG1_SIGNATURE, GMXEventData
from eth_defi.hypersync.session import open_hypersync_stream
from eth_defi.vault.flow_events import decode_hypersync_int

try:
    import hypersync
    from hypersync import BlockField, LogField
except ImportError:
    hypersync = None


GMX_SHARE_PRICE_EVENT_NAMES = ("MarketPoolValueUpdated", "GlvValueUpdated")
GMX_DEPOSIT_ACTION_TYPE = keccak(encode(["string"], ["DEPOSIT"]))

#: Non-indexed ``EventLog1`` inputs. Direct decoding avoids materialising all
#: seven generic GMX key/value families through Web3's event wrapper.
GMX_EVENT_LOG1_DATA_TYPES = (
    "address",
    "string",
    "(((string,address)[],(string,address[])[]),((string,uint256)[],(string,uint256[])[]),((string,int256)[],(string,int256[])[]),((string,bool)[],(string,bool[])[]),((string,bytes32)[],(string,bytes32[])[]),((string,bytes)[],(string,bytes[])[]),((string,string)[],(string,string[])[]))",
)


def encode_address_as_event_topic(address: HexAddress) -> str:
    """Encode an EVM address as a GMX ``EventLog1`` topic.

    :param address:
        GM or GLV product address.
    :return:
        Lower-case, zero-padded 32-byte topic.
    """

    return f"0x{'0' * 24}{to_checksum_address(address)[2:].lower()}"


def decode_historical_share_price_event_data(web3: Web3, data: str | bytes, product_topic: str | bytes) -> GMXEventData:
    """Decode the GMX ``EventLog1`` fields needed for LP valuation.

    :param web3:
        Web3 instance providing the ABI codec.
    :param data:
        Raw non-indexed ``EventLog1`` payload.
    :param product_topic:
        Indexed GM or GLV address topic.
    :return:
        Minimal decoded event data.
    """

    _, event_name, event_data = web3.codec.decode(GMX_EVENT_LOG1_DATA_TYPES, HexBytes(data))
    product_address = to_checksum_address(HexBytes(product_topic)[-20:])
    if event_name == "MarketPoolValueUpdated":
        address_items = {"market": product_address}
    elif event_name == "GlvValueUpdated":
        address_items = {"glv": product_address}
    else:
        raise ValueError(f"Unexpected GMX historical share-price event {event_name}")
    return GMXEventData(
        event_name=event_name,
        address_items=address_items,
        uint_items=dict(event_data[1][0]),
        int_items=dict(event_data[2][0]),
        bytes32_items=dict(event_data[4][0]),
    )


@dataclass(slots=True, frozen=True)
class GMXHistoricalSharePriceObservation:
    """One GM or GLV value-and-supply observation.

    ``MarketPoolValueUpdated`` contains GM value and supply after a deposit.
    Withdrawal-context observations are excluded because GMX uses different
    valuation parameters for withdrawals. ``GlvValueUpdated`` contains GLV
    value and supply after execution; GLV minting and burning use the pre-flow
    ratio, so updating both numerator and denominator does not mechanically
    rebase the existing holders' claim.

    :param chain_id:
        EVM chain where GMX emitted the observation.
    :param block_number:
        Block containing the valuation event.
    :param block_timestamp:
        Unix timestamp of the valuation block.
    :param transaction_hash:
        Emitting transaction hash.
    :param log_index:
        Log position inside its block.
    :param product_address:
        GM market token or GLV share token.
    :param raw_value:
        USD GM market or GLV value using GMX's 30-decimal precision.
    :param raw_supply:
        ERC-20 share supply using 18-decimal precision.
    :param event_name:
        ``MarketPoolValueUpdated`` or ``GlvValueUpdated``.
    """

    chain_id: int
    block_number: int
    block_timestamp: int
    transaction_hash: str
    log_index: int
    product_address: HexAddress
    raw_value: int
    raw_supply: int
    event_name: str

    @property
    def source_observation_id(self) -> str:
        """Return a stable identifier for persistence and retries."""

        return f"{self.block_number}:{self.transaction_hash.lower()}:{self.log_index}"

    @property
    def total_assets(self) -> Decimal:
        """Return the USD product value in decimal units."""

        return Decimal(self.raw_value) / Decimal(10**30)

    @property
    def total_supply(self) -> Decimal:
        """Return the ERC-20 share supply in decimal units."""

        return Decimal(self.raw_supply) / Decimal(10**18)

    @property
    def share_price(self) -> Decimal:
        """Return the supply-normalised USD value per share."""

        return self.total_assets / self.total_supply


def extract_historical_share_price_observation(
    event: GMXEventData,
    *,
    chain_id: int,
    block_number: int,
    block_timestamp: int,
    transaction_hash: str,
    log_index: int,
) -> GMXHistoricalSharePriceObservation | None:
    """Convert a GMX value event into a share-price observation.

    The canonical event definitions are in GMX's `MarketEventUtils.sol
    <https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/market/MarketEventUtils.sol>`__
    and `GlvEventUtils.sol
    <https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/glv/GlvEventUtils.sol>`__.

    :param event:
        Decoded GMX EventEmitter payload.
    :param chain_id:
        Chain represented by the source stream.
    :param block_number:
        Emitting block number.
    :param block_timestamp:
        Unix timestamp of the emitting block.
    :param transaction_hash:
        Emitting transaction hash.
    :param log_index:
        Log position inside its block.
    :return:
        Complete value-and-supply observation, or ``None`` for another event
        or a non-positive value/supply pair.
    """

    if event.event_name == "MarketPoolValueUpdated":
        if event.get_bytes32("actionType") != GMX_DEPOSIT_ACTION_TYPE:
            return None
        product_address = event.get_address("market")
        raw_value = event.get_int("poolValue")
        raw_supply = event.get_uint("marketTokensSupply")
    elif event.event_name == "GlvValueUpdated":
        product_address = event.get_address("glv")
        raw_value = event.get_uint("value")
        raw_supply = event.get_uint("supply")
    else:
        return None

    if product_address is None or raw_value is None or raw_supply is None:
        raise ValueError(f"Incomplete GMX {event.event_name} value event")
    if raw_value <= 0 or raw_supply <= 0:
        return None
    return GMXHistoricalSharePriceObservation(
        chain_id=chain_id,
        block_number=block_number,
        block_timestamp=block_timestamp,
        transaction_hash=transaction_hash,
        log_index=log_index,
        product_address=to_checksum_address(product_address),
        raw_value=raw_value,
        raw_supply=raw_supply,
        event_name=event.event_name,
    )


async def _fetch_historical_share_price_observations_hypersync_async(
    *,
    hypersync_client,
    web3: Web3,
    chain_id: int,
    event_emitter_address: HexAddress,
    start_block: int,
    end_block: int,
    product_addresses: Iterable[HexAddress] | None = None,
) -> list[GMXHistoricalSharePriceObservation]:
    """Fetch GM and GLV value events from one half-open block range."""

    assert hypersync is not None, "hypersync package is required to fetch GMX historical value events"
    assert 0 <= start_block < end_block, f"Bad half-open block range: [{start_block}, {end_block})"
    event_emitter_address = to_checksum_address(event_emitter_address)
    signature = EVENT_LOG1_SIGNATURE if EVENT_LOG1_SIGNATURE.startswith("0x") else f"0x{EVENT_LOG1_SIGNATURE}"
    topics = [[signature], [f"0x{keccak(text=name).hex()}" for name in GMX_SHARE_PRICE_EVENT_NAMES]]
    if product_addresses is not None:
        product_topics = sorted({encode_address_as_event_topic(address) for address in product_addresses})
        if not product_topics:
            return []
        topics.append(product_topics)

    query = hypersync.Query(
        from_block=start_block,
        to_block=end_block,
        logs=[hypersync.LogSelection(address=[event_emitter_address.lower()], topics=topics)],
        field_selection=hypersync.FieldSelection(
            block=[BlockField.NUMBER, BlockField.TIMESTAMP],
            log=[LogField.BLOCK_NUMBER, LogField.LOG_INDEX, LogField.TRANSACTION_HASH, LogField.TOPIC0, LogField.TOPIC1, LogField.TOPIC2, LogField.DATA],
        ),
    )
    observations: list[GMXHistoricalSharePriceObservation] = []
    receiver = await open_hypersync_stream(hypersync_client, query)
    while response := await receiver.recv():
        block_timestamps = {decode_hypersync_int(block.number): decode_hypersync_int(block.timestamp) for block in response.data.blocks or [] if block.number is not None and block.timestamp is not None}
        for log in response.data.logs or []:
            block_number = decode_hypersync_int(log.block_number)
            block_timestamp = block_timestamps.get(block_number)
            if block_timestamp is None:
                raise ValueError(f"Hypersync did not return a timestamp for GMX value block {block_number}")
            observation = extract_historical_share_price_observation(
                decode_historical_share_price_event_data(web3, log.data, log.topics[2]),
                chain_id=chain_id,
                block_number=block_number,
                block_timestamp=block_timestamp,
                transaction_hash=log.transaction_hash,
                log_index=decode_hypersync_int(log.log_index),
            )
            if observation is not None:
                observations.append(observation)
    observations.sort(key=lambda item: (item.block_number, item.log_index))
    return observations


def fetch_historical_share_price_observations_hypersync(
    *,
    hypersync_client,
    web3: Web3,
    chain_id: int,
    event_emitter_address: HexAddress,
    start_block: int,
    end_block: int,
    product_addresses: Iterable[HexAddress] | None = None,
) -> list[GMXHistoricalSharePriceObservation]:
    """Fetch GM and GLV value-and-supply observations.

    :param hypersync_client:
        Configured throttle-aware Hypersync client for the chain.
    :param web3:
        Web3 instance used to decode GMX EventEmitter payloads.
    :param chain_id:
        Chain represented by the stream.
    :param event_emitter_address:
        GMX EventEmitter deployment.
    :param start_block:
        Inclusive block boundary.
    :param end_block:
        Exclusive block boundary.
    :param product_addresses:
        Optional GM and GLV addresses selected at the indexed-log level.
    :return:
        Chronologically ordered observations.
    """

    return asyncio.run(
        _fetch_historical_share_price_observations_hypersync_async(
            hypersync_client=hypersync_client,
            web3=web3,
            chain_id=chain_id,
            event_emitter_address=event_emitter_address,
            start_block=start_block,
            end_block=end_block,
            product_addresses=product_addresses,
        ),
    )
