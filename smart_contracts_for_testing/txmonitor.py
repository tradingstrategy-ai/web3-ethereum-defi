"""Transaction monitoring helpers."""
import time
from typing import List, Dict
import datetime

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
