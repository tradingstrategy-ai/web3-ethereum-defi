"""Read all Uniswap pairs and their swaps in a blockchain.

Overview:

- Stateful: Can resume operation after CTRL+C or crash

- Outputs two append only CSV files, `/tmp/uni-v2-pairs.csv` and `/tmp/uni-v2-swaps.csv`

- Iterates through all the events using `read_events()` generator

- Events can be pair creation or swap events

- For pair creation events, we perform additional token lookups using Web3 connection

- Demonstrates how to hand tune event decoding

- The first PairCreated event is at Ethereum mainnet block is 10000835

- The first swap event is at Ethereum mainnet block 10_008_566, 0x932cb88306450d481a0e43365a3ed832625b68f036e9887684ef6da594891366

- Uniswap v2 deployment transaction https://bloxy.info/tx/0xc31d7e7e85cab1d38ce1b8ac17e821ccd47dbde00f9d57f2bd8613bff9428396

To run:

.. code-block:: shell

    # Switch between INFO and DEBUG
    export LOG_LEVEL=INFO
    # Your Ethereum node RPC
    export JSON_RPC_URL="https://xxx@vitalik.tradingstrategy.ai"
    python scripts/read-uniswap-v2-pairs-and-swaps.py


"""

import csv
import datetime
import os
import logging
from typing import Optional

import requests
from eth_defi.chain import install_chain_middleware

from tqdm import tqdm

from web3 import HTTPProvider, Web3

from eth_defi.abi import get_contract
from eth_defi.event_reader.conversion import convert_uint256_string_to_address, convert_uint256_bytes_to_address, decode_data, convert_int256_bytes_to_int, convert_jsonrpc_value_to_int
from eth_defi.event_reader.fast_json_rpc import patch_web3
from eth_defi.event_reader.logresult import LogContext
from eth_defi.event_reader.reader import read_events, LogResult
from eth_defi.token import fetch_erc20_details, TokenDetails

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


def decode_pair_created(web3: Web3, log: LogResult) -> dict:
    """Process a pair created event.

    This function does manually optimised high speed decoding of the event.

    The event signature is:

    .. code-block::

        event PairCreated(address indexed token0, address indexed token1, address pair, uint);
    """

    # The raw log result looks like
    # {'address': '0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f', 'blockHash': '0x359d1dc4f14f9a07cba3ae8416958978ce98f78ad7b8d505925dad9722081f04', 'blockNumber': '0x98b723', 'data': '0x000000000000000000000000b4e16d0168e52d35cacd2c6185b44281ec28c9dc0000000000000000000000000000000000000000000000000000000000000001', 'logIndex': '0x22', 'removed': False, 'topics': ['0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9', '0x000000000000000000000000a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', '0x000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'], 'transactionHash': '0xd07cbde817318492092cc7a27b3064a69bd893c01cb593d6029683ffd290ab3a', 'transactionIndex': '0x26', 'event': <class 'web3._utils.datatypes.PairCreated'>, 'timestamp': 1588710145}

    # Do additional lookup for the token data
    token_cache: TokenCache = log["context"]

    block_time = native_datetime_utc_fromtimestamp(log["timestamp"])

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

    block_time = native_datetime_utc_fromtimestamp(log["timestamp"])

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


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper(), handlers=[logging.StreamHandler()])

    # Mute noise
    logging.getLogger("web3.providers.HTTPProvider").setLevel(logging.WARNING)
    logging.getLogger("web3.RequestManager").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

    # HTTP 1.1 keep-alive
    session = requests.Session()

    json_rpc_url = os.environ["JSON_RPC_URL"]
    web3 = Web3(HTTPProvider(json_rpc_url, session=session))

    # Enable faster orjson reads
    patch_web3(web3)

    web3.middleware_onion.clear()

    install_chain_middleware(web3)

    # Get contracts
    Factory = get_contract(web3, "UniswapV2Factory.json")
    Pair = get_contract(web3, "UniswapV2Pair.json")

    events = [Factory.events.PairCreated, Pair.events.Swap]  # https://etherscan.io/txs?ea=0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f&topic0=0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9

    pairs_fname = "/tmp/uni-v2-pairs.csv"
    swaps_fname = "/tmp/uni-v2-swaps.csv"
    state_fname = "/tmp/uni-v2-last-block-state.txt"

    start_block = restore_state(state_fname, 10_000_835)  # # When Uni v2 was deployed
    end_block = web3.eth.block_number

    max_blocks = end_block - start_block

    pairs_event_buffer = []
    swaps_event_buffer = []

    token_cache = TokenCache()

    print(f"Starting to read block range {start_block:,} - {end_block:,}")

    with open(pairs_fname, "a") as pairs_out, open(swaps_fname, "a") as swaps_out:
        pairs_writer = csv.DictWriter(pairs_out, fieldnames=PAIR_FIELD_NAMES)
        swaps_writer = csv.DictWriter(swaps_out, fieldnames=SWAP_FIELD_NAMES)

        with tqdm(total=max_blocks) as progress_bar:
            #  1. Update the progress bar
            #  2. save any events in the buffer in to a file in one go
            def update_progress(current_block, start_block, end_block, chunk_size: int, total_events: int, last_timestamp: Optional[int], context: TokenCache):
                nonlocal pairs_event_buffer
                nonlocal swaps_event_buffer
                if last_timestamp:
                    # Display progress with the date information
                    d = native_datetime_utc_fromtimestamp(last_timestamp)
                    formatted_time = d.strftime("%d-%m-%Y")
                    progress_bar.set_description(f"Block: {current_block:,}, events: {total_events:}, time:{formatted_time}")
                else:
                    progress_bar.set_description(f"Block: {current_block:,}, events: {total_events:,}")
                progress_bar.update(chunk_size)

                # Output scanned events
                for entry in pairs_event_buffer:
                    pairs_writer.writerow(entry)

                for entry in swaps_event_buffer:
                    swaps_writer.writerow(entry)

                # Save the scan sate
                save_state(state_fname, current_block - 1)

                # Reset buffer
                pairs_event_buffer = []
                swaps_event_buffer = []

            # Read specified events in block range
            for log_result in read_events(
                web3,
                start_block,
                end_block,
                events,
                update_progress,
                chunk_size=100,
                context=token_cache,
            ):
                # We are getting two kinds of log entries, pairs and swaps.
                # Choose between where to store.
                try:
                    if log_result["event"].event_name == "PairCreated":
                        pairs_event_buffer.append(decode_pair_created(web3, log_result))
                    elif log_result["event"].event_name == "Swap":
                        swaps_event_buffer.append(decode_swap(log_result))
                except Exception as e:
                    raise RuntimeError(f"Could not decode {log_result}") from e


if __name__ == "__main__":
    main()
