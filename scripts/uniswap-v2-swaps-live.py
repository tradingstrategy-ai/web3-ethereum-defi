"""Live Uniswap v2 swap event monitor with chain reorganisation detection.

This is an example code for showing live swaps happening
on Uniswap v2 compatible examples. In this example
we use QuickSwap (Polygon) because Polygon provides
good free RPC nodes.

- This example runs on free Polygon JSON-RPC nodes,
  you do not need any self-hosted or commercial node service providers.

- This is an modified example of `read-uniswap-v2-pairs-and-swaps.py` to gracefully handle  chain reorganisations, thus the code is suitable for live event reading. It should also support low quality JSON-RPC nodes that may give different replies between API requests.

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
    export JSON_RPC_POLYGON="https://polygon-rpc.com"
    python scripts/read-uniswap-v2-swaps-live.py

"""

import datetime
import os
import time
from functools import lru_cache
from pathlib import Path
import logging

import coloredlogs
import requests
from tqdm import tqdm

from web3 import HTTPProvider, Web3

from eth_defi.abi import get_contract
from eth_defi.chain import install_chain_middleware, install_retry_middleware, install_api_call_counter_middleware
from eth_defi.event_reader.block_time import measure_block_time
from eth_defi.event_reader.conversion import decode_data, convert_int256_bytes_to_int, convert_jsonrpc_value_to_int
from eth_defi.event_reader.csv_block_data_store import CSVDatasetBlockDataStore
from eth_defi.event_reader.fast_json_rpc import patch_web3
from eth_defi.event_reader.reader import read_events, LogResult, prepare_filter
from eth_defi.event_reader.reorganisation_monitor import ChainReorganisationDetected, JSONRPCReorganisationMonitor
from eth_defi.uniswap_v2.pair import PairDetails, fetch_pair_details


logger = logging.getLogger(__name__)


@lru_cache(maxsize=256)
def fetch_pair_details_cached(web3: Web3, pair_address: str) -> PairDetails:
    """In-process memory cache for getting pair data in decoded format."""
    return fetch_pair_details(web3, pair_address)


def decode_swap(web3: Web3, log: LogResult) -> dict:
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

    block_time = native_datetime_utc_fromtimestamp(log["timestamp"])

    pair_contract_address = log["address"]

    pair_details = fetch_pair_details_cached(web3, pair_contract_address)

    # Optimised decode path for Uniswap v2 event data
    amount0_in, amount1_in, amount0_out, amount1_out = decode_data(log["data"])

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
    """Entry point for the script"""

    # Mute extra logging output
    setup_logging()

    # HTTP 1.1 keep-alive to speed up JSON-RPC protocol
    session = requests.Session()

    json_rpc_url = os.environ["JSON_RPC_POLYGON"]
    web3 = Web3(HTTPProvider(json_rpc_url, session=session))

    # Enable faster JSON decoding with orjson
    patch_web3(web3)

    web3.middleware_onion.clear()

    # Setup Polygon middleware support
    install_chain_middleware(web3)

    # Setup support for retry after JSON-RPC endpoint starts throttling us
    install_retry_middleware(web3)

    # Count API requests
    api_request_counter = install_api_call_counter_middleware(web3)

    # Get contracts
    Factory = get_contract(web3, "sushi/UniswapV2Factory.json")
    Pair = get_contract(web3, "sushi/UniswapV2Pair.json")

    # Create a filter that will match both PairCreaetd and Swap events
    # when reading tx receipts
    filter = prepare_filter([Factory.events.PairCreated, Pair.events.Swap])

    # Store block headers locally in a CSV file,
    # so we can speed up startup
    block_store = CSVDatasetBlockDataStore(Path("uni-v2-last-block-state.csv"))

    # Create a blockchain minor reorganisation detector,
    # so we can handle cases when the last block is rolled back
    reorg_mon = JSONRPCReorganisationMonitor(web3)

    if not block_store.is_virgin():
        # Start from the existing save point
        block_header_df = block_store.load()
        reorg_mon.load_pandas(block_header_df)
        logger.info("Loaded %d existing blocks from %s.\nIf the save checkpoint was long time ago, we need to catch up all blocks and it could be slow.", len(block_header_df), block_store.path)
    else:
        # Start from the scratch,
        # use tqdm progess bar for interactive progress
        initial_block_count = 50
        logger.info("Starting with fresh block header store at %s, cold start fetching %d blocks", block_store.path, initial_block_count)
        reorg_mon.load_initial_block_headers(initial_block_count, tqdm=tqdm)

    # Block time can be between 3 seconds to 12 seconds depending on
    # the EVM chain
    block_time = measure_block_time(web3)

    total_reorgs = 0

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
                extract_timestamps=None,
                reorg_mon=reorg_mon,
            ):
                if log_result["event"].event_name == "PairCreated":
                    logger.info(f"New pair created: {log_result}")
                elif log_result["event"].event_name == "Swap":
                    swap = decode_swap(web3, log_result)
                    swap_fmt = format_swap(swap)
                    logger.info("%s", swap_fmt)
                else:
                    raise NotImplementedError()

            # Dump stats to the output regularly
            if time.time() > next_stat_print:
                req_count = api_request_counter["total"]
                logger.info("**STATS** Reorgs detected: %d, block headers buffered: %d, API requests made: %d", total_reorgs, len(reorg_mon.block_map), req_count)
                next_stat_print = time.time() + stat_delay

                # Save the current block headers on disk
                # to speed up the next start
                df = reorg_mon.to_pandas()
                block_store.save(df)

        except ChainReorganisationDetected as e:
            # Chain reorganisation was detected during reading the events.
            # reorg_mon.update_chain() will detect the fork and purge bad state
            total_reorgs += 1
            logger.warning("Chain reorg event raised: %s, we have now detected %d chain reorganisations.", e, total_reorgs)

        time.sleep(block_time)


if __name__ == "__main__":
    main()
