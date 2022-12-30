"""Live Uniswap v2 swap event monitor.

- This is an modified example of `read-uniswap-v2-pairs-and-swaps.py` to support
  chain reorganisations, thus suitable for live event reading.

- It will print out live trade events for Uniswap v2 compatible exchange

- This will also show how to track block headers on disk,
  so that next start up is faster

- This is a dummy example just showing how to build the live loop,
  because how stores are constructed it is not good for processing
  actual data

"""
import datetime
import os
import time
from pathlib import Path
import logging

import requests

from tqdm import tqdm

from web3 import HTTPProvider, Web3

from eth_defi.abi import get_contract
from eth_defi.event_reader.block_time import measure_block_time
from eth_defi.event_reader.conversion import convert_uint256_string_to_address, convert_uint256_bytes_to_address, \
    decode_data, convert_int256_bytes_to_int, convert_jsonrpc_value_to_int
from eth_defi.event_reader.csv_block_data_store import CSVDatasetBlockDataStore
from eth_defi.event_reader.fast_json_rpc import patch_web3
from eth_defi.event_reader.logresult import LogContext
from eth_defi.event_reader.reader import read_events, LogResult
from eth_defi.event_reader.reorganisation_monitor import ReorganisationMonitor, ChainReorganisationDetected
from eth_defi.token import fetch_erc20_details, TokenDetails


#: List of output columns to pairs.csv
PAIR_FIELD_NAMES = [
    'block_number',
    'timestamp',
    'tx_hash',
    'log_index',
    'factory_contract_address',
    'pair_contract_address',
    'pair_count_index',
    'token0_address',
    'token0_symbol',
    'token1_address',
    'token1_symbol',
]

#: List of output columns to swaps.csv
SWAP_FIELD_NAMES = [
    'block_number',
    'timestamp',
    'tx_hash',
    'log_index',
    'pair_contract_address',
    "amount0_in",
    "amount1_in",
    "amount0_out",
    "amount1_out",
]


logger = logging.getLogger(__name__)


class TokenCache(LogContext):
    """Manage cache of token data when doing PairCreated look-up.

    Do not do extra requests for already known tokens.
    """

    def __init__(self):
        self.cache = {}

    def get_token_info(self, web3: Web3, address: str) -> TokenDetails:
        if address not in self.cache:
            self.cache[address] = fetch_erc20_details(web3, address, raise_on_error=False)
        return self.cache[address]


def save_state(state_fname, last_block):
    """Saves the last block we have read."""
    with open(state_fname, "wt") as f:
        print(f"{last_block}", file=f)


def restore_state(state_fname, default_block: int) -> int:
    """Restore the last block we have processes."""
    if os.path.exists(state_fname):
        with open(state_fname, "rt") as f:
            last_block_text = f.read()
            return int(last_block_text)

    return default_block


def decode_pair_created(log: LogResult) -> dict:
    """Process a pair created event.

    This function does manually optimised high speed decoding of the event.

    The event signature is:

    .. code-block::

        event PairCreated(address indexed token0, address indexed token1, address pair, uint);
    """

    # The raw log result looks like
    # {'address': '0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f', 'blockHash': '0x359d1dc4f14f9a07cba3ae8416958978ce98f78ad7b8d505925dad9722081f04', 'blockNumber': '0x98b723', 'data': '0x000000000000000000000000b4e16d0168e52d35cacd2c6185b44281ec28c9dc0000000000000000000000000000000000000000000000000000000000000001', 'logIndex': '0x22', 'removed': False, 'topics': ['0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9', '0x000000000000000000000000a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', '0x000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'], 'transactionHash': '0xd07cbde817318492092cc7a27b3064a69bd893c01cb593d6029683ffd290ab3a', 'transactionIndex': '0x26', 'event': <class 'web3._utils.datatypes.PairCreated'>, 'timestamp': 1588710145}

    # Do additional lookup for the token data
    web3 = log["event"].web3
    token_cache: TokenCache = log["context"]

    block_time = datetime.datetime.utcfromtimestamp(log["timestamp"])

    # Any indexed Solidity event parameter will be in topics data.
    # The first topics (0) is always the event signature.
    token0_address = convert_uint256_string_to_address(log["topics"][1])
    token1_address = convert_uint256_string_to_address(log["topics"][2])

    factory_address = log["address"]

    # Chop data blob to byte32 entries
    data_entries = decode_data(log["data"])

    # Any non-indexed Solidity event parameter will be in the data section.
    pair_contract_address = convert_uint256_bytes_to_address(data_entries[0])
    pair_count = int.from_bytes(data_entries[1], "big")

    # Now enhanche data with token information
    token0 = token_cache.get_token_info(web3, token0_address)
    token1 = token_cache.get_token_info(web3, token1_address)

    data = {
        "block_number": convert_jsonrpc_value_to_int(log["blockNumber"]),
        "timestamp": block_time.isoformat(),
        "tx_hash": log["transactionHash"],
        "log_index": int(log["logIndex"], 16),
        "factory_contract_address": factory_address,
        "pair_contract_address": pair_contract_address,
        "pair_count_index": pair_count,
        "token0_symbol": token0.symbol,
        "token0_address": token0_address,
        "token1_symbol": token1.symbol,
        "token1_address": token1_address,
    }
    return data


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
        "block_number": convert_jsonrpc_value_to_int(log["blockNumber"]),
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


