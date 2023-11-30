"""Transaction broadcasting, block confirmation and completion monitoring.

- Wait for multiple transactions to be confirmed and read back the results from the blockchain

- The safest way to get transactions out is to use :py:func:`wait_and_broadcast_multiple_nodes`
"""

import datetime
import logging
import time
from typing import Dict, List, Set, Union, cast, Collection, TypeAlias

from eth_account.datastructures import SignedTransaction
from eth_typing import HexStr, Address

from eth_defi.provider.named import get_provider_name
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import TransactionNotFound

from eth_defi.hotwallet import SignedTransactionWithNonce
from eth_defi.tx import decode_signed_transaction
from eth_defi.provider.fallback import FallbackProvider, get_fallback_provider
from web3.providers import BaseProvider

logger = logging.getLogger(__name__)


class BroadcastFailure(Exception):
    """Could not broadcast a transaction for some reason."""


class ConfirmationTimedOut(Exception):
    """We exceeded the transaction confirmation timeout."""


class NonceMismatch(Exception):
    """Chain has a different nonce than we expect."""


def wait_transactions_to_complete(
    web3: Web3,
    txs: List[Union[HexBytes, str]],
    confirmation_block_count: int = 0,
    max_timeout=datetime.timedelta(minutes=5),
    poll_delay=datetime.timedelta(seconds=1),
    node_switch_timeout=datetime.timedelta(minutes=1),
) -> Dict[HexBytes, dict]:
    """Watch multiple transactions executed at parallel.

    Use simple poll loop to wait all transactions to complete.

    If ``web3`` is configured to use :py:class:`eth_defi.provider.fallback.FallbackProvider`,
    try to switch between alternative node providers when confirming the transactions,
    because sometimes low quality nodes (Ankr, LlamaNodes) do not see transactions
    for several minutes.

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

    :param node_switch_timeout:
        Switch to alternative fallback node provider
        every time we reach this limit.

        Sometimes our node is malfunctioning (LlamaNodes, Ankr)
        and does not report transactions timely. Try with another node.

        See :py:class:`eth_defi.provider.fallback.FallbackProvider` for details.

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

    # When we switch to level to verbose to be more
    # concerned with our debug logging
    verbose_timeout = max_timeout - datetime.timedelta(minutes=1)

    next_node_switch = started_at + node_switch_timeout

    while len(unconfirmed_txs) > 0:
        # Transaction hashes that receive confirmation on this round
        confirmation_received = set()

        # Bump our verbosiveness levels for the last minutes of wait
        if datetime.datetime.utcnow() > started_at + verbose_timeout:
            tx_log_level = logging.WARNING
        else:
            tx_log_level = logging.DEBUG

        for tx_hash in unconfirmed_txs:
            try:
                receipt = web3.eth.get_transaction_receipt(tx_hash)
            except TransactionNotFound as e:
                # BNB Chain get does this instead of returning None
                logger.debug("Transaction not found yet: %s", e)
                receipt = None

            if receipt:
                tx_confirmations = web3.eth.block_number - receipt["blockNumber"]
                if tx_confirmations >= confirmation_block_count:
                    logger.log(
                        tx_log_level,
                        "Confirmed tx %s with %d confirmations",
                        tx_hash.hex(),
                        tx_confirmations,
                    )
                    confirmation_received.add(tx_hash)
                    receipts_received[tx_hash] = receipt
                else:
                    logger.log(tx_log_level, "Still waiting more confirmations. Tx %s with %d confirmations, %d needed", tx_hash.hex(), tx_confirmations, confirmation_block_count)

        # Remove confirmed txs from the working set
        unconfirmed_txs -= confirmation_received

        if unconfirmed_txs:
            time.sleep(poll_delay.total_seconds())

            if datetime.datetime.utcnow() > started_at + max_timeout:
                for tx_hash in unconfirmed_txs:
                    try:
                        tx_data = web3.eth.get_transaction(tx_hash)
                        logger.error("Data for transaction %s was %s", tx_hash.hex(), tx_data)
                    except TransactionNotFound as e:
                        # Happens on LlamaNodes - we have broadcasted the transaction
                        # but its nodes do not see it yet
                        logger.error("Node missing transaction broadcast %s", tx_hash.hex())
                        logger.exception(e)

                unconfirmed_tx_strs = ", ".join([tx_hash.hex() for tx_hash in unconfirmed_txs])
                raise ConfirmationTimedOut(f"Transaction confirmation failed. Started: {started_at}, timed out after {max_timeout} ({max_timeout.total_seconds()}s). Poll delay: {poll_delay.total_seconds()}s. Still unconfirmed: {unconfirmed_tx_strs}")

        if datetime.datetime.utcnow() >= next_node_switch:
            # Check if it time to try a better node provider
            if isinstance(web3.provider, FallbackProvider):
                provider = cast(FallbackProvider, web3.provider)
                logger.warning(
                    "Timeout %s reached with this node provider. Trying with alternative node provider.",
                    node_switch_timeout,
                )
                provider.switch_provider()
                next_node_switch = datetime.datetime.utcnow() + node_switch_timeout
            else:
                logger.warning("TX confirmation takes long time. No alternative node available: %s", web3.provider)

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

    :raise BroadcastFailure:
        If the JSON-RPC node rejects the transaction.

        - Anvil will reject some transactions immediately: if there is not enough gas money

        - Ethereum Tester reject some transactions immediately on any error in automining mode
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

        try:
            hash = web3.eth.send_raw_transaction(tx.rawTransaction)
        except ValueError as e:
            # Anvil/Ethereum tester immediately fail on the broadcast
            # ValueError: {'code': -32003, 'message': 'Insufficient funds for gas * price + value'}
            decoded_tx = decode_signed_transaction(tx.rawTransaction)
            raise BroadcastFailure(f"Could not broadcast transaction: {tx.hash.hex()}. Transaction data: {decoded_tx}. JSON-RPC error: {e}") from e

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


# Support different raw tx formats
SignedTxType = Union[SignedTransaction, SignedTransactionWithNonce]


def _broadcast_multiple_nodes(providers: Collection[BaseProvider], signed_tx: SignedTxType):
    """Attempt to broadcast a transaction through multiple providers.

    We attemt to broadcast transaction through all providers,
    one by one in serial manner.

    :param providers:
        List of Web3 providers

    :param signed_tx:
        The transaction we are going to broadcast

    :raise Exception:
        If all providers fail, raise the last exception.

        If some providers success in broadcast, consider the operation successful.
    """

    assert len(providers) > 0, "No providers provided"

    # provider instances that succeeded in broadcast
    success = set()

    # provider instance -> exception mapping
    exceptions = {}

    # See SignedTransactionWithNonce
    nonce = getattr(signed_tx, "nonce", None)
    address = getattr(signed_tx, "address", None)
    source = getattr(signed_tx, "source", None)
    tx_hash = signed_tx.hash.hex()

    for p in providers:
        name = get_provider_name(p)
        logger.info("Broadcasting %s through %s", signed_tx.hash.hex(), name)

        # Does not use any middleware
        web3 = Web3(p)
        try:
            web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            success.add(p)
        except ValueError as e:
            resp_data: dict = e.args[0]

            logger.info("Broadcast JSON-RPC error %s from: %s, nonce: %s on provider: %s, got error: %s\n", signed_tx.hash.hex(), address, nonce, name, resp_data)
            logger.info("Signed tx: %s", signed_tx)
            logger.info("Source: %s", source)

            # When we rebroadcast we are getting nonce too low errors,
            # both for too high and too low nonces
            if resp_data["message"] == "nonce too low":
                continue

        except Exception as e:
            exceptions[p] = e

    if exceptions:
        if len(exceptions) == len(providers):
            logger.error(
                "All providers failed to broadcast the transaction. Tx: %s, from: %s, nonce: %s.",
                tx_hash,
                address,
                nonce,
            )
            for provider, exception in exceptions.items():
                name = get_provider_name(p)
                logger.error("%s failed with: %s", name, e)
                logger.exception(e)

            # Raise the last exception
            raise exception
        else:
            logger.warning(
                "Some providers failed to broadcast the transaction. Success %d / %d providers. Tx: %s, from: %s, nonce: %s.",
                len(success),
                len(providers),
                tx_hash,
                address,
                nonce,
            )
            for p in success:
                name = get_provider_name(p)
                logger.warning("Provider succesfully broadcasted: %s", name)

            for p, exception in exceptions.items():
                name = get_provider_name(p)
                logger.warning("Provider failed %s: exception: %s. See log for the details", name, exception)
                logger.info(exception, exc_info=True)

            # It's enough that at least one provider success,
            # so no exception here

    # All providers succeeded
    logger.info("All providers succeeded to broadcast the tx: %s", tx_hash)


def wait_and_broadcast_multiple_nodes(
    web3: Web3,
    txs: Collection[SignedTxType],
    confirmation_block_count: int = 0,
    max_timeout=datetime.timedelta(minutes=5),
    poll_delay=datetime.timedelta(seconds=1),
    node_switch_timeout=datetime.timedelta(minutes=3),
    check_nonce_validity=True,
) -> Dict[HexBytes, dict]:
    """Try to broadcast transactions through multiple nodes.

    - Broadcast transaction through all nodes
    - Wait to confirm
    - If ``node_switch_timeout`` is reached, try to confirm using an alternative node

    :param web3:
        Web3 instance with :py:class:`eth_defi.provider.fallback.FallbackProvider`
        configured as its RPC provider.

    :param txs:
        List of transaction to broadcast.

        Most be pre-ordered by ``(address, nonce)``.

    :param confirmation_block_count:
        How many blocks wait for the transaction receipt to settle.
        Set to zero to return as soon as we see the first transaction receipt.

    :param node_switch_timeout:
        Switch to alternative fallback node provider
        every time we reach this limit.

        Sometimes our node is malfunctioning (LlamaNodes, Ankr)
        and does not report transactions timely. Try with another node.

        See :py:class:`eth_defi.provider.fallback.FallbackProvider` for details.

    :param check_nonce_validity:
        Check if signed nonces match on-chain data before attempting to broadcat.

    :return:
        Map of transaction hashes -> receipt

    :raise ConfirmationTimedOut:
        If we cannot get transactions out

    :raise NonceMismatch:
        Starting nonce does not match what we see on chain.

        When ``check_nonce_validity`` is set.

    :raise Exception:
        If all nodes fail to broadcast the transaction, then raise an exception.

        It's likely that there is a problem with a transaction.

        The exception is raised after we try multiple nodes multiple times,
        based on ``node_switch_timeout`` and other arguments.

        A reverted transaction is not an exception, but will be returned
        in the receipts.

        In the case of multiple exceptions, the last one is raised.
        The exception is whatever lower stack is giving us.
    """

    assert isinstance(poll_delay, datetime.timedelta)
    assert isinstance(max_timeout, datetime.timedelta)
    assert isinstance(confirmation_block_count, int)

    if web3.eth.chain_id == 61:
        assert confirmation_block_count == 0, "Ethereum Tester chain does not progress itself, so we cannot wait"

    for tx in txs:
        assert getattr(tx, "hash", None), f"Does not look like compatible TxType: {tx.__class__}: {tx}"

    if check_nonce_validity:
        check_nonce_mismatch(web3, txs)

    provider = get_fallback_provider(web3)  # Will raise if fallback provider is not configured
    providers = provider.providers

    logger.info(
        "Broadcasting %d transactions using %s to confirm in %d blocks, timeout is %s",
        len(txs),
        ", ".join([get_provider_name(p) for p in providers]),
        confirmation_block_count,
        max_timeout,
    )

    # Double check nonces before letting txs thru
    used_nonces = set()
    for tx in txs:
        nonce = getattr(tx, "nonce", None)
        if nonce is not None:
            assert nonce not in used_nonces, f"Nonce used twice: {nonce}"
            used_nonces.add(nonce)

    started_at = datetime.datetime.utcnow()

    receipts_received = {}

    unconfirmed_txs: Set[HexBytes] = {tx.hash for tx in txs}

    # When we switch to level to verbose to be more
    # concerned with our debug logging
    verbose_timeout = max_timeout - datetime.timedelta(minutes=1)

    next_node_switch = started_at + node_switch_timeout

    last_exception: Exception | None = None

    # Initial broadcast of txs
    for tx in txs:
        try:
            _broadcast_multiple_nodes(providers, tx)
            last_exception = None
        except Exception as e:
            last_exception = e

    while len(unconfirmed_txs) > 0:
        # Transaction hashes that receive confirmation on this round
        confirmation_received = set()

        unconfirmed_tx_hashes = ", ".join(tx_hash.hex() for tx_hash in unconfirmed_txs)
        logger.debug("Starting confirmation cycle, unconfirmed txs are %s", unconfirmed_tx_hashes)

        # Bump our verbosiveness levels for the last minutes of wait
        if datetime.datetime.utcnow() > started_at + verbose_timeout:
            tx_log_level = logging.WARNING
        else:
            tx_log_level = logging.DEBUG

        for tx_hash in unconfirmed_txs:
            try:
                receipt = web3.eth.get_transaction_receipt(tx_hash)
            except TransactionNotFound as e:
                # BNB Chain get does this instead of returning None
                logger.debug("Transaction not found yet: %s", e)
                receipt = None

            if receipt:
                tx_confirmations = web3.eth.block_number - receipt["blockNumber"]
                if tx_confirmations >= confirmation_block_count:
                    logger.log(
                        tx_log_level,
                        "Confirmed tx %s with %d confirmations",
                        tx_hash.hex(),
                        tx_confirmations,
                    )
                    confirmation_received.add(tx_hash)
                    receipts_received[tx_hash] = receipt
                else:
                    logger.log(tx_log_level, "Still waiting more confirmations. Tx %s with %d confirmations, %d needed", tx_hash.hex(), tx_confirmations, confirmation_block_count)

        # Remove confirmed txs from the working set
        unconfirmed_txs -= confirmation_received

        if unconfirmed_txs:
            time.sleep(poll_delay.total_seconds())

            if datetime.datetime.utcnow() > started_at + max_timeout:
                for tx_hash in unconfirmed_txs:
                    try:
                        tx_data = web3.eth.get_transaction(tx_hash)
                        logger.error("Data for transaction %s was %s", tx_hash.hex(), tx_data)
                    except TransactionNotFound as e:
                        # Happens on LlamaNodes - we have broadcasted the transaction
                        # but its nodes do not see it yet
                        name = get_provider_name(web3.provider)
                        logger.warning("Node %s missing transaction broadcast %s", name, tx_hash.hex())
                        logger.exception(e)

                unconfirmed_tx_strs = ", ".join([tx_hash.hex() for tx_hash in unconfirmed_txs])
                raise ConfirmationTimedOut(f"Transaction confirmation failed. Started: {started_at}, timed out after {max_timeout} ({max_timeout.total_seconds()}s). Poll delay: {poll_delay.total_seconds()}s. Still unconfirmed: {unconfirmed_tx_strs}")

        if datetime.datetime.utcnow() >= next_node_switch:
            # Check if it time to try a better node provider
            logger.warning(
                "Timeout %s reached with this node provider. Trying confirm tx success with an alternative node provider: %s.",
                node_switch_timeout,
                provider,
            )
            provider.switch_provider()
            next_node_switch = datetime.datetime.utcnow() + node_switch_timeout

            # Rebroadcast txs again if we suspect a broadcast failed
            for tx in txs:
                try:
                    _broadcast_multiple_nodes(providers, tx)
                    last_exception = None
                except Exception as e:
                    last_exception = e

    if last_exception:
        raise last_exception

    return receipts_received


def check_nonce_mismatch(web3: Web3, txs: Collection[SignedTxType]):
    """Check for nonce re-use issues.

    Compare pre-signed transactions with on-chain addresses' nonce states.

    :raise NonceMismatch:
        If your transaction broadcast is going to fail because nonce too low.
    """

    #
    # We can broadcast for multiple addresses, each address can contain multipe txs
    # Check the lowest on-chain nonce for each address
    #

    #: address, starting nonce mappings
    min_nonces = {}
    for tx in txs:
        address = tx.address
        min_nonces[address] = min(tx.nonce, min_nonces.get(address, 9_999_999))

    for address, nonce in min_nonces.items():
        on_chain_nonce = web3.eth.get_transaction_count(address)

        if on_chain_nonce != nonce:
            raise NonceMismatch(f"Nonce mismatch for broadcasted transactions.\n" + f"Address {address}, we have signed with nonce {nonce}, but on-chain is {on_chain_nonce}.\n" + f"Potential reasons include incorrectly shared hot wallet or badly synced hot wallet nonce.")
