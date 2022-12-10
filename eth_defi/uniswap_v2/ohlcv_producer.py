import datetime
import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Dict, Tuple
import logging

import pandas as pd
from eth_defi.abi import get_contract
from eth_defi.event_reader.conversion import decode_data, convert_int256_bytes_to_int
from eth_defi.event_reader.logresult import LogResult, LogContext
from eth_defi.event_reader.reader import read_events_concurrent
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.event_reader.web3worker import create_thread_pool_executor
from eth_defi.uniswap_v2.pair import PairDetails

from ..ohlcv.ohlcv_producer import OHLCVProducer, Trade
from ..ohlcv.reorgmon import ReorganisationMonitor

logger = logging.getLogger(__name__)


#: List of output columns to pairs.csv
PAIR_FIELD_NAMES = [
    "block_number",
    "timestamp",
    "tx_hash",
    "log_index",
    "factory_contract_address",
    "pair_contract_address",
    "pair_count_index",
    "token0_address",
    "token0_symbol",
    "token1_address",
    "token1_symbol",
]

#: List of fields we need to decode in swaps
SWAP_FIELD_NAMES = [
    "block_number",
    "timestamp",
    "tx_hash",
    "log_index",
    "pair_contract_address",
    "amount0_in",
    "amount1_in",
    "amount0_out",
    "amount1_out",
]

#: List of fields we need to decode in syncs
SYNC_FIELD_NAMES = [
    "block_number",
    "timestamp",
    "tx_hash",
    "log_index",
    "pair_contract_address",
    "reserve0",
    "reserve1",
]


class SwapKind(enum.Enum):
    """What kind of swaps we might have."""

    # token1 -> token0
    buy = "buy"

    # token0 -> token1
    sell = "sell"

    # Traded both ways at the same time
    complex = "complex"

    # Zero traded volumne
    invalid = "invalid"


class UniswapV2OHLCVProducer(OHLCVProducer):
    """Uniswap v2 compatible DXE candle generator."""

    def __init__(self,
                 pairs: List[PairDetails],
                 web3_factory: TunedWeb3Factory,
                 oracles: Dict[str, PriceOracle],
                 reorg_mon: ReorganisationMonitor,
                 data_retention_time: Optional[pd.Timedelta] = None,
                 candle_size=pd.Timedelta(minutes=1),
                 threads=16,
                 chunk_size=100):

        super().__init__(
            oracles=oracles,
            reorg_mon=reorg_mon,
            data_retention_time=data_retention_time,
            candle_size=candle_size,
        )

        #: Pair address -> details mapping
        self.pair_map = Dict[str, PairDetails] = {p.address: p for p in pairs}
        self.web3_factory = web3_factory
        self.event_reader_context = LogContext()
        self.chunk_size = chunk_size
        self.executor = create_thread_pool_executor(self.web3_factory, self.event_reader_context, max_workers=threads)

        web3 = web3_factory(self.event_reader_context)

        # Get data from ABI
        Pair = get_contract(web3, "UniswapV2Pair.json")
        self.events_to_read = [Pair.events.Swap, Pair.events.Sync]

    def update_block_range(self, start_block, end_block):
        """Read data between logs.

        :raise ChainReorganisationDetected:
            In the case we notice chain data has changed during the reading
        """

        events = []

        def _extract_timestamps(web3, start_block, end_block):
            ts_map = {}
            for block_num in range(start_block, end_block+1):
                ts_map[block_num] = self.reorg_mon.get_block_timestamp(block_num)
            return ts_map

        # Read specified events in block range
        for log_result in read_events_concurrent(
            self.executor,
            start_block,
            end_block,
            self.events_to_read,
            None,
            chunk_size=self.chunk_size,
            context=self.event_reader_context,
            extract_timestamps=_extract_timestamps,
        ):
            if log_result["event"].event_name == "Swap":
                events.append(decode_swap(log_result))
            elif log_result["event"].event_name == "Sync":
                events.append(decode_swap(log_result))

        trades = self.map_uniswap_v2_events(events)
        self.add_trades(trades)

    def map_uniswap_v2_events(self, events: List[dict]) -> List[Trade]:
        """Figure out Uniswap v2 swap and volume."""

        prev_sync = None
        trades = []
        for evt in events:
            if evt["type"] == "sync":
                native_price = Decimal(evt["reserve0"]) / Decimal(evt["reserve1"])
                prev_sync = evt
            else:
                # Swap
                tx_hash = evt["tx_hash"]
                if prev_sync["tx_hash"] != tx_hash:
                    logger.debug("Current sync and swap do not follow Uniswap logic: %s - %s", prev_sync, evt)

                pair: PairDetails
                pair = self.pair_map[evt["pair_contract_address"]]

                price, amount = calculate_reserve_price_in_quote_token_raw(
                    pair.reverse_token_order,
                    prev_sync["reserve0"],
                    prev_sync["reserve1"],
                    evt["amount0_in"],
                    evt["amount1_in"],
                    evt["amount0_out"],
                    evt["amount1_out"],
                )

                timestamp = self.reorg_mon.get_block_timestamp(evt["block_number"])

                t = Trade(
                    pair=pair.address,
                    block_number=evt["block_number"],
                    block_hash=evt["block_hash"],
                    log_index=evt["log_index"],
                    tx_hash=evt["tx_hash"],
                    timestamp=pd.Timestamp.utcfromtimestamp(timestamp),
                    price=price,
                    amount=amount,
                )
                trades.append(t)
        return trades


