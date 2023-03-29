"""Verify an EVM blockchain full node integrity over JSON-RPC API.


This is an example script how to verify the integrity of your JSON-RPC full node.

The script will check for the full block range 1 - current block that your node will reply with proper

- Block data

- Transaction

- Smart contract code

- Transaction receipts

- Logs (Solidity events)

Prerequisites
~~~~~~~~~~~~~

To use the script first

- Understand basics of Python programming

- Install `web3-ethereum-defi <https://github.com/tradingstrategy-ai/web3-ethereum-defi>`__ package

Usage
~~~~~

The script  fetches of random blocks and recipies to see the node contains all the data,
up to the latest block. Uses parallel workers to speed up the checks.

The script contains heurestics whether or not block comes from a "good" full node
with all transaction receipts intact, not pruned. There are also other various failure
modes like RPC nodes just failing to return core data (e.g. polygon-rpc.com).

Here are some usage examples for UNIX shell.

First set up your JSON-RPC connection URL (with any secret tokens):

.. code-block:: shell

    export JSON_RPC_URL=https://polygon-rpc.com/

Run a check for 100 randomly selected blocks:

.. code-block:: shell

    CHECK_COUNT=100 python scripts/verify-node-integrity.py

Run a check for 100 randomly selected blocks from the last 10,000 blocks of the chain:

.. code-block:: shell

    START_BLOCK=-10000 CHECK_COUNT=100 python scripts/verify-node-integrity.py

Run in a single-thread example, good for debugging):

.. code-block:: shell

    MAX_WORKERS=1 python scripts/verify-node-integrity.py

The script will go through all randomly blocks in the order print its progress int the console:

.. code-block:: text

    Block 26,613,761 ok - has logs
    Block 26,525,210 ok - has logs
    Block 26,618,551 ok - has logs
    Block 26,629,338 ok - has logs

In the end the script prints out all failed :

.. code-block:: text

    Finished, found 0 uncertain/failed blocks out of 1,000 with the failure rate of 0.0%

In the case of errors you will see:

.. code-block:: text

    Finished, found 52 uncertain/failed blocks out of 100 with the failure rate of 52.0%
    Double check uncertain blocks manually and with a block explorer:
        Block 10,472,300 - could not fetch transaction data for transaction 0xdd090fdde0f32d5c1beb27bcbf08220e3976c59c7f9bceb586b4841d9a4acd0e
        Block 10,857,915 - could not fetch transaction receipt for transaction 0xf77fc0d5053738b688022e8ab3c7cc4335f0467e22042f3cc0ec85a10a0e42a3
        Block 11,984,710 - could not fetch transaction data for transaction 0x

"""
import os
import random
import time
from dataclasses import dataclass
from itertools import starmap
from typing import List, Tuple, Optional

import futureproof
import requests
from eth.constants import ZERO_ADDRESS
from hexbytes import HexBytes
from web3.exceptions import TransactionNotFound, BlockNotFound

from eth_defi.event_reader.conversion import convert_jsonrpc_value_to_int
from eth_defi.event_reader.web3factory import TunedWeb3Factory


@dataclass
class BlockIntegrityFailure:
    """Describe block data errors.

    Signal the node verifier had errors on a specific block.
    The subclasses will tell the exact error type.
    """

    block_number: int


@dataclass
class BlockMissing(BlockIntegrityFailure):
    """Node lacks block data."""

    def __repr__(self):
        return f"Block {self.block_number:,} - block missing"


@dataclass
class LogsMissing(BlockIntegrityFailure):
    """We could find any logs in the block."""

    attempts: int

    def __repr__(self):
        return f"Block {self.block_number:,} - did not find any smart contract transaction with logs after {self.attempts} random checks"


@dataclass
class TransactionMissing(BlockIntegrityFailure):
    """Node could not serve the transaction by its hash"""

    tx_hash: HexBytes

    def __repr__(self):
        return f"Block {self.block_number:,} - could not fetch transaction data for transaction {self.tx_hash.hex()}"


