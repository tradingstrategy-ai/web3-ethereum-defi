"""Transaction broadcasting, block confirmation and completion monitoring.

- Wait for multiple transactions to be confirmed and read back the results from the blockchain

- The safest way to get transactions out is to use :py:func:`wait_and_broadcast_multiple_nodes`

Some notes

- `MEV Blocker endpoints <https://docs.cow.fi/mevblocker/users-and-integrators/users/available-endpoints>`__
"""

import datetime
import logging
import time
from _decimal import Decimal
from dataclasses import dataclass
from pprint import pformat
from typing import Collection, Dict, List, Set, Union, cast

from eth_account.datastructures import SignedTransaction
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import TransactionNotFound
from web3.providers import BaseProvider

from eth_defi.compat import native_datetime_utc_now
from eth_defi.event_reader.fast_json_rpc import get_last_headers
from eth_defi.hotwallet import SignedTransactionWithNonce
from eth_defi.provider.anvil import is_anvil, mine
from eth_defi.provider.fallback import FallbackProvider, get_fallback_provider
from eth_defi.provider.mev_blocker import MEVBlockerProvider
from eth_defi.provider.named import get_provider_name
from eth_defi.provider.receipt import TransactionVisibilityTimedOut, wait_for_transaction_visibility
from eth_defi.revert_reason import fetch_transaction_revert_reason
from eth_defi.timestamp import get_latest_block_timestamp
from eth_defi.tx import DecodeFailure, decode_signed_transaction, get_tx_broadcast_data
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


def _format_gas_price(value: int) -> str:
    """Format a wei-denominated gas price for an exception message.

    Keep the exact wei value and include a compact gwei equivalent so an
    operator can compare signed and network pricing without doing conversion.

    :param value:
        Gas price in wei.

    :return:
        Human-readable gas price containing wei and gwei values.
    """
    gwei = f"{value / 10**9:.9f}".rstrip("0").rstrip(".")
    return f"{value} wei ({gwei} gwei)"


def _parse_gas_price(value: object) -> int | None:
    """Parse a Python or JSON-RPC gas-price value.

    Web3 transaction data normally contains integers, while raw JSON-RPC
    responses contain hexadecimal strings. Other values are unusable for
    diagnostics and return ``None``.

    :param value:
        Candidate gas-price value.

    :return:
        Parsed integer, or ``None`` for an unsupported value.
    """
    if type(value) is int:
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return None
    return None


def _get_gas_price_from_source(source: dict | None) -> tuple[int | None, int | None, str | None]:
    """Extract EIP-1559 or legacy gas-price fields from transaction data.

    EIP-1559 transactions use their maximum fee as the inclusion cap. Legacy
    transactions use ``gasPrice`` directly.

    :param source:
        Decoded or retained transaction fields.

    :return:
        Gas-price cap, optional priority fee and formatted field description.
    """
    if not source:
        return None, None, None

    max_fee_per_gas = _parse_gas_price(source.get("maxFeePerGas"))
    if max_fee_per_gas is not None:
        priority_fee_per_gas = _parse_gas_price(source.get("maxPriorityFeePerGas"))
        priority_description = _format_gas_price(priority_fee_per_gas) if priority_fee_per_gas is not None else "unavailable"
        description = f"maxFeePerGas={_format_gas_price(max_fee_per_gas)}, maxPriorityFeePerGas={priority_description}"
        return max_fee_per_gas, priority_fee_per_gas, description

    legacy_gas_price = _parse_gas_price(source.get("gasPrice"))
    if legacy_gas_price is not None:
        return legacy_gas_price, None, f"gasPrice={_format_gas_price(legacy_gas_price)}"

    return None, None, None