def decode_swap(log: LogResult) -> dict:
    """Process swap event.

    This function does manually optimised high speed decoding of the event.

    The event signature is:

    .. code-block::

        event Swap(
          address indexed sender,
          uint amount0In,
          uint amount1In,
          uint amount0Out,
          uint amount1Out,
          address indexed to
        );
    """

    # Raw example event
    # {'address': '0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc', 'blockHash': '0x4ba33a650f9e3d8430f94b61a382e60490ec7a06c2f4441ecf225858ec748b78', 'blockNumber': '0x98b7f6', 'data': '0x00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000046ec814a2e900000000000000000000000000000000000000000000000000000000000003e80000000000000000000000000000000000000000000000000000000000000000', 'logIndex': '0x4', 'removed': False, 'topics': ['0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822', '0x000000000000000000000000f164fc0ec4e93095b804a4795bbe1e041497b92a', '0x0000000000000000000000008688a84fcfd84d8f78020d0fc0b35987cc58911f'], 'transactionHash': '0x932cb88306450d481a0e43365a3ed832625b68f036e9887684ef6da594891366', 'transactionIndex': '0x1', 'context': <__main__.TokenCache object at 0x104ab7e20>, 'event': <class 'web3._utils.datatypes.Swap'>, 'timestamp': 1588712972}

    block_time = datetime.datetime.utcfromtimestamp(log["timestamp"])

    pair_contract_address = log["address"]

    # Chop data blob to byte32 entries
    data_entries = decode_data(log["data"])

    amount0_in, amount1_in, amount0_out, amount1_out = data_entries

    data = {
        "type": "sync",
        "block_number": int(log["blockNumber"], 16),
        "timestamp": block_time.isoformat(),
        "tx_hash": log["transactionHash"],
        "log_index": int(log["logIndex"], 16),
        "pair_contract_address": pair_contract_address,
        "amount0_in": convert_int256_bytes_to_int(amount0_in),
        "amount1_in": convert_int256_bytes_to_int(amount1_in),
        "amount0_out": convert_int256_bytes_to_int(amount0_out),
        "amount1_out": convert_int256_bytes_to_int(amount1_out),
    }
    return data


def decode_sync(log: LogResult) -> dict:
    """Process sync event.

    This function does manually optimised high speed decoding of the event.

    The event signature is:

    .. code-block::

        event Sync(uint112 reserve0, uint112 reserve1);
    """

    # Raw example event
    # {'address': '0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc', 'blockHash': '0x4ba33a650f9e3d8430f94b61a382e60490ec7a06c2f4441ecf225858ec748b78', 'blockNumber': '0x98b7f6', 'data': '0x00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000046ec814a2e900000000000000000000000000000000000000000000000000000000000003e80000000000000000000000000000000000000000000000000000000000000000', 'logIndex': '0x4', 'removed': False, 'topics': ['0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822', '0x000000000000000000000000f164fc0ec4e93095b804a4795bbe1e041497b92a', '0x0000000000000000000000008688a84fcfd84d8f78020d0fc0b35987cc58911f'], 'transactionHash': '0x932cb88306450d481a0e43365a3ed832625b68f036e9887684ef6da594891366', 'transactionIndex': '0x1', 'context': <__main__.TokenCache object at 0x104ab7e20>, 'event': <class 'web3._utils.datatypes.Swap'>, 'timestamp': 1588712972}

    block_time = datetime.datetime.utcfromtimestamp(log["timestamp"])

    pair_contract_address = log["address"]

    # Chop data blob to byte32 entries
    data_entries = decode_data(log["data"])

    reserve0, reserve1 = data_entries

    data = {
        "type": "sync",
        "block_number": int(log["blockNumber"], 16),
        "timestamp": block_time.isoformat(),
        "tx_hash": log["transactionHash"],
        "log_index": int(log["logIndex"], 16),
        "pair_contract_address": pair_contract_address,
        "reserve0": convert_int256_bytes_to_int(reserve0),
        "reserve1": convert_int256_bytes_to_int(reserve1),
    }
    return data


def calculate_reserve_price_in_quote_token_raw(
        reversed: bool,
        reserve0: int,
        reserve1: int,
        amount0_in,
        amount1_in,
        amount0_out,
        amount1_out,
) -> Tuple[Decimal, Decimal]:
    """Calculate the market price based on Uniswap pool reserve0 an reserve1.

    :param reversed:
        Determine base, quote token order relative to token0, token1.
        If reversed, quote token is token0, else quote token is token0.

    :return:
        Price in quote token, amount in quote token
    """

    assert reserve0 > 0, f"Bad reserves {reserve0}, {reserve1}"
    assert reserve1 > 0, f"Bad reserves {reserve0}, {reserve1}"

    if reversed:
        reserve0, reserve1 = reserve1, reserve0

    if reversed:
        quote_amount = (amount0_out - amount0_in)
        base_amount = (amount1_out - amount1_in)
    else:
        base_amount = (amount0_out - amount0_in)
        quote_amount = (amount1_out - amount1_in)

    price = Decimal(reserve1) / Decimal(reserve0)

    volume = Decimal(quote_amount)

    return price, volume




