"""Revert reason extraction.

Further reading

- `Web3.py Patterns: Revert Reason Lookups <https://snakecharmers.ethereum.org/web3py-revert-reason-parsing/>`_

"""
import logging
from typing import Union

from eth_tester.exceptions import TransactionFailed
from hexbytes import HexBytes
from web3 import Web3

from eth_defi.abi import get_transaction_data_field

logger = logging.getLogger(__name__)


def fetch_transaction_revert_reason(
    web3: Web3,
    tx_hash: Union[HexBytes, str],
    use_archive_node=False,
    unknown_error_message="<could not extract the revert reason>",
) -> str:
    """Gets a transaction revert reason.

    Ethereum nodes do not store the transaction failure reason in any database or index.

    There is two ways to get the revert reason

    - Replay the transaction against the same block, and the same EVM state, where it was mined. An archive node is needed.

    - Replay the transaction against the current state. No archive node is needed, but the revert reason might be wrong.

    To make this work

    - Live node must have had enough archive state for the replay to success (full nodes store only 128 blocks by default)

    - Ganache must have been started with `block_time >= 1` so that transactions do not revert on transaction broadcast

    - When sending transsaction using `web3.eth.send_transaction` it must have `gas` set, or the transaction
      will revert during the gas estimation

    Example:

    .. code-block:: python

        receipts = wait_transactions_to_complete(web3, [tx_hash])

        # Check that the transaction reverted
        assert len(receipts) == 1
        receipt = receipts[tx_hash]
        assert receipt.status == 0

        reason = fetch_transaction_revert_reason(web3, tx_hash)
        assert reason == "VM Exception while processing transaction: revert BEP20: transfer amount exceeds balance"

    .. note ::

        `use_archive_node=True` path cannot be tested in unit testing.

    Different JSON-RPC providers may return payloads and this function
    needs to handle each provider as a special case. See `manual_bnb_chain_check_revert_reason.py`
    for testing. Currently tested:

    - Ethereum Tester

    - Ganache

    - BNB Chain + geth

    :param web3: Our JSON-RPC connection

    :param tx_hash: Transaction hash of which reason we extract by simulation.

    :param use_archive_node:
        Look up *exact* reason by running the tx against the past state.
        This only works if you are connected to the archive node.

    :param unknown_error_message:
        Return this message if the revert reason extraction fails.
        Check the logs for details and pointers.

    :return: The revert reason of the placeholder message if we could not extract the reason somehow.
    """

    # fetch a reverted transaction:
    tx = web3.eth.get_transaction(tx_hash)

    # build a new transaction to replay:
    replay_tx = {
        "to": tx["to"],
        "from": tx["from"],
        "value": tx["value"],
        "data": get_transaction_data_field(tx),
    }

    # Replay the transaction locally
    try:
        if use_archive_node:
            result = web3.eth.call(replay_tx, tx.blockNumber - 1)
        else:
            result = web3.eth.call(replay_tx)
    except ValueError as e:
        logger.debug("Revert exception result is: %s", e)
        assert len(e.args) == 1, f"Something fishy going on with {e}"

        data = e.args[0]
        if type(data) == str:
            # BNB Smart chain + geth
            return data
        else:
            # Ganache
            # {'message': 'VM Exception while processing transaction: revert BEP20: transfer amount exceeds balance', 'stack': 'CallError: VM Exception while processing transaction: revert BEP20: transfer amount exceeds balance\n    at Blockchain.simulateTransaction (/usr/local/lib/node_modules/ganache/dist/node/1.js:2:49094)\n    at processTicksAndRejections (node:internal/process/task_queues:96:5)', 'code': -32000, 'name': 'CallError', 'data': '0x08c379a00000000000000000000000000000000000000000000000000000000000000020000000000000000000000000000000000000000000000000000000000000002642455032303a207472616e7366657220616d6f756e7420657863656564732062616c616e63650000000000000000000000000000000000000000000000000000'}
            return data["message"]
    except TransactionFailed as e:
        # Ethereum Tester
        return e.args[0]

    # TODO:
    # Not sure why this happens.
    # When checking on bscchain:
    # This transaction has been included and will be reflected in a short while.

    receipt = web3.eth.get_transaction_receipt(tx_hash)
    if receipt.status != 0:
        logger.error("Queried revert reason for a transaction, but receipt tells it did not fail. tx_hash:%s, receipt: %s", tx_hash.hex(), receipt)

    current_block_number = web3.eth.block_number
    # TODO: Convert to logger record
    logger.error(f"Transaction succeeded, when we tried to fetch its revert reason. Hash: {tx_hash.hex()}, tx block num: {tx.blockNumber}, current block number: {current_block_number}, transaction result {result.hex()}. Maybe the chain tip is unstable?")
    return unknown_error_message
