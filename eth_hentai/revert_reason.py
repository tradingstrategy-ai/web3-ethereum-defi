"""Revert reason extraction.

Further reading

- `Web3.py Patterns: Revert Reason Lookups <https://snakecharmers.ethereum.org/web3py-revert-reason-parsing/>`_

"""
from typing import Union

from hexbytes import HexBytes
from web3 import Web3


class RevertReasonFetchFailed(Exception):
    """We could not get the revert reason for a reason or another."""


def fetch_transaction_revert_reason(web3: Web3, tx_hash: Union[HexBytes, str], use_archive_node=False) -> str:
    """Gets a transaction revert reason.

    Ethereum nodes do not store the transaction failure reason in any database or index.

    There is two ways to get the revert reason

    - Replay the transaction against the same block, and the same EVM state, where it was mined. An archive node is needed.

    - Replay the transaction against the current state. No archive node is needed, but the revert reason might be wrong.

    To make this work

    - Live node must have had enough archive state for the replay to success (full nodes store only 128 blocks by default)

    - Ganache must have been started with `block_time >= 1` so that transactions do not revent on transaction JSON-RPC broadcast

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

    :param web3: Our JSON-RPC connection

    :param tx_hash: Transaction hash of which reason we extract by simulation.

    :param use_archive_node:
        Look up *exact* reason by running the tx against the past state.
        This only works if you are connected to the archive node.
    """

    # fetch a reverted transaction:
    tx = web3.eth.get_transaction(tx_hash)

    # build a new transaction to replay:
    replay_tx = {
        'to': tx['to'],
        'from': tx['from'],
        'value': tx['value'],
        'data': tx['input'],
    }

    # replay the transaction locally
    try:
        if use_archive_node:
            web3.eth.call(replay_tx, tx.blockNumber - 1)
        else:
            web3.eth.call(replay_tx)
    except ValueError as e:
        assert len(e.args) == 1, f"Something fishy going on with {e}"
        # {'message': 'VM Exception while processing transaction: revert BEP20: transfer amount exceeds balance', 'stack': 'CallError: VM Exception while processing transaction: revert BEP20: transfer amount exceeds balance\n    at Blockchain.simulateTransaction (/usr/local/lib/node_modules/ganache/dist/node/1.js:2:49094)\n    at processTicksAndRejections (node:internal/process/task_queues:96:5)', 'code': -32000, 'name': 'CallError', 'data': '0x08c379a00000000000000000000000000000000000000000000000000000000000000020000000000000000000000000000000000000000000000000000000000000002642455032303a207472616e7366657220616d6f756e7420657863656564732062616c616e63650000000000000000000000000000000000000000000000000000'}
        data = e.args[0]
        return data["message"]

    raise RevertReasonFetchFailed("Transaction succeeded, when it should have failed")