def _get_signed_transaction_gas_price(signed_tx: SignedTxType) -> tuple[int | None, int | None, str]:
    """Read gas pricing from a signed transaction.

    Prefer retained source fields and decode the raw transaction only when
    needed, because not every signed-transaction wrapper retains its source.

    :param signed_tx:
        Signed transaction whose submitted gas pricing is needed.

    :return:
        Gas-price cap, optional priority fee and formatted field description.
    """
    source = getattr(signed_tx, "source", None)
    gas_price, priority_fee, description = _get_gas_price_from_source(source)
    if gas_price is not None:
        return gas_price, priority_fee, description

    try:
        raw_transaction = get_tx_broadcast_data(signed_tx)
        decoded_source = decode_signed_transaction(raw_transaction)
    except (AttributeError, DecodeFailure, TypeError, ValueError) as e:
        logger.warning("Could not decode signed transaction gas price for timeout diagnostics: %s", e)
        return None, None, "unavailable; the signed transaction source could not be recovered"

    gas_price, priority_fee, description = _get_gas_price_from_source(decoded_source)
    if gas_price is None:
        return None, None, "unavailable from the signed transaction"
    return gas_price, priority_fee, description


@dataclass(slots=True, frozen=True)
class _NetworkGasPrice:
    """Best-effort network pricing captured for timeout diagnostics."""

    price: int | None
    priority_fee: int | None
    source: str
    is_base_fee: bool = False


def _get_single_attempt_provider(web3: Web3) -> BaseProvider:
    """Get one provider without the fallback provider's retry loop.

    Unwrap both MEV Blocker and fallback providers so timeout diagnostics make
    exactly one transport attempt instead of starting another failover cycle.

    :param web3:
        Web3 connection used by transaction confirmation.

    :return:
        Active raw provider for a single diagnostic request.
    """
    provider = web3.provider
    if isinstance(provider, MEVBlockerProvider):
        provider = provider.call_provider
    if isinstance(provider, FallbackProvider):
        return provider.get_active_provider()
    return provider


def _fetch_rpc_gas_price(provider: BaseProvider, method: str) -> tuple[int | None, str | None]:
    """Fetch one integer gas-price value through raw JSON-RPC.

    Diagnostic RPC failures are returned as text so they cannot replace the
    original confirmation timeout.

    :param provider:
        Provider used for one raw request.

    :param method:
        Integer-valued JSON-RPC method to call.

    :return:
        Parsed value and optional failure description.
    """
    try:
        response = provider.make_request(method, [])
        if response.get("error"):
            return None, f"{method} returned RPC error {response['error']}"
        value = _parse_gas_price(response.get("result"))
        if value is None or value < 0:
            return None, f"{method} returned invalid value {response.get('result')!r}"
        return value, None
    except Exception as e:
        # Raw provider failures vary by transport and must not replace the
        # original ConfirmationTimedOut.
        return None, f"{method} failed with {type(e).__name__}: {e}"


def _fetch_current_network_gas_price(provider: BaseProvider) -> _NetworkGasPrice:
    """Fetch current network pricing without masking a confirmation timeout.

    Try ``eth_gasPrice`` first because chains may customise it to return their
    recommended next-block price. If that RPC method is unavailable, fall back
    to the latest block's EIP-1559 base fee. The caller supplies a provider for
    a single raw RPC attempt so diagnostics cannot restart the
    fallback provider's multi-minute retry cycle. Raw JSON-RPC values are parsed
    here without depending on Web3 middleware.

    :param provider:
        Active Web3 provider used directly for diagnostic RPC calls.

    :return:
        Node-reported gas price, priority fee suggestion and source details.
    """
    gas_price, gas_price_error = _fetch_rpc_gas_price(provider, "eth_gasPrice")
    priority_fee, priority_fee_error = _fetch_rpc_gas_price(provider, "eth_maxPriorityFeePerGas")
    errors = [error for error in (gas_price_error, priority_fee_error) if error]

    if gas_price is not None:
        source = "eth_gasPrice"
        if errors:
            source += "; " + "; ".join(errors)
        return _NetworkGasPrice(gas_price, priority_fee, source)

    try:
        response = provider.make_request("eth_getBlockByNumber", ["latest", False])
        if response.get("error"):
            errors.append(f"eth_getBlockByNumber returned RPC error {response['error']}")
        else:
            latest_block = response.get("result") or {}
            base_fee = _parse_gas_price(latest_block.get("baseFeePerGas"))
            if base_fee is not None and base_fee >= 0:
                source = "latest block baseFeePerGas fallback; " + "; ".join(errors)
                return _NetworkGasPrice(base_fee, priority_fee, source, is_base_fee=True)
            errors.append(f"eth_getBlockByNumber returned invalid baseFeePerGas {latest_block.get('baseFeePerGas')!r}")
    except Exception as e:
        # Transport exceptions are diagnostic data and must not replace the
        # original ConfirmationTimedOut.
        errors.append(f"latest block lookup failed with {type(e).__name__}: {e}")

    return _NetworkGasPrice(None, priority_fee, "; ".join(errors))


