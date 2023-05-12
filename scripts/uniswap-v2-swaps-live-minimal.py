"""Minimal example of a chain reader with chain reorganisation detection.

.. code-block:: shell

    python scripts/read-uniswap-v2-pairs-and-swaps-live.py

"""

import os
import time

from web3 import HTTPProvider, Web3

from eth_defi.abi import get_contract
from eth_defi.chain import install_chain_middleware
from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.reader import read_events, LogResult
from eth_defi.event_reader.reorganisation_monitor import JSONRPCReorganisationMonitor


def main():
    json_rpc_url = os.environ.get("JSON_RPC_POLYGON", "https://polygon-rpc.com")
    web3 = Web3(HTTPProvider(json_rpc_url))
    web3.middleware_onion.clear()
    install_chain_middleware(web3)

    # Get contracts
    Pair = get_contract(web3, "sushi/UniswapV2Pair.json")

    filter = Filter.create_filter(address=None, event_types=[Pair.events.Swap])  # Listen events from any smart contract

    reorg_mon = JSONRPCReorganisationMonitor(web3, check_depth=3)

    # Get the headers of last 5 blocks before starting
    reorg_mon.load_initial_block_headers(block_count=5)

    processed_events = set()

    latest_block = reorg_mon.get_last_block_live()

    # Keep reading events as they land
    while True:
        chain_reorg_resolution = reorg_mon.update_chain()
        start, end = chain_reorg_resolution.get_read_range()

        if chain_reorg_resolution.reorg_detected:
            print("Chain reorg warning")

        evt: LogResult
        for evt in read_events(
            web3,
            start_block=start,
            end_block=end,
            filter=filter,
        ):
            # How to uniquely identify EVM logs
            key = evt["blockHash"] + evt["transactionHash"] + evt["logIndex"]

            # The reader may cause duplicate events as the chain tip reorganises
            if key not in processed_events:
                print(f"Swap at block:{evt['blockNumber']:,} tx:{evt['transactionHash']}")
                processed_events.add(key)
        else:
            print(".")

        if end != latest_block:
            for block_num in range(latest_block + 1, end + 1):
                block_data = reorg_mon.get_block_by_number(block_num)
                print(f"Block {block_num:,} is {block_data.block_hash}")

            latest_block = end

        time.sleep(0.5)


if __name__ == "__main__":
    main()