@dataclass
class ReceiptMissing(BlockIntegrityFailure):
    """Node could not serve the transaction receipt"""

    tx_hash: HexBytes

    def __repr__(self):
        return f"Block {self.block_number:,} - could not fetch transaction receipt for transaction {self.tx_hash.hex()}"


@dataclass
class CodeMissing(BlockIntegrityFailure):
    """Node could not serve the smart contract code"""

    address: str

    def __repr__(self):
        return f"Block {self.block_number:,} - could not fetch smart contract cpde for address {self.address}"


@dataclass
class RandomTransactionDataFailure(BlockIntegrityFailure):
    """An error we have no idea about.

    Thanks Pokt Network.
    I hae no idea what's this.

    Example:

    .. code-block:: text

        ValueError: {'code': -32000, 'message': 'getReceipts error: nonce too low: address 0x5cFEa98867469500b90F0Aa18402b95531869662, tx: 1577 state: 2172'}
    """

    tx_hash: HexBytes

    error: str

    def __repr__(self):
        return f"Block {self.block_number:,} - could not fetch transaction data {self.tx_hash.hex()} - error {self.error}"


def check_block(web3_factory, block_no: int, max_tx_checks=20, low_block_tx_threshold=10) -> Optional[BlockIntegrityFailure]:
    """A worker function to check the integrity of a specific block over EVM-compatible JSON-RPC.

    - Check block data downloads (should always happen)

    - Check receipts download (only if your node is not pruned)

    :return:
        None on success (block contains logs).

        For failures return tuple (block number, transaction hash)
    """
    web3 = web3_factory()

    try:
        block = web3.eth.get_block(block_no)
    except BlockNotFound:
        return BlockMissing(block_number=block_no)

    # Verify block integrity by checking it has a timestamp
    timestamp = convert_jsonrpc_value_to_int(block["timestamp"])
    assert timestamp > 0  # Not going to happen, but let's just check for something

    # Pick a random transaction.
    # Here we make some heurestics what we assume is a good transactoin.
    # - Check the transaction is a smart contract transaction
    # - Assume transaction may or may not event emits
    txs = block["transactions"]
    if len(txs) < low_block_tx_threshold:
        # Ignore first tx which is always coinbase tx
        print(f"Block {block_no:,} ok - has low amount of transactions {len(txs)} and no log check attempted")
        return None

    # Try to find at least one transaction with logs
    for attempt in range(max_tx_checks):
        tx_hash = random.choice(txs)

        try:
            tx = web3.eth.get_transaction(tx_hash)
        except TransactionNotFound as e:
            # TransactionNotFound is generated by so-called null result formatter in
            # web3._utils.method_formatters - it means JSON-RPC returned a zero byte response.
            # In these cases we do not retry, because if a node ha
            return TransactionMissing(block_no, tx_hash)

        target_address = tx["to"]
        if target_address == ZERO_ADDRESS:
            # Contract deployment tx
            continue

        if target_address is None:
            # Not sure what tx type, but happens,
            # prolly someone made a broken tx?
            continue

        # If transaction was pure value transfer and not to smart contract,]
        # it cannot emit logs
        try:
            code = web3.eth.get_code(target_address)
        except Exception as e:
            # Not sure under which conditions this happen but add some more context
            # info to the exception
            return CodeMissing(block_no, target_address)

        if len(code) == 0:
            # Not a smart contract target,
            # tx to another EOA,
            # try to find a better tx to test
            continue

        try:
            receipt = web3.eth.get_transaction_receipt(tx_hash)
        except ValueError as e:
            return RandomTransactionDataFailure(block_no, tx_hash, str(e))
        except TransactionNotFound as e:
            # TransactionNotFound is generated by so-called null result formatter in
            # web3._utils.method_formatters - it means JSON-RPC returned a zero byte response.
            # In these cases we do not retry, because if a node has an error condition
            # it should return HTTP error, not zero byte response.
            return ReceiptMissing(block_no, tx_hash)

        if len(receipt["logs"]) > 0:
            # We have logs for this tx - the block is correctly indexed
            # and not pruned
            print(f"Block {block_no:,} ok - has logs")
            return None

    print(f"WARNING Block {block_no:,} - could not find any transaction with logs, did {max_tx_checks} heuristic attempts")
    return LogsMissing(block_no, max_tx_checks)


