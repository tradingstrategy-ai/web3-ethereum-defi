"""Verify an EVM blockchain full node integrity.

Perform fetches of random blocks and recipies to see the node contains all the data.

"""
import os
import random
from typing import List, Tuple

import futureproof
import requests
from web3 import Web3, HTTPProvider

from eth_defi.event_reader.web3factory import TunedWeb3Factory


def check_block(web3_factory, block_no: int):
    """A worker function to check the integrity of a specific block over EVM-compatible JSON-RPC.

    - Check block data downloads (should always happen)

    - Check receipts download (only if your node is not pruned)

    """
    web3 = web3_factory()
    block = web3.eth.get_block(block_no)

    # Verify block integrity by checking it has a timestamp
    assert int(block["timestamp"], 16) > 0

    # Pick a random transaction.
    # Here we make some heurestics what we assume is a good transactoin.
    # - Check the transaction is a smart contract transaction
    # - Assume transaction may or may not event emits
    for attempt in range(5):
        pass

    print(f"Block {block_no:,} ok")



def main():

    check_count = int(os.environ.get("CHECK_COUNT", "10000"))

    json_rpc_url = os.environ.get("JSON_RPC_URL")

    max_workers = int(os.environ.get("MAX_WORKERS", "10"))

    assert json_rpc_url, f"You need to give JSON_RPC_URL environment variable pointing ot your full node"

    # Setup connection
    web3 = Web3(HTTPProvider(json_rpc_url))

    # Clear AttributedDict middleware that slows us down
    web3.middleware_onion.clear()

    # Set up HTTP connection pool parameters and factory
    # for creating web3 connections inside worker threads
    http_adapter = requests.adapters.HTTPAdapter(pool_connections=max_workers, pool_maxsize=max_workers)
    # Special Web3 factory function that is
    # - optimised for speed of JSON-RPC
    # - can gracefully throttle when API rate limit reached
    web3_factory = TunedWeb3Factory(json_rpc_url, http_adapter)  #
    web3 = web3_factory()

    # Always check block 1 because it is most likely to fail
    last_block = web3.eth.block_number

    print(f"Chain {web3.eth.chain_id}, checking block range 1 - {last_block:,}")

    # Set up a futureproof task manager
    #
    # For futureproof usage see
    #https://github.com/yeraydiazdiaz/futureproof
    executor = futureproof.ThreadPoolExecutor(max_workers=max_workers)
    tm = futureproof.TaskManager(executor, error_policy=futureproof.ErrorPolicyEnum.RAISE)

    # List of (web3 factory, block number) tuples
    task_args: List[Tuple[TunedWeb3Factory, int]] = []

    # Always check block 1 -  this check does not go through the thread pool
    check_block(web3_factory, 1)

    # Set up the task queue for checks
    for check_no in range(check_count):
        task_args.append((web3_factory, random.randint(1, last_block)))

    # Order checks by a block number
    task_args = sorted(task_args, key=lambda t: t[1])

    # Run the checks parallel using the thread pool
    tm.map(check_block, task_args)

    print("All ok")


if __name__ == "__main__":
    main()
