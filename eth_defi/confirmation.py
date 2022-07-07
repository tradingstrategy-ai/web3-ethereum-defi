"""Transaction broadcasting, block confirmations and completion monitoring."""

import datetime
import logging
import time
from typing import Dict, List, Set, Union

from eth_account.datastructures import SignedTransaction
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import TransactionNotFound

from eth_defi.hotwallet import SignedTransactionWithNonce

logger = logging.getLogger(__name__)


class ConfirmationTimedOut(Exception):
    """We exceeded the transaction confirmation timeout."""


def wait_transactions_to_complete(
    web3: Web3,
    txs: List[Union[HexBytes, str]],
    confirmation_block_count: int = 0,
    max_timeout=datetime.timedelta(minutes=5),
    poll_delay=datetime.timedelta(seconds=1),
) -> Dict[HexBytes, dict]:
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

    :param txs:
        List of transaction hashes
    :param confirmation_block_count:
        How many blocks wait for the transaction receipt to settle.
        Set to zero to return as soon as we see the first transaction receipt.
    :return:
        Map of transaction hashes -> receipt
    """

    assert isinstance(poll_delay, datetime.timedelta)
    assert isinstance(max_timeout, datetime.timedelta)
    assert isinstance(confirmation_block_count, int)

    if web3.eth.chain_id == 61:
        assert confirmation_block_count == 0, "Ethereum Tester chain does not progress itself, so we cannot wait"

    logger.info("Waiting %d transactions to confirm in %d blocks, timeout is %s", len(txs), confirmation_block_count, max_timeout)

    started_at = datetime.datetime.utcnow()

    receipts_received = {}

    unconfirmed_txs: Set[HexBytes] = {HexBytes(tx) for tx in txs}

    while len(unconfirmed_txs) > 0:

        # Transaction hashes that receive confirmation on this round
        confirmation_received = set()

        for tx_hash in unconfirmed_txs:
            try:
                receipt = web3.eth.get_transaction_receipt(tx_hash)
            except TransactionNotFound as e:
                # BNB Chain get does this instead of returning None
                logger.debug("Transaction not found yet: %s", e)
                receipt = None

            if receipt:
                tx_confirmations = web3.eth.block_number - receipt.blockNumber
                if tx_confirmations >= confirmation_block_count:
                    logger.debug("Confirmed tx %s with %d confirmations", tx_hash.hex(), tx_confirmations)
                    confirmation_received.add(tx_hash)
                    receipts_received[tx_hash] = receipt
                else:
                    logger.debug("Still waiting more confirmations. Tx %s with %d confirmations, %d needed", tx_hash.hex(), tx_confirmations, confirmation_block_count)

        # Remove confirmed txs from the working set
        unconfirmed_txs -= confirmation_received

        if unconfirmed_txs:
            time.sleep(poll_delay.total_seconds())

            if datetime.datetime.utcnow() > started_at + max_timeout:
                for tx_hash in unconfirmed_txs:
                    tx_data = web3.eth.get_transaction(tx_hash)
                    logger.error("Data for transaction %s was %s", tx_hash.hex(), tx_data)
                unconfirmed_tx_strs = ", ".join([tx_hash.hex() for tx_hash in unconfirmed_txs])
                raise ConfirmationTimedOut(f"Transaction confirmation failed. Started: {started_at}, timed out after {max_timeout}. Still unconfirmed: {unconfirmed_tx_strs}")

    return receipts_received


def broadcast_transactions(
    web3: Web3,
    txs: List[SignedTransaction],
    confirmation_block_count=0,
    work_around_bad_nodes=True,
    bad_node_sleep=0.5,
) -> List[HexBytes]:
    """Broadcast and wait a bunch of signed transactions to confirm.

    Multiple transactions can be broadcasted and confirmed in a single go,
    to ensure fast confirmation batches.

    :param web3: Web3
    :param txs: List of Signed transactions
    :param work_around_bad_nodes:
        If `true` try to work around issues with low quality JSON-RPC APIs like Ganache
        by checking if the transaction broadcast succeeded
    :param confirmation_block_count:
        How many blocks wait for the transaction receipt to settle.
        Set to zero to return as soon as we see the first transaction receipt
        or when using insta-mining tester RPC.
    :return: List of tx hashes
    """
    # Detect Ganache
    chain_id = web3.eth.chain_id
    low_quality_node = chain_id in (1337,)
    broadcast_attempts = 5
    broadcast_sleep = 1
    bad_node_workaround = work_around_bad_nodes and low_quality_node and (confirmation_block_count > 0)

    if bad_node_workaround:
        logger.info("Ganache broadcast workaround engaged")

    # Broadcast transactions to the mempool
    hashes = []
    for tx in txs:
        assert isinstance(tx, SignedTransaction) or isinstance(tx, SignedTransactionWithNonce), f"Got {tx}"
        hash = web3.eth.send_raw_transaction(tx.rawTransaction)

        assert hash

        # Work around "Transaction not found" issues later
        # by bombing Ganache until it picks up the transaction.
        # And you can guess this code is not testable. You only run in Github CI
        # and hope it works.
        if bad_node_workaround:

            # Try to be gentle with Ganache
            time.sleep(bad_node_sleep)

            tx_data = None
            attempt = broadcast_attempts
            while attempt >= 0:
                try:
                    tx_data = web3.eth.get_transaction(hash)
                    logger.info("Node recognized our transaction %s in mempool", hash.hex())
                    break
                except TransactionNotFound:
                    pass

                time.sleep(broadcast_sleep)
                logger.warning("Rebroadcasting %s, attempts left %d", hash.hex(), attempt)
                hash = web3.eth.send_raw_transaction(tx.rawTransaction)
                attempt -= 1
            assert tx_data, f"Could not read broadcasted transaction back from the node {hash.hex()}"
        else:
            logger.debug("We are not going to try to broadcast too hard. work_around_bad_nodes:%s, confirmation_block_count:%d, chain_id:%d", work_around_bad_nodes, confirmation_block_count, chain_id)

        hashes.append(hash)

    return hashes


def broadcast_and_wait_transactions_to_complete(
    web3: Web3,
    txs: List[SignedTransaction],
    confirm_ok=True,
    work_around_bad_nodes=True,
    confirmation_block_count: int = 0,
    max_timeout=datetime.timedelta(minutes=5),
    poll_delay=datetime.timedelta(seconds=1),
) -> Dict[HexBytes, dict]:
    """Broadcast and wait a bunch of signed transactions to confirm.

    Multiple transactions can be broadcasted and confirmed in a single go,
    to ensure fast confirmation batches.

    :param web3: Web3
    :param txs: List of Signed transactions
    :param confirm_ok: Raise an error if any of the transaction reverts
    :param max_timeout: How long we wait until we give up waiting transactions to complete
    :param poll_delay: Poll timeout between the tx check loops
    :param work_around_bad_nodes:
        If `true` try to work around issues with low quality JSON-RPC APIs like Ganache
        by checking if the transaction broadcast succeeded
    :param confirmation_block_count:
        How many blocks wait for the transaction receipt to settle.
        Set to zero to return as soon as we see the first transaction receipt.
    :return: Map transaction hash -> receipt
    """

    hashes = broadcast_transactions(
        web3=web3,
        txs=txs,
        work_around_bad_nodes=work_around_bad_nodes,
        confirmation_block_count=confirmation_block_count,
    )

    # Wait transactions to confirm
    receipts = wait_transactions_to_complete(web3, hashes, confirmation_block_count=confirmation_block_count, max_timeout=max_timeout, poll_delay=poll_delay)

    if confirm_ok:
        for tx_hash, receipt in receipts.items():
            if receipt.status != 1:
                raise RuntimeError(f"Transaction {tx_hash} failed {receipt}")

    return receipts