def format_confirmation_timeout_gas_diagnostics(
    provider: BaseProvider,
    txs: Collection[SignedTxType],
    unconfirmed_txs: Collection[HexBytes],
) -> str:
    """Format submitted and current network gas prices after a timeout.

    :param provider:
        Single-attempt provider for raw diagnostic RPC calls.

    :param txs:
        All signed transactions in the confirmation batch.

    :param unconfirmed_txs:
        Hashes that remained unconfirmed when the timeout elapsed.

    :return:
        Multi-line gas-price diagnostics for ``ConfirmationTimedOut``.
    """
    network = _fetch_current_network_gas_price(provider)
    lines = ["Gas price diagnostics:"]

    if network.price is None:
        lines.append(f"Current network gas price unavailable: {network.source}.")
    elif network.is_base_fee:
        lines.append(f"Latest block base fee: {_format_gas_price(network.price)} (excludes priority fee; source: {network.source}).")
    else:
        lines.append(f"Current network gas price: {_format_gas_price(network.price)} (source: {network.source}).")
    if network.priority_fee is not None:
        lines.append(f"Current network priority fee: {_format_gas_price(network.priority_fee)} (source: eth_maxPriorityFeePerGas).")

    unconfirmed_hashes = set(unconfirmed_txs)
    for signed_tx in txs:
        if signed_tx.hash not in unconfirmed_hashes:
            continue

        tx_hash = signed_tx.hash.hex()
        used_price, used_priority_fee, used_price_description = _get_signed_transaction_gas_price(signed_tx)
        lines.append(f"Transaction {tx_hash} used gas price: {used_price_description}.")

        if used_price is None:
            continue

        effective_priority_fee = used_priority_fee
        if network.is_base_fee and network.price is not None and used_priority_fee is not None:
            effective_priority_fee = min(used_priority_fee, max(0, used_price - network.price))
        priority_underpriced = effective_priority_fee is not None and network.priority_fee is not None and effective_priority_fee < network.priority_fee

        if network.price is None and priority_underpriced:
            lines.append(f"Likely transaction gas-price mispricing for {tx_hash}: maxPriorityFeePerGas {_format_gas_price(used_priority_fee)} is below the node's current priority-fee suggestion {_format_gas_price(network.priority_fee)}. The transaction may be deprioritised; maxFeePerGas sufficiency could not be assessed because the current network price was unavailable.")
        elif network.price is None:
            lines.append(f"Gas-price mispricing for {tx_hash} could not be assessed because the current network price was unavailable.")
        elif used_price < network.price:
            if network.is_base_fee:
                lines.append(f"Likely transaction gas-price mispricing for {tx_hash}: the transaction cap {_format_gas_price(used_price)} is below the latest block base fee {_format_gas_price(network.price)} and cannot be included until the base fee falls.")
            else:
                lines.append(f"Likely transaction gas-price mispricing for {tx_hash}: the transaction cap {_format_gas_price(used_price)} is below the node's current suggested network price {_format_gas_price(network.price)}. The transaction may remain deprioritised or be dropped.")
        elif priority_underpriced and network.is_base_fee:
            lines.append(f"Likely transaction gas-price mispricing for {tx_hash}: the effective priority fee {_format_gas_price(effective_priority_fee)} is below the node's current priority-fee suggestion {_format_gas_price(network.priority_fee)}. The effective priority fee is limited by maxPriorityFeePerGas and by maxFeePerGas minus the base fee, so the transaction may be deprioritised.")
        elif priority_underpriced:
            lines.append(f"Likely transaction gas-price mispricing for {tx_hash}: maxPriorityFeePerGas {_format_gas_price(used_priority_fee)} is below the node's current priority-fee suggestion {_format_gas_price(network.priority_fee)}. The transaction may be deprioritised even though its maxFeePerGas is sufficient.")
        else:
            lines.append(f"The transaction cap for {tx_hash} is not below the available network fee reference at timeout. Gas pricing may have differed at initial broadcast; investigate nonce ordering, broadcast acceptance, and RPC propagation.")

    return "\n".join(lines)