def setup_logging():
    logging.basicConfig(level=os.environ["LOG_LEVEL"], handlers=[logging.StreamHandler()])

    # Mute noise
    logging.getLogger("web3.providers.HTTPProvider").setLevel(logging.WARNING)
    logging.getLogger("web3.RequestManager").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)


def main():

    setup_logging()

    # HTTP 1.1 keep-alive
    session = requests.Session()

    json_rpc_url = os.environ["JSON_RPC_URL"]
    web3 = Web3(HTTPProvider(json_rpc_url, session=session))

    # Enable faster ujson reads
    patch_web3(web3)

    web3.middleware_onion.clear()

    # Get contracts
    Factory = get_contract(web3, "UniswapV2Factory.json")
    Pair = get_contract(web3, "UniswapV2Pair.json")

    events = [
        Factory.events.PairCreated,  # https://etherscan.io/txs?ea=0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f&topic0=0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9
        Pair.events.Swap
    ]

    block_store = CSVDatasetBlockDataStore(Path("/tmp/uni-v2-last-block-state.csv"))

    reorg_mon = ReorganisationMonitor()
    if not block_store.is_virgin():
        block_header_df = block_store.load()
        reorg_mon.load_pandas(block_header_df)
        logger.info("Loaded %d existing blocks", len(block_header_df))
    else:
        logger.info("Starting with fresh block header store")

    initial_block_depth = 10

    # Do the initial buffering of the blocks
    reorg_mon.load_initial_block_headers(initial_block_depth, tqdm=tqdm)

    # Block time can be between 3 seconds to 12 seconds depending on
    # the EVM chain
    block_time = measure_block_time(web3)

    token_cache = TokenCache()

    while True:

        try:
            chain_reorg_resolution = reorg_mon.update_chain()

            if chain_reorg_resolution:
                logger.info(f"Chain reorganisation updated: {chain_reorg_resolution}")

            # Read specified events in block range
            for log_result in read_events(
                    web3,
                    start_block=chain_reorg_resolution.latest_block_with_good_data,
                    end_block=chain_reorg_resolution.last_live_block,
                    events=events,
                    notify=None,
                    chunk_size=100,
                    context=token_cache,
            ):
                if log_result["event"].event_name == "PairCreated":
                    logger.info(f"New pair created: {log_result}")
                elif log_result["event"].event_name == "Swap":
                    logger.info(f"Swap detected: {log_result}")
                else:
                    raise NotImplementedError()
        except ChainReorganisationDetected as e:
            # Chain reorganisation was detected during reading the events.
            # reorg_mon.update_chain() will detect the fork and purge bad state
            logger.warning("Chain reorg event raised: %s", e)

        # Save the current block headers
        df = reorg_mon.to_pandas()
        block_store.save(df)

        time.sleep(block_time)


if __name__ == "__main__":
    main()
