"""Live Uniswap v2 swap event monitor with chain reorganisation detection.

- This example runs on free Polygon JSON-RPC nodes,
  you do not need any self-hosted or commercial node service providers.

- This is an modified example of `read-uniswap-v2-pairs-and-swaps.py` to support
  chain reorganisations, thus suitable for live event reading.

- It will print out live trade events for Uniswap v2 compatible exchange.

- This will also show how to track block headers on disk,
  so that next start up is faster.

- This is a dummy example just showing how to build the live loop,
  because how stores are constructed it is not good for processing
  actual data.

- Because pair and token details are dynamically fetched
  when a swap for a pair is encountered for the first time,
  the startup is a bit slow as the pair details cache
  is warming up.


To run for Polygon (and QuickSwap):

.. code-block:: shell

    # Need for nice output
    pip install coloredlogs

    # Switch between INFO and DEBUG
    export LOG_LEVEL=INFO
    # Your Ethereum node RPC
    export JSON_RPC_URL="https://polygon-rpc.com"
    python scripts/read-uniswap-v2-pairs-and-swaps-live.py


"""
import datetime
import os
import time
from pathlib import Path
import logging
from typing import Dict

import coloredlogs
import requests

from web3 import HTTPProvider, Web3

from eth_defi.abi import get_contract
from eth_defi.chain import install_chain_middleware
from eth_defi.event_reader.block_time import measure_block_time
from eth_defi.event_reader.conversion import decode_data, convert_int256_bytes_to_int, convert_jsonrpc_value_to_int
from eth_defi.event_reader.csv_block_data_store import CSVDatasetBlockDataStore
from eth_defi.event_reader.fast_json_rpc import patch_web3
from eth_defi.event_reader.logresult import LogContext
from eth_defi.event_reader.reader import read_events, LogResult, prepare_filter
from eth_defi.event_reader.reorganisation_monitor import ChainReorganisationDetected, JSONRPCReorganisationMonitor
from eth_defi.token import fetch_erc20_details, TokenDetails
from eth_defi.uniswap_v2.pair import PairDetails, fetch_pair_details


#: List of output columns to swaps.csv
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


logger = logging.getLogger(__name__)


class BlockchainStateCache(LogContext):
    """Manage cache of token and pair data.

    - Read data from the chain state

    - Store process in-memory for the duration of the session
    """

    def __init__(self, web3: Web3):
        self.web3 = web3
        self.token_cache: Dict[str, TokenDetails] = {}
        self.pair_cache: Dict[str, PairDetails] = {}

    def get_token_details(self, address: str) -> TokenDetails:
        if address not in self.token_cache:
            self.token_cache[address] = fetch_erc20_details(self.web3, address, raise_on_error=False)
        return self.token_cache[address]

    def get_pair_details(self, address: str) -> PairDetails:
        if address not in self.pair_cache:
            self.pair_cache[address] = fetch_pair_details(self.web3, address)
        return self.pair_cache[address]


def decode_swap(web3: Web3, cache: BlockchainStateCache, log: LogResult) -> dict:
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

    pair_details = cache.get_pair_details(Web3.toChecksumAddress(pair_contract_address))

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
        "pair_details": pair_details,
    }
    return data


def format_swap(swap: dict) -> str:
    """Write swap in human readable format.

    - Two simplified format for swaps

    - Complex output format for more complex swaps
    """

    pair: PairDetails = swap["pair_details"]
    token0 = pair.token0
    token1 = pair.token1
    tx_hash = swap["tx_hash"]
    block_number = swap["block_number"]

    if swap["amount0_in"] and not swap["amount1_in"]:
        token_in = token0
        token_out = token1
        amount_in = token0.convert_to_decimals(swap["amount0_in"])
        amount_out = token1.convert_to_decimals(swap["amount1_out"])
        return f"{block_number:,} {tx_hash} {amount_in} {token_in.symbol} -> {amount_out} {token_out.symbol}"
    elif swap["amount1_in"] and not swap["amount0_in"]:
        token_in = token1
        token_out = token0
        amount_in = token1.convert_to_decimals(swap["amount1_in"])
        amount_out = token0.convert_to_decimals(swap["amount0_out"])
        return f"{block_number:,} {tx_hash} {amount_in} {token_in.symbol} -> {amount_out} {token_out.symbol}"
    else:
        amount0_in = token0.convert_to_decimals(swap["amount0_in"])
        amount1_in = token1.convert_to_decimals(swap["amount1_in"])
        amount0_out = token0.convert_to_decimals(swap["amount0_out"])
        amount1_out = token1.convert_to_decimals(swap["amount1_out"])
        return f"{block_number:,} {tx_hash} {amount0_in} {token0.symbol}, {amount1_in} {token1.symbol} -> {amount0_out} {token0.symbol}, {amount1_out} {token1.symbol}"


