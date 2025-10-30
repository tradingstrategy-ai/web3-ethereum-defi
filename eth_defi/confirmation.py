"""Transaction broadcasting, block confirmation and completion monitoring.

- Wait for multiple transactions to be confirmed and read back the results from the blockchain

- The safest way to get transactions out is to use :py:func:`wait_and_broadcast_multiple_nodes`

Some notes

- `MEV Blocker endpoints <https://docs.cow.fi/mevblocker/users-and-integrators/users/available-endpoints>`__
"""

import datetime
import logging
import time
from pprint import pformat

from typing import Collection, Dict, List, Set, Union, cast

from _decimal import Decimal
from eth_account.datastructures import SignedTransaction

from eth_defi.compat import native_datetime_utc_now
from eth_defi.event_reader.fast_json_rpc import get_last_headers
from eth_defi.provider.anvil import is_anvil
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import TransactionNotFound
from web3.providers import BaseProvider

from eth_defi.hotwallet import SignedTransactionWithNonce
from eth_defi.provider.anvil import mine
from eth_defi.provider.fallback import FallbackProvider, get_fallback_provider
from eth_defi.provider.mev_blocker import MEVBlockerProvider
from eth_defi.provider.named import get_provider_name
from eth_defi.revert_reason import fetch_transaction_revert_reason
from eth_defi.timestamp import get_latest_block_timestamp
from eth_defi.tx import decode_signed_transaction, get_tx_broadcast_data
from eth_defi.utils import to_unix_timestamp


logger = logging.getLogger(__name__)


class BroadcastFailure(Exception):
    """Could not broadcast a transaction for some reason."""


class ConfirmationTimedOut(Exception):
    """We exceeded the transaction confirmation timeout."""


class NonRetryableBroadcastException(Exception):
    """Don't try to rebroadcast these."""


class NonceMismatch(Exception):
    """Chain has a different nonce than we expect."""


class OutOfGasFunds(NonRetryableBroadcastException):
    """Out of gas funds for an executor."""


class NonceTooLow(NonRetryableBroadcastException):
    """Out of gas funds for an executor."""


class BadChainId(NonRetryableBroadcastException):
    """Out of gas funds for an executor."""


class Reverted(Exception):
    """Transaction reverted on-chain."""


def is_out_of_gas(eth_rpc_error_messag: str) -> bool:
    return "insufficient funds" in eth_rpc_error_messag


def is_invalid_sender(eth_rpc_error_messag: str) -> bool:
    """from address missing in the tx payload"""
    return "invalid sender" in eth_rpc_error_messag


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

        raw_bytes1 = get_tx_broadcast_data(signed1)
        tx_hash1 = web3.eth.send_raw_transaction(raw_bytes)

        raw_bytes2 = get_tx_broadcast_data(signed2)
        tx_hash2 = web3.eth.send_raw_transaction(raw_bytes2)

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

    started_at = native_datetime_utc_now()

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
        if native_datetime_utc_now() > started_at + verbose_timeout:
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

            if native_datetime_utc_now() > started_at + max_timeout:
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

        if native_datetime_utc_now() >= next_node_switch:
            # Check if it time to try a better node provider
            if isinstance(web3.provider, FallbackProvider):
                provider = cast(FallbackProvider, web3.provider)
                if len(provider.providers) > 1:
                    logger.warning(
                        "Timeout %s reached with this node provider. Trying with alternative node provider.",
                        node_switch_timeout,
                    )
                else:
                    logger.warning(
                        "Timeout warning threshold %s reached when trying to confirm txs, still trying:\n%s",
                        node_switch_timeout,
                        unconfirmed_txs,
                    )
                provider.switch_provider()
                next_node_switch = native_datetime_utc_now() + node_switch_timeout
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
        raw_bytes = get_tx_broadcast_data(tx)

        try:
            hash = web3.eth.send_raw_transaction(raw_bytes)
        except ValueError as e:
            # Anvil/Ethereum tester immediately fail on the broadcast
            # ValueError: {'code': -32003, 'message': 'Insufficient funds for gas * price + value'}
            decoded_tx = decode_signed_transaction(raw_bytes)
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
                hash = web3.eth.send_raw_transaction(raw_bytes)
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

    :raise Reverted:
        If the transaction did not go through and `confirm_ok` is set.
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
            if receipt["status"] != 1:
                revert_reason = fetch_transaction_revert_reason(web3, tx_hash)
                raise Reverted(f"Transaction {tx_hash.hex()} failed. Reverted: {revert_reason}\n{pformat(receipt)}")

    return receipts


