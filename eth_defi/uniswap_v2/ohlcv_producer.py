import datetime

from eth_defi.abi import get_contract
from eth_defi.event_reader.conversion import decode_data, convert_int256_bytes_to_int
from eth_defi.event_reader.logresult import LogResult, LogContext
from eth_defi.event_reader.reader import read_events_concurrent
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.event_reader.web3worker import create_thread_pool_executor

from ..ohlcv.ohlcv_producer import OHLCVProducer


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


class UniswapV2OHLCVProducer(OHLCVProducer):
    """Uniswap v2 compatible DXE candle generator."""

    def __init__(self, web3_factory: TunedWeb3Factory, threads=16, chunk_size=100):
        self.web3_factory = web3_factory
        self.event_reader_context = LogContext()
        self.chunk_size = chunk_size
        self.executor = create_thread_pool_executor(self.web3_factory, self.event_reader_context, max_workers=threads)

        web3 = web3_factory(self.event_reader_context)

        # Get data from ABI
        Pair = get_contract(web3, "UniswapV2Pair.json")
        self.events_to_read = [Pair.events.Swap, Pair.events.Sync]

    def perform_duty_cycle(self):
        pass

    def update_block_range(self, start_block, end_block):
        """Read data between logs.

        :raise ChainReorganisationDetected:
            In the case we notice chain data has changed during the reading
        """

        events = []

        # Read specified events in block range
        for log_result in read_events_concurrent(
            self.executor,
            start_block,
            end_block,
            self.events_to_read,
            None,
            chunk_size=self.chunk_size,
            context=self.event_reader_context,
        ):
            if log_result["event"].event_name == "Swap":
                events.append(decode_swap(log_result))
            elif log_result["event"].event_name == "Sync":
                events.append(decode_swap(log_result))




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
        "block_number": int(log["blockNumber"], 16),
        "timestamp": block_time.isoformat(),
        "tx_hash": log["transactionHash"],
        "log_index": int(log["logIndex"], 16),
        "pair_contract_address": pair_contract_address,
        "reserve0": convert_int256_bytes_to_int(reserve0),
        "reserve1": convert_int256_bytes_to_int(reserve1),
    }
    return data