def setup_logging():
    level = os.environ.get("LOG_LEVEL", "info").upper()

    fmt = "%(asctime)s %(name)-44s %(message)s"
    date_fmt = "%H:%M:%S"
    coloredlogs.install(level=level, fmt=fmt, date_fmt=date_fmt)

    logging.basicConfig(level=level, handlers=[logging.StreamHandler()])

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

    # Support Polygon
    install_chain_middleware(web3)

    # Get contracts
    Factory = get_contract(web3, "UniswapV2Factory.json")
    Pair = get_contract(web3, "UniswapV2Pair.json")

    events = [Factory.events.PairCreated, Pair.events.Swap]  # https://etherscan.io/txs?ea=0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f&topic0=0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9

    block_store = CSVDatasetBlockDataStore(Path("/tmp/uni-v2-last-block-state.csv"))

    reorg_mon = JSONRPCReorganisationMonitor(web3)
    if not block_store.is_virgin():
        block_header_df = block_store.load()
        reorg_mon.load_pandas(block_header_df)
        logger.info("Loaded %d existing blocks from %s", len(block_header_df), block_store.path)
    else:
        logger.info("Starting with fresh block header store at %s", block_store.path)

    # Block time can be between 3 seconds to 12 seconds depending on
    # the EVM chain
    block_time = measure_block_time(web3)

    token_cache = BlockchainStateCache(web3)

    total_reorgs = 0

    filter = prepare_filter(events)

    # Cache pair and token details read from the blockchain state
    cache = BlockchainStateCache(web3)

    stat_delay = 10
    next_stat_print = time.time() + stat_delay

    while True:

        try:

            # Figure out the next good unscanned block range,
            # and fetch block headers and timestamps for this block range
            chain_reorg_resolution = reorg_mon.update_chain()

            if chain_reorg_resolution.reorg_detected:
                logger.info(f"Chain reorganisation data updated: {chain_reorg_resolution}")

            # Read specified events in block range
            for log_result in read_events(
                web3,
                start_block=chain_reorg_resolution.latest_block_with_good_data + 1,
                end_block=chain_reorg_resolution.last_live_block,
                filter=filter,
                notify=None,
                chunk_size=100,
                context=token_cache,
                extract_timestamps=None,
                reorg_mon=reorg_mon,
            ):
                if log_result["event"].event_name == "PairCreated":
                    logger.info(f"New pair created: {log_result}")
                elif log_result["event"].event_name == "Swap":
                    swap = decode_swap(web3, cache, log_result)
                    swap_fmt = format_swap(swap)
                    logger.info("%s", swap_fmt)
                else:
                    raise NotImplementedError()

            # Dump stats to the output
            if time.time() > next_stat_print:
                logger.info("**STATS** Reorgs detected: %d, block headers buffered: %d, pairs cached: %d", total_reorgs, len(reorg_mon.block_map), len(cache.pair_cache))
                next_stat_print = time.time() + stat_delay

        except ChainReorganisationDetected as e:
            # Chain reorganisation was detected during reading the events.
            # reorg_mon.update_chain() will detect the fork and purge bad state
            total_reorgs += 1
            logger.warning("Chain reorg event raised: %s, we have now detected %d chain reorganisations.", e, total_reorgs)

        # Save the current block headers on disk
        df = reorg_mon.to_pandas()
        block_store.save(df)

        time.sleep(block_time)


if __name__ == "__main__":
    main()