def main():
    # Read arguments
    check_count = int(os.environ.get("CHECK_COUNT", "100"))
    json_rpc_url = os.environ.get("JSON_RPC_URL")
    max_workers = int(os.environ.get("MAX_WORKERS", "10"))
    start_block = int(os.environ.get("START_BLOCK", "1"))  # Negative start block scans from the end of the chain
    assert json_rpc_url, f"You need to give JSON_RPC_URL environment variable pointing ot your full node"

    # Set up HTTP connection pool parameters and factory
    # for creating web3 connections inside worker threads
    http_adapter = requests.adapters.HTTPAdapter(pool_connections=max_workers, pool_maxsize=max_workers)
    # Special Web3 factory function that is
    # - optimised for speed of JSON-RPC
    # - can gracefully throttle when API rate limit reached
    web3_factory = TunedWeb3Factory(json_rpc_url, http_adapter, thread_local_cache=True, api_counter=True)
    web3 = web3_factory()

    # Always check block 1 because it is most likely to fail
    last_block = web3.eth.block_number

    # Choose N blocks from the end of the chain
    if start_block < 0:
        start_block = last_block + start_block

    print(f"Chain {web3.eth.chain_id}, checking block range {start_block:,} - {last_block:,}")

    # List of (web3 factory, block number) tuples
    task_args: List[Tuple[TunedWeb3Factory, int]] = []

    # Always check block 1 as it is most likely to fail
    # This check does not go through the thread pool
    first_block_error = check_block(web3_factory, 1)
    if first_block_error:
        print(f"First block is faulty: {first_block_error}")

    # Set up the task queue for checks
    for check_no in range(check_count):
        task_args.append((web3_factory, random.randint(start_block, last_block)))

    # Order checks by a block number
    task_args = sorted(task_args, key=lambda t: t[1])

    print(f"Checking {len(task_args):,} blocks")

    start = time.time()

    if max_workers > 1:
        print(f"Doing multithread scan using {max_workers} workers")
        # Do a parallel scan for the maximum speed
        #
        # Set up a futureproof task manager
        #
        # For futureproof usage see
        # https://github.com/yeraydiazdiaz/futureproof
        executor = futureproof.ThreadPoolExecutor(max_workers=max_workers)
        tm = futureproof.TaskManager(executor, error_policy=futureproof.ErrorPolicyEnum.RAISE)

        # Run the checks parallel using the thread pool
        tm.map(check_block, task_args)

        # Extract results from the parallel task queue
        results = [task.result for task in tm.as_completed()]

    else:
        print("Doing single thread scan")
        # Do single thread - good for debuggers like pdb/ipdb
        #
        iter = starmap(check_block, task_args)

        # Force workers to finish
        results = list(iter)

    duration = time.time() - start

    # Count failures
    failed_blocks = [b for b in results if b is not None]
    failure_rate = len(failed_blocks) / check_count
    print(f"Finished, found {len(failed_blocks):,} uncertain/failed blocks out of {check_count:,} with the failure rate of {failure_rate * 100:.1f}%")
    blocks = len(results)
    block_per_second = blocks / duration
    api_call_counts = web3_factory.get_total_api_call_counts()
    api_calls_per_second = api_call_counts["total"] / duration
    api_calls_per_block = api_call_counts["total"] / blocks
    print(f"Blocks checked per second: {block_per_second:.2f}")
    print(f"API calls per second: {api_calls_per_second:.2f}")
    print(f"API calls per block: {api_calls_per_block:.2f}")

    if failed_blocks:
        print("Double check uncertain blocks manually and with a block explorer:")
        failure_reason: BlockIntegrityFailure
        for failure_reason in failed_blocks:
            print(f"    {failure_reason}")


if __name__ == "__main__":
    main()