def _fetch_timed_out_transaction(provider: BaseProvider, tx_hash: HexBytes) -> dict | None:
    """Fetch a timed-out transaction through one raw JSON-RPC request.

    Raw providers require a ``0x``-prefixed transaction hash. An RPC error is
    distinct from a successful null result, which means the node does not know
    the transaction.

    :param provider:
        Provider used for one raw request.

    :param tx_hash:
        Transaction hash to query.

    :return:
        Transaction data, or ``None`` when the node does not know the hash.

    :raise ValueError:
        The provider returned a JSON-RPC error response.
    """
    response = provider.make_request("eth_getTransactionByHash", [tx_hash.to_0x_hex()])
    if response.get("error"):
        raise ValueError(f"eth_getTransactionByHash returned RPC error {response['error']}")
    return response.get("result")


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

        Wait up to this time between multiple tx broadcasts for all read
        providers to see the preceding transaction. Progress immediately once
        they do. At timeout, progress when at least one read provider sees it.
        If no provider sees it, continue with normal confirmation and rebroadcast
        handling.

        This checks transaction visibility only; normal receipt confirmation
        follows after the complete batch has been broadcast.

        See https://github.com/ethereum/go-ethereum/issues/26890

        Problematic providers: Alchemy.

        Reset for Anvil to make unit tests faster.

    :return:
        Map of transaction hashes -> receipt

    :raise ConfirmationTimedOut:
        If we cannot get transactions out. The exception includes the signed
        transaction gas-price fields, best-effort current network gas and
        priority fees, and an underpricing hint when the signed values are lower.

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
        # Keep the visibility deadline short on Anvil so unit tests remain fast.
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
        "Broadcasting %d transactions using %s to confirm in %d blocks, timeout is %s, inter-node visibility timeout is %s",
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

    # Initial broadcast of txs. Wait for read-provider visibility before the
    # following sequential nonce, using the former sleep as a maximum budget.
    for tx_index, tx in enumerate(txs):
        try:
            _broadcast_multiple_nodes(providers, tx)
            last_exception = None
        except NonRetryableBroadcastException:
            # Don't try to handle
            raise
        except Exception as e:
            last_exception = e

        if tx_index < len(txs) - 1:
            # https://github.com/ethereum/go-ethereum/issues/26890
            visibility_timeout = inter_node_delay.total_seconds()
            if visibility_timeout > 0:
                visibility_poll_delay = min(1.0, visibility_timeout / 2)
                try:
                    wait_for_transaction_visibility(
                        web3,
                        tx.hash,
                        timeout=visibility_timeout,
                        poll_delay=visibility_poll_delay,
                        max_poll_delay=max(visibility_poll_delay, min(5.0, visibility_timeout / 2)),
                    )
                except TransactionVisibilityTimedOut as e:
                    # A private or unhealthy read provider can hide a broadcast
                    # temporarily. Let the existing confirmation/rebroadcast
                    # loop handle this just as it did after the former sleep.
                    logger.warning(
                        "Transaction visibility gate timed out, continuing with broadcast batch, tx_hash=%s, error=%s",
                        tx.hash.hex(),
                        e,
                    )
                except Exception as e:
                    # Provider implementations can raise exceptions outside the
                    # normal JSON-RPC hierarchy. Visibility is only a best-effort
                    # sequencing optimisation and must not interrupt the batch.
                    logger.warning(
                        "Transaction visibility gate failed, continuing with broadcast batch, tx_hash=%s, error=%s",
                        tx.hash.hex(),
                        e,
                    )
            else:
                logger.info(
                    "Transaction visibility wait disabled, tx_hash=%s",
                    tx.hash.hex(),
                )
        else:
            logger.info(
                "Transaction visibility wait skipped after final transaction",
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
                unconfirmed_tx_strs = ", ".join([tx_hash.hex() for tx_hash in unconfirmed_txs])
                try:
                    diagnostic_provider = _get_single_attempt_provider(web3)
                    for tx_hash in unconfirmed_txs:
                        try:
                            tx_data = _fetch_timed_out_transaction(diagnostic_provider, tx_hash)
                            if tx_data:
                                logger.error("Data for transaction %s was %s", tx_hash.hex(), tx_data)
                            else:
                                logger.warning("Node %s missing transaction broadcast %s", get_provider_name(diagnostic_provider), tx_hash.hex())
                        except Exception as e:
                            # Provider-specific transport and RPC failures are
                            # diagnostic data and must not replace the timeout.
                            logger.warning("Could not fetch timed-out transaction %s: %s", tx_hash.hex(), e)
                    gas_diagnostics = format_confirmation_timeout_gas_diagnostics(
                        diagnostic_provider,
                        txs,
                        unconfirmed_txs,
                    )
                except Exception as e:
                    # Timeout diagnostics are best effort and must never mask
                    # the original ConfirmationTimedOut.
                    logger.warning("Could not construct gas-price timeout diagnostics: %s", e)
                    gas_diagnostics = f"Gas price diagnostics unavailable: {type(e).__name__}: {e}"
                raise ConfirmationTimedOut(f"Transaction confirmation failed. Started: {started_at}, timed out after {max_timeout} ({max_timeout.total_seconds()}s). Poll delay: {poll_delay.total_seconds()}s. Still unconfirmed: {unconfirmed_tx_strs}\n{gas_diagnostics}")

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


def _node_providers(web3: Web3) -> list[BaseProvider]:
    """Return the individual RPC nodes behind a connection.

    For a :py:class:`~eth_defi.provider.fallback.FallbackProvider` this is the list of
    child providers; otherwise the single configured provider.  Used to sample each node
    independently when diagnosing a nonce mismatch on an eventually-consistent multi-node
    RPC (e.g. HyperEVM behind Alchemy / Goldsky / dRPC).

    :param web3:
        Web3 connection, ideally backed by a fallback provider.

    :return:
        List of underlying providers, never empty.
    """
    try:
        return list(get_fallback_provider(web3).providers)
    except (AssertionError, AttributeError):
        # Not a fallback provider: single node (e.g. plain Anvil in tests) or an
        # MEV-blocker wrapping a single call provider that has no .providers list.
        return [web3.provider]


def _sample_node(provider: BaseProvider, address: str) -> tuple[int | None, int | None, str | None]:
    """Read one node's latest block number and address nonce.

    Issues raw ``eth_blockNumber`` and ``eth_getTransactionCount`` calls directly against a
    single provider, bypassing the fallback provider so we observe that one node's view.
    Never raises — a diagnostic must not mask the original error — and tolerates JSON-RPC
    ``error`` objects and malformed (e.g. non-hex) responses.

    :param provider:
        A single RPC node provider.

    :param address:
        Hex address whose nonce we want.

    :return:
        Tuple ``(block_number, nonce, error)``.  On success ``error`` is ``None``; on
        failure ``block_number`` and ``nonce`` are ``None`` and ``error`` is a message.
    """
    try:
        block_resp = provider.make_request("eth_blockNumber", [])
        nonce_resp = provider.make_request("eth_getTransactionCount", [address, "latest"])
        if "error" in block_resp or "error" in nonce_resp:
            return None, None, f"json-rpc error: {block_resp.get('error') or nonce_resp.get('error')}"
        return int(block_resp["result"], 16), int(nonce_resp["result"], 16), None
    except (KeyError, ValueError, TypeError) as e:
        # Missing "result", non-hex value, wrong shape
        return None, None, f"malformed response: {e}"
    except Exception as e:  # noqa - diagnostic must stay resilient against any node failure
        return None, None, f"{type(e).__name__}: {e}"


def _sample_all_nodes(web3: Web3, address: str) -> list[dict]:
    """Sample every RPC node for its latest block and the address nonce.

    :param web3:
        Web3 connection.

    :param address:
        Hex address whose nonce we want.

    :return:
        List of dicts with keys ``name``, ``block``, ``nonce``, ``error``.
    """
    samples = []
    for provider in _node_providers(web3):
        block, nonce, error = _sample_node(provider, address)
        samples.append({"name": get_provider_name(provider), "block": block, "nonce": nonce, "error": error})
    return samples


def _authoritative_nonce(samples: list[dict]) -> tuple[int | None, bool, str | None]:
    """Pick the authoritative nonce from per-node samples.

    The node with the highest block number is the most up-to-date and is treated as
    authoritative — a node that is behind cannot veto a node that is ahead.  If several
    nodes share the highest block but disagree on the nonce, the reading is inconsistent
    and must not be trusted (retry instead).

    :param samples:
        Output of :py:func:`_sample_all_nodes`.

    :return:
        Tuple ``(nonce, consistent, authority_name)``.  ``nonce`` is ``None`` and
        ``consistent`` is ``False`` when no node could be sampled or the most-advanced
        nodes disagree.
    """
    ok = [s for s in samples if s["error"] is None and s["block"] is not None]
    if not ok:
        return None, False, None
    max_block = max(s["block"] for s in ok)
    top = [s for s in ok if s["block"] == max_block]
    if len({s["nonce"] for s in top}) > 1:
        # Same height, different nonce -> inconsistent, do not trust
        return None, False, top[0]["name"]
    return top[0]["nonce"], True, top[0]["name"]


def _format_samples(samples: list[dict]) -> str:
    """Render per-node block/nonce samples as an aligned, friendly table."""
    lines = ["Per-node state (nonce mismatch diagnostic):"]
    for s in samples:
        if s["error"]:
            lines.append(f"  {s['name']:<40} ERROR: {s['error']}")
        else:
            lines.append(f"  {s['name']:<40} block={s['block']:<12} nonce={s['nonce']}")
    return "\n".join(lines)


def format_node_block_diagnostic(web3: Web3, address: str) -> str:
    """Friendly per-node latest block + nonce table, for diagnosing lagging RPC nodes.

    On a nonce mismatch this lets an operator immediately see whether one fallback node is
    behind the others (the usual cause of a false positive on an eventually-consistent
    multi-node RPC).

    :param web3:
        Web3 connection.

    :param address:
        Hex address whose nonce is in question.

    :return:
        Multi-line, aligned diagnostic string.
    """
    return _format_samples(_sample_all_nodes(web3, address))


def check_nonce_mismatch(
    web3: Web3,
    txs: Collection[SignedTxType],
    retries: int = 3,
    retry_delay: datetime.timedelta = datetime.timedelta(seconds=1),
):
    """Check for nonce re-use issues.

    Compare pre-signed transactions with on-chain addresses' nonce states.  The nonce is
    owned solely by our internal hot wallet counter, so the on-chain nonce must match our
    expected nonce exactly — strict equality is intentional and preserved.

    The happy path is a single ``get_transaction_count`` read (unchanged).  Only when that
    first read disagrees do we re-sample every fallback node and trust the most up-to-date
    one (highest block number): on an eventually-consistent multi-node RPC a single node can
    lag a transaction behind and return a stale count, which previously crashed the live
    loop.  We raise only if the mismatch persists on the most-advanced node across all
    retries — a genuine desync — and include a per-node block/nonce diagnostic so the
    lagging node is obvious.  See ``deps/web3-ethereum-defi/docs/README-hyperevm-goldsky-failure.md``.

    :param web3:
        Web3 connection, ideally a fallback provider over multiple nodes.

    :param txs:
        Pre-signed transactions to validate.  May span multiple addresses; the lowest nonce
        per address is checked.

    :param retries:
        How many times to re-sample the nodes when the first read mismatches.

    :param retry_delay:
        Sleep between resamples, giving a lagging node time to catch up.  Total added
        wall-clock is bounded by ``(retries - 1) * retry_delay``.

    :raise NonceMismatch:
        If the most up-to-date node still disagrees with our expected nonce after retries,
        or duplicate nonces appear in the same batch.
    """

    #
    # Deterministic local batch validation (independent of RPC reads).
    # Duplicate nonce for the same address is always a bug; nonce gaps are suspicious but
    # may be legitimate (externally-submitted pending txs), so only warn.
    #
    per_address: dict[str, list[int]] = {}
    for tx in txs:
        per_address.setdefault(tx.address, []).append(tx.nonce)
    for address, nonces in per_address.items():
        ordered = sorted(nonces)
        if len(set(nonces)) != len(nonces):
            raise NonceMismatch(f"Duplicate nonce in transaction batch for {address}: {ordered}")
        if any(b != a + 1 for a, b in zip(ordered, ordered[1:])):
            logger.warning("Non-contiguous nonces in transaction batch for %s: %s", address, ordered)

    #
    # We can broadcast for multiple addresses, each address can contain multiple txs.
    # Check the lowest on-chain nonce for each address.
    #

    #: address, starting nonce mappings
    min_nonces = {}
    for tx in txs:
        address = tx.address
        min_nonces[address] = min(tx.nonce, min_nonces.get(address, 9_999_999))

    for address, nonce in min_nonces.items():
        # Happy path: a single read that matches -> done, no extra RPC.
        on_chain_nonce = web3.eth.get_transaction_count(address)
        if on_chain_nonce == nonce:
            continue

        # First read disagrees. This may be a stale read from a single lagging fallback
        # node rather than a genuine desync. Re-sample all nodes and trust the most
        # up-to-date one, retrying to let a lagging node catch up.
        last_samples: list[dict] = []
        authority_nonce: int | None = on_chain_nonce
        authority_name: str | None = get_provider_name(web3.provider)
        resolved = False
        for attempt in range(retries):
            samples = _sample_all_nodes(web3, address)
            last_samples = samples
            sampled_nonce, consistent, sampled_authority = _authoritative_nonce(samples)
            if sampled_authority is not None:
                authority_name = sampled_authority
                # Keep the last known nonce for the message; do not overwrite with None
                # when same-height nodes disagree (the diagnostic table still shows reality).
                if sampled_nonce is not None:
                    authority_nonce = sampled_nonce
            if consistent and sampled_nonce == nonce:
                logger.info(
                    "Nonce mismatch on first read for %s resolved on attempt %d/%d: most up-to-date node %s reports expected nonce %d.\n%s",
                    address,
                    attempt + 1,
                    retries,
                    sampled_authority,
                    nonce,
                    _format_samples(samples),
                )
                resolved = True
                break
            logger.warning(
                "Nonce check attempt %d/%d for %s: expected %d, most-advanced node (%s) reports %s (consistent=%s).\n%s",
                attempt + 1,
                retries,
                address,
                nonce,
                authority_name,
                sampled_nonce,
                consistent,
                _format_samples(samples),
            )
            if attempt < retries - 1:
                time.sleep(retry_delay.total_seconds())

        if resolved:
            continue

        raise NonceMismatch(f"Nonce mismatch for broadcasted transactions.\nAddress {address}, we have signed with nonce {nonce}, but the most up-to-date node ({authority_name}) reports {authority_nonce}.\nPotential reasons include incorrectly shared hot wallet or badly synced hot wallet nonce.\n{_format_samples(last_samples)}")


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
