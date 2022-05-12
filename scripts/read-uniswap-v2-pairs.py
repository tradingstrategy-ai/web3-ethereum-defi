"""Read all Uniswap pairs and their swaps in a blockchain.

- Stateful: Can resume operation after CTRL+C or crash

- Outputs to append only CSV file

To run:

.. code-block:: shell

    # Your Ethereum node RPC
    export JSON_RPC_URL="https://xxx@vitalik.tradingstrategy.ai"
    python scripts/read-uniswap-v2-pairs.py


"""
import csv
import os
import logging
from dataclasses import dataclass, field
from typing import List

import pyarrow as pa
import requests
from dataclasses_json import dataclass_json
from tqdm import tqdm
from tradingstrategy.client import Client

#: Our API key to access the dataset
from tradingstrategy.pair import PairUniverse, DEXPair
from web3 import HTTPProvider, Web3

from eth_defi.abi import get_deployed_contract, get_contract
from eth_defi.block_reader.fastjsonrpc import patch_web3
from eth_defi.block_reader.reader import read_events


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


def main():

    logging.basicConfig(level=logging.DEBUG, handlers=[logging.StreamHandler()])

    # Mute noise
    logging.getLogger("web3.providers.HTTPProvider").setLevel(logging.WARNING)
    logging.getLogger("web3.RequestManager").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

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
        Pair.events.Swap  #
    ]

    output_fname = "/tmp/uni-v2-events.csv"
    state_fname = "/tmp/uni-v2-last-block-state.txt"

    start_block = restore_state(state_fname, 9_900_000)  # # When Uni v2 was deployed
    end_block = web3.eth.block_number

    max_blocks = end_block - start_block

    buffer = []

    field_names = [
        'block_num',
        'block_timestamp',
        'tx_hash',
        'log_index',
        'args',
    ]

    print(f"Starting to read block range {start_block:,} - {end_block:,}")

    # Prepare CSV append wr
    with open(output_fname, 'a') as f:

        writer = csv.DictWriter(f, fieldnames=field_names)

        with tqdm(max_blocks) as progress_bar:

            # 1. Update the progress bar
            # 2. save any events in the buffer in to a file in one go
            def update_progress(current_block, start_block,  end_block, chunk_size: int, total_events: int):
                nonlocal buffer
                progress_bar.set_description(f"Block: {current_block:,}, events: {total_events:,}")
                progress_bar.update(chunk_size)

                # Save the progress
                for entry in buffer:
                    writer.writerow(entry)

                save_state(state_fname, current_block-1)
                buffer = []

            # Read specified events in block range
            for event in read_events(web3, start_block, end_block - 1, events, update_progress, chunk_size=1000):
                buffer.append(event)

if __name__ == "__main__":
    main()