# Support different raw tx formats
SignedTxType = Union[SignedTransaction, SignedTransactionWithNonce]


def _broadcast_multiple_nodes(
    providers: Collection[BaseProvider],
    signed_tx: SignedTxType,
):
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
        logger.info("_broadcast_multiple_nodes(): Broadcasting nonce:%d, hash:%s, through %s, transaction source is %s", signed_tx.nonce, signed_tx.hash.hex(), name, pformat(source or {}))

        # Does not use any middleware
        web3 = Web3(p)
        try:
            raw_bytes = get_tx_broadcast_data(signed_tx)
            web3.eth.send_raw_transaction(raw_bytes)
            success.add(p)
        except ValueError as e:
            headers = get_last_headers()
            resp_data: dict = e.args[0]

            logger.info("Broadcast JSON-RPC error %s from: %s, nonce: %s on provider: %s, got error: %s\n", signed_tx.hash.hex(), address, nonce, name, resp_data)
            logger.info("send_raw_transaction() headers:\n%s", pformat(headers))
            logger.info("Signed tx: %s", signed_tx)
            logger.info("Source transaction data: %s", source)

            # When we rebroadcast we are getting nonce too low errors,
            # both for too high and too low nonces.
            # We also get nonce too low errors,
            # when broadcasting through multiple nodes and those nodes sync nonce faster than we broadcast
            if "nonce too low" in resp_data["message"] or "nonce too high" in resp_data["message"]:
                if address:
                    current_nonce = web3.eth.get_transaction_count(address)
                else:
                    current_nonce = None

                logger.info("Nonce too low. Current:%s proposed:%s address:%s: tx:%s resp:%s", current_nonce, nonce, address, signed_tx, resp_data)
                # raise NonceTooLow(f"Current on-chain nonce {current_nonce}, proposed {nonce}") from e

            elif "ALREADY_EXISTS" in resp_data["message"]:
                # Some RPCs throw this custom error.
                # BNB chain.
                # {'code': -32000, 'message': 'ALREADY_EXISTS: already known'}
                logger.info("Already exists. Current:%s proposed:%s address:%s: tx:%s resp:%s", current_nonce, nonce, address, signed_tx, resp_data)

            elif "transaction underpriced" in resp_data["message"]:
                # Some RPCs throw this custom error.
                # Transaction is not really underpriced.
                # BNB chain.
                #  lb.drpc.org, got error: {'message': 'transaction underpriced: gas tip cap 100000000, minimum needed 1000000000', 'code': -32000}
                logger.info("Transaction underpriced. Current:%s proposed:%s address:%s: tx:%s resp:%s", current_nonce, nonce, address, signed_tx, resp_data)

            elif "invalid chain" in resp_data["message"]:
                # Invalid chain id / chain id missing.
                # Cannot retry.
                logger.warning("Invalid chain: %s %s", signed_tx, resp_data)
                raise BadChainId() from e

            elif "insufficient funds for gas" in resp_data["message"]:
                logger.warning("Out of balance error. Tx: %s, resp: %s", signed_tx, resp_data)
                # Always raise when we are out of funds,
                # because any retry is not help
                if address:
                    our_balance = web3.eth.get_balance(address)
                    our_balance = Decimal(our_balance) / Decimal(10**18)
                else:
                    our_balance = None
                raise OutOfGasFunds(f"Failed to broadcast {tx_hash}, out of gas, account {address} balance is {our_balance}.\nTX details: {signed_tx}") from e
            else:
                raise ValueError(f"Does not know how to handle error: {e}\nTx: {tx_hash}, nonce {nonce}, address {address}, see logs for further details") from e

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
    mine_blocks=False,
    inter_node_delay=datetime.timedelta(seconds=60),
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

    :param mine_blocks:
        For forked mainnet RPCs (Anvil) make sure the blockchain is making blocks.

        Only use with Anvil.

    :param inter_node_delay:
        Work around bad JSON-RPC SaaS providers.

        Sleep this time between multiple tx broadcasts.

        See https://github.com/ethereum/go-ethereum/issues/26890

        Problematic providers: Alchemy.

        Reset for Anvil to make unit tests faster.

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

    :raise OutOfGasFunds:
        The hot wallet account does not have enough native token to cover the tx fees.

    """

    assert isinstance(poll_delay, datetime.timedelta)
    assert isinstance(max_timeout, datetime.timedelta)
    assert isinstance(confirmation_block_count, int)

    if web3.eth.chain_id == 61:
        assert confirmation_block_count == 0, "Ethereum Tester chain does not progress itself, so we cannot wait"

    anviled = is_anvil(web3)

    if anviled:
        # Anvil is buggy piece of crap when you hit it with multiple RPC/broadcast requests,
        # so try to sleep and pray it works
        inter_node_delay = datetime.timedelta(seconds=0.5)

    for tx in txs:
        assert getattr(tx, "hash", None), f"Does not look like compatible TxType: {tx.__class__}: {tx}"

    txs = sorted(list(txs), key=lambda tx: tx.nonce)

    if check_nonce_validity:
        check_nonce_mismatch(web3, txs)

    provider = get_fallback_provider(web3)  # Will raise if fallback provider is not configured
    all_providers = providers = provider.providers

    provider = web3.provider
    if isinstance(provider, MEVBlockerProvider):
        transact_provider = provider.transact_provider
    else:
        transact_provider = None

    if transact_provider:
        providers = [transact_provider]
        logger.info(
            "MEV blocking enabled.\nBroadcast only through: %s\nAll providers: %s",
            providers,
            all_providers,
        )
    else:
        logger.info("No MEV blocker enable, Anvil is %s", anviled)

    logger.info(
        "Broadcasting %d transactions using %s to confirm in %d blocks, timeout is %s, inter node delay is %s",
        len(txs),
        ", ".join([get_provider_name(p) for p in providers]),
        confirmation_block_count,
        max_timeout,
        inter_node_delay,
    )

    # Double check nonces before letting txs thru
    used_nonces = set()
    for tx in txs:
        nonce = getattr(tx, "nonce", None)
        if nonce is not None:
            assert nonce not in used_nonces, f"Nonce used twice: {nonce}"
            used_nonces.add(nonce)

    started_at = native_datetime_utc_now()

    receipts_received = {}

    unconfirmed_txs: Set[HexBytes] = {tx.hash for tx in txs}

    # When we switch to level to verbose to be more
    # concerned with our debug logging,
    # but have threshold at least 1 min to avoid test spam
    verbose_timeout = max(max_timeout - datetime.timedelta(minutes=1), datetime.timedelta(minutes=1))

    next_node_switch = started_at + node_switch_timeout

    last_exception: Exception | None = None

    # Initial broadcast of txs
    for tx in txs:
        try:
            _broadcast_multiple_nodes(providers, tx)
            last_exception = None
        except NonRetryableBroadcastException:
            # Don't try to handle
            raise
        except Exception as e:
            last_exception = e

        if len(txs) >= 2:
            # https://github.com/ethereum/go-ethereum/issues/26890
            logger.info("Broadcasting multiple transactions, using inter node delay %s to sleep to ensure poor-quality nodes like Alchemy work", inter_node_delay)
            time.sleep(inter_node_delay.total_seconds())

            if anviled:
                mine(web3)

            # logger.info("Sleep done")
        else:
            logger.info(
                "Internode sleep skipped",
            )

    while len(unconfirmed_txs) > 0:
        # Transaction hashes that receive confirmation on this round
        confirmation_received = set()

        unconfirmed_tx_hashes = ", ".join(tx_hash.hex() for tx_hash in unconfirmed_txs)
        logger.debug("Starting confirmation cycle, unconfirmed txs are %s", unconfirmed_tx_hashes)

        # Bump our verbosiveness levels for the last minutes of wait
        if native_datetime_utc_now() > started_at + verbose_timeout:
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
            # TODO: Clean this up after the root cause with Anvil is figured out
            if mine_blocks:
                timestamp = get_latest_block_timestamp(web3)
                # Timestamp we read back is too old
                # ValueError: {'code': -32602, 'message': "Timestamp error: 1697933604 is lower than or equal to previous block's timestamp"}
                anvil_ts_correction = datetime.timedelta(seconds=1)
                advanced_timestamp = timestamp + poll_delay + anvil_ts_correction
                raw_ts = int(to_unix_timestamp(advanced_timestamp))
                try:
                    logger.info("Anvil mine hack running, uncofirmed txs is %s", unconfirmed_txs)
                    mine(web3)
                except ValueError as e:
                    logger.error(f"Could not mine a block, propose timestamp {advanced_timestamp}, incoming timestamp was {timestamp}")
                    raise e

            logger.info("We have still unconfirmed %d txs, sleeping %s", len(unconfirmed_txs), poll_delay.total_seconds())
            if anviled:
                # Anvil hack on failing to get receipts
                mine(web3)
            time.sleep(poll_delay.total_seconds())

            if native_datetime_utc_now() > started_at + max_timeout:
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

        if native_datetime_utc_now() >= next_node_switch:
            if transact_provider:
                logger.info(f"Broadcast failed with {transact_provider} - trying again")
            else:
                # Check if it time to try a better node provider
                logger.warning(
                    "Timeout %s reached with this node provider. Trying confirm tx success with an alternative node provider: %s.",
                    node_switch_timeout,
                    provider,
                )
                if hasattr(provider, "switch_provider"):
                    provider.switch_provider()
                else:
                    logger.warning(f"Unknown provider {provider} of {providers} - cannot switch. Not sure what's going on")

            next_node_switch = native_datetime_utc_now() + node_switch_timeout

            # Rebroadcast txs again if we suspect a broadcast failed
            # This path starts to get extra hard to handle - needs to be cleaned up
            logger.info("Rebroadcast in progress")
            for tx in txs:
                if tx.hash in unconfirmed_txs:
                    logger.info("Rebroadcasting %s", tx)
                    try:
                        _broadcast_multiple_nodes(providers, tx)
                        last_exception = None
                    except Exception as e:
                        last_exception = e
                else:
                    logger.info("Tx %s already successfully broadcasted", tx)

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


def wait_and_broadcast_multiple_nodes_mev_blocker(
    provider: MEVBlockerProvider,
    txs: Collection[SignedTxType],
    max_timeout=datetime.timedelta(minutes=10),
    poll_delay=datetime.timedelta(seconds=10),
    broadcast_and_read_delay=datetime.timedelta(seconds=6),
    try_other_provider_delay=datetime.timedelta(seconds=45),
) -> Dict[HexBytes, dict]:
    """Broadcast transactions through a MEV blocker enabled endpoint.

    - Cannot transact multiple transactions simultaneously, need to broadacst and confirm one by one

    For all transactions

    - Broadcast transaction
    - Wait until it is confirmed
        - To avoid nonce errors

    :param web3:
        Web3 instance with :py:class:`eth_defi.provider.fallback.FallbackProvider`
        configured as its RPC provider.

    :param txs:
        List of transaction to broadcast.

        Most be pre-ordered by ``(address, nonce)``.

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

    :raise OutOfGasFunds:
        The hot wallet account does not have enough native token to cover the tx fees.

    """

    assert isinstance(poll_delay, datetime.timedelta)
    assert isinstance(max_timeout, datetime.timedelta)

    receipts = {}

    # We need to perform some read calls,
    # and Base sequencer will crash with:
    # requests.exceptions.HTTPError: 403 Client Error: Forbidden for url: https://mainnet-sequencer.base.org/
    full_web3 = Web3(provider)

    # Only interact with the transact provider from no one
    if isinstance(provider, MEVBlockerProvider):
        transaction_provider = provider.transact_provider
        backup_provider = provider.call_provider
    else:
        # Test path
        transaction_provider = provider
        backup_provider = provider

    web3 = Web3(transaction_provider)
    backup_web3 = Web3(backup_provider)

    anviled = is_anvil(full_web3)
    if anviled:
        poll_delay = datetime.timedelta(seconds=0.1)

    logger.info(
        "wait_and_broadcast_multiple_nodes_mev_blocker(): broadcasting %d transactions, anvil is %s, provider is %s, timeout is %s",
        len(txs),
        anviled,
        transaction_provider,
        max_timeout,
    )

    # Initial broadcast of txs
    last_exception = None

    try_other_provider_timeout = time.time() + try_other_provider_delay.total_seconds()

    for tx in txs:
        logger.info(
            "Broadcasting nonce: %d, hash: %s, endpoint: %s",
            tx.nonce,
            tx.hash.hex(),
            get_provider_name(provider),
        )

        end = time.time() + max_timeout.total_seconds()
        tx_hash = None
        tx_hash_2 = None
        backup_provider_receipt = None
        while time.time() < end:
            try:
                if not tx_hash:
                    # Can raise nonce too low if some node is behind
                    raw_bytes = get_tx_broadcast_data(tx)
                    tx_hash = web3.eth.send_raw_transaction(raw_bytes)

                    if not anviled:
                        # Sleep between send and first read
                        time.sleep(broadcast_and_read_delay.total_seconds())

                if time.time() > try_other_provider_timeout:
                    # Also try backup provider if sequencer is blocking us for some reason
                    logger.info("Attempting backup provider %s", backup_provider)

                    # If we do not check for this we may get "nonce too low" error when
                    # broadcasting the same transaction, which is a bug in JSON-RPC
                    backup_provider_receipt = backup_web3.eth.get_transaction_receipt(tx_hash)

                    if not backup_provider_receipt:
                        logger.info(
                            "No receipt, attempting to broadcast with hash: %s with backup provider %s",
                            tx.hash.hex(),
                            backup_provider,
                        )
                        try:
                            raw_bytes = get_tx_broadcast_data(tx)
                            tx_hash_2 = web3.eth.send_raw_transaction(raw_bytes)
                            logger.info("Backup provider broadcast complete: %s", tx_hash.hex())
                        except ValueError as e:
                            logger.info("Backup broadcast failed: %s", e)
                            if "already known" in str(e):
                                # Will not retry, method eth_sendRawTransaction, as not a retryable exception <class 'ValueError'>: {'code': -32000, 'message': 'already known'}
                                # base-memex  | 2025-01-18 17:42:39 eth_defi.confirmation
                                logger.info("Already known race condition: %s", str(e))
                            else:
                                raise e
                    else:
                        logger.info("Received backup receipt with has tx_hash: %s", tx.hash)

                logger.debug("Starting MEV Blocker confirmation cycle, unconfirmed tx is: %s, sleeping poll delay %s", tx_hash.hex(), poll_delay)

                # Read receipt using read node,
                # as mainnet-sequencer on Base does not give even the receipt
                if backup_provider_receipt:
                    logger.info("Using receipt from the backup provider")
                    receipt = backup_provider_receipt
                else:
                    logger.info("Attempting to fetch receipt")
                    receipt = full_web3.eth.get_transaction_receipt(tx_hash)

                if not receipt:
                    logger.info("No receipt yet, keep trying")
                    continue

                receipts[tx.hash] = receipt
                last_exception = None
                break
            except Exception as e:
                nonce = full_web3.eth.get_transaction_count(tx.address)

                if not isinstance(e, TransactionNotFound):
                    logger.info("No receipt yet, current nonce: %d, exception %s", nonce, e, exc_info=e)
                else:
                    logger.info(f"TransactionNotFound - will keep trying. Primary tx hash: {tx_hash.hex()}, backup provider tx_hash: {tx_hash_2.hex() if tx_hash_2 else '-'}")

                last_exception = e

                if is_out_of_gas(str(e)):
                    # Out of gas situation we can never recover
                    raise OutOfGasFunds(f"Run out of gas to broadcast a transaction {tx}: {e}") from e

                if is_invalid_sender(str(e)):
                    # Out of gas situation we can never recover
                    raise NonRetryableBroadcastException(f"Invalid from value {tx}: {e}") from e

                time.sleep(poll_delay.total_seconds())

        if time.time() > end:
            if last_exception:
                raise ConfirmationTimedOut(
                    f"Run out of poll delay when confirming %d: %s, last exception is %s",
                    tx.nonce,
                    tx.hash.hex() if tx_hash else "-",
                    last_exception,
                ) from last_exception
            else:
                raise ConfirmationTimedOut(f"Run out of poll delay when confirming %d: %s", tx.nonce, tx.hash.hex())

    if last_exception:
        raise last_exception

    logger.info("All broadcasted, hashes are: %s", [h.hex() for h in receipts.keys()])

    return receipts
