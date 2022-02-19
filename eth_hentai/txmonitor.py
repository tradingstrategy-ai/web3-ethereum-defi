"""Transaction broadcasting and monitoring."""

import time
from typing import List, Dict
import datetime

from eth_account.datastructures import SignedTransaction
from hexbytes import HexBytes
from web3 import Web3


def wait_transactions_to_complete(
        web3: Web3,
        txs: List[HexBytes],
        max_timeout=datetime.timedelta(minutes=5),
        poll_delay=datetime.timedelta(seconds=1)) -> Dict[HexBytes, dict]:
    """Watch multiple transactions executed at parallel.

    Use simple poll loop to wait all transactions to complete.

    Example:

    .. code-block:: python

        tx_hash1 = web3.eth.send_raw_transaction(signed1.rawTransaction)
        tx_hash2 = web3.eth.send_raw_transaction(signed2.rawTransaction)

        complete = wait_transactions_to_complete(web3, [tx_hash1, tx_hash2])

        # Check both transaction succeeded
        for receipt in complete.values():
            assert receipt.status == 1  # tx success

    :param txs: List of transaction hashes
    :return: Map of transaction hashes -> receipt
    """

    assert isinstance(poll_delay, datetime.timedelta)
    assert isinstance(max_timeout, datetime.timedelta)

    started_at = datetime.datetime.utcnow()

    receipts_received = {}

    while len(receipts_received) < len(txs):

        for tx_hash in txs:
            receipt = web3.eth.get_transaction_receipt(tx_hash)
            if receipt:
                receipts_received[tx_hash] = receipt

        time.sleep(poll_delay.total_seconds())

        if datetime.datetime.utcnow() > started_at + max_timeout:
            raise RuntimeError("Never was able to confirm some of the transactions")

    return receipts_received


def broadcast_and_wait_transactions_to_complete(
        web3: Web3,
        txs: List[SignedTransaction],
        confirm_ok=True,
        max_timeout=datetime.timedelta(minutes=5),
        poll_delay=datetime.timedelta(seconds=1)) -> Dict[HexBytes, dict]:
    """Broadcast and wait a bunch of signed transactions to confirm.

    :param web3: Web3
    :param txs: List of Signed transactions
    :param confirm_ok: Raise an error if any of the transaction reverts
    :param max_timeout: How long we wait until we give up waiting transactions to complete
    :param poll_delay: Poll timeout between the tx check loops
    :return: Map transaction hash -> receipt
    """

    # Broadcast transactions to the mempool
    hashes = []
    for tx in txs:
        assert isinstance(tx, SignedTransaction), f"Got {tx}"
        hash = web3.eth.send_raw_transaction(tx.rawTransaction)
        hashes.append(hash)

    # Wait transactions to confirm
    receipts = wait_transactions_to_complete(web3, hashes, max_timeout, poll_delay)

    if confirm_ok:
        for tx_hash, receipt in receipts.items():
            if receipt.status != 1:
                raise RuntimeError(f"Transaction {tx_hash} failed {receipt}")

    return receipts

