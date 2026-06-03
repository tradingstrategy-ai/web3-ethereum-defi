"""Transaction receipt visibility helpers for multi-provider Web3 setups."""

import logging
import time

from hexbytes import HexBytes
from requests.exceptions import RequestException
from web3 import Web3
from web3.exceptions import (
    CannotHandleRequest,
    MultipleFailedRequests,
    ProviderConnectionError,
    RequestTimedOut,
    TransactionNotFound,
    TooManyRequests,
    Web3RPCError,
)
from web3.providers import BaseProvider
from web3.types import TxReceipt

from eth_defi.provider.anvil import is_anvil
from eth_defi.provider.fallback import FallbackProvider
from eth_defi.provider.named import get_provider_name


logger = logging.getLogger(__name__)


PROVIDER_READ_EXCEPTIONS = (
    CannotHandleRequest,
    MultipleFailedRequests,
    ProviderConnectionError,
    RequestException,
    RequestTimedOut,
    TransactionNotFound,
    TooManyRequests,
    Web3RPCError,
)
"""Exceptions raised by unavailable or read-restricted JSON-RPC providers."""


class ReceiptVisibilityTimedOut(TimeoutError):
    """Timed out before all read providers could see a transaction receipt."""


class ReceiptVisibilityMismatch(RuntimeError):
    """Read providers returned conflicting receipt data for the same transaction."""


def _is_anvil(web3: Web3) -> bool:
    """Check Anvil without letting read-restricted providers crash the caller."""
    try:
        return is_anvil(web3)
    except PROVIDER_READ_EXCEPTIONS:
        return False


def _get_active_read_provider(provider: BaseProvider) -> BaseProvider:
    """Get the active read provider from direct, fallback, or MEV-wrapped providers."""
    call_provider = getattr(provider, "call_provider", None)
    if call_provider is not None:
        provider = call_provider

    if isinstance(provider, FallbackProvider):
        return provider.get_active_provider()

    return provider


def get_read_providers(web3: Web3) -> list[BaseProvider]:
    """Get JSON-RPC providers used for read calls.

    MEV/sequencer transaction providers are intentionally excluded because some
    sequencers are write-only and do not support receipt or state reads.
    """
    provider = web3.provider

    if _is_anvil(web3):
        return [_get_active_read_provider(provider)]

    call_provider = getattr(provider, "call_provider", None)
    if call_provider is not None:
        provider = call_provider

    if isinstance(provider, FallbackProvider):
        return provider.providers

    return [provider]


def _get_provider_label(index: int, provider: BaseProvider) -> str:
    """Get a stable provider label for diagnostics."""
    return f"{index}:{get_provider_name(provider)}"


def _fetch_raw_receipt(provider: BaseProvider, tx_hash_hex: str) -> dict | None:
    """Fetch raw JSON-RPC receipt data from a provider."""
    response = provider.make_request("eth_getTransactionReceipt", [tx_hash_hex])
    return response.get("result")


def _fetch_raw_block_number(provider: BaseProvider) -> int:
    """Fetch raw JSON-RPC block number from a provider."""
    response = provider.make_request("eth_blockNumber", [])
    result = response.get("result")
    if result is None:
        raise ValueError(f"Provider returned no eth_blockNumber result: {response}")
    return int(result, 16)


def _is_failed_receipt_status(status) -> bool:
    """Check raw or typed receipt status for a failed transaction."""
    if status is None:
        return False
    if isinstance(status, str):
        return status.lower() == "0x0"
    return status == 0 or status is False


def _log_raw_receipt_statuses(tx_hash_hex: str, receipts: dict[str, dict]) -> None:
    """Log failed transaction status from raw provider receipts."""
    for label, receipt in receipts.items():
        status = receipt.get("status")
        if _is_failed_receipt_status(status):
            logger.error(
                "Transaction %s has failed receipt status %s on read provider %s, blockNumber=%s, blockHash=%s",
                tx_hash_hex,
                status,
                label,
                receipt.get("blockNumber"),
                receipt.get("blockHash"),
            )


def _log_typed_receipt_status(tx_hash_hex: str, receipt: TxReceipt) -> None:
    """Log failed transaction status from the final typed Web3 receipt."""
    status = receipt.get("status")
    if _is_failed_receipt_status(status):
        logger.error(
            "Transaction %s has failed typed receipt status %s from original Web3 provider",
            tx_hash_hex,
            status,
        )


def _assert_receipts_match(receipts: dict[str, dict], tx_hash_hex: str) -> None:
    """Check all raw JSON-RPC receipts describe the same transaction inclusion."""
    expected_tx_hash = tx_hash_hex.lower()
    reference_label = None
    reference_block_hash = None

    for label, receipt in receipts.items():
        receipt_tx_hash = receipt.get("transactionHash", "").lower()
        block_hash = receipt.get("blockHash", "").lower()

        if receipt_tx_hash != expected_tx_hash:
            raise ReceiptVisibilityMismatch(
                f"Provider {label} returned receipt for transaction {receipt_tx_hash}, expected {expected_tx_hash}"
            )

        if reference_block_hash is None:
            reference_label = label
            reference_block_hash = block_hash
        elif block_hash != reference_block_hash:
            raise ReceiptVisibilityMismatch(
                f"Provider {label} returned blockHash {block_hash}, but provider {reference_label} returned {reference_block_hash}"
            )


def _get_insufficient_confirmations(
    provider_entries: list[tuple[int, BaseProvider]],
    receipts: dict[str, dict],
    confirmation_block_count: int,
) -> list[str]:
    """Return providers whose block height does not yet give enough confirmations.

    This check is best-effort: provider block numbers are read sequentially, so a
    new block may arrive between receipt and block number reads.
    """
    if confirmation_block_count <= 0:
        return []

    insufficient = []
    for index, provider in provider_entries:
        label = _get_provider_label(index, provider)
        receipt = receipts[label]
        receipt_block = int(receipt["blockNumber"], 16)
        try:
            current_block = _fetch_raw_block_number(provider)
        except PROVIDER_READ_EXCEPTIONS as e:
            logger.warning(
                "Could not fetch current block number from read provider while checking transaction confirmations, provider=%s",
                label,
                exc_info=True,
            )
            insufficient.append(f"{label} (block number unavailable: {e})")
            continue
        confirmations = current_block - receipt_block
        if confirmations < confirmation_block_count:
            insufficient.append(f"{label} ({confirmations}/{confirmation_block_count})")

    return insufficient


def wait_for_transaction_receipt_robust(
    web3: Web3,
    tx_hash: HexBytes | str,
    timeout: float = 120.0,
    poll_delay: float = 1.0,
    max_poll_delay: float = 5.0,
    confirmation_block_count: int = 0,
    extra_sleep: float = 0.0,
) -> TxReceipt:
    """Wait until a transaction receipt is visible through all read RPC providers.

    This is stricter than :py:meth:`web3.eth.wait_for_transaction_receipt` for
    live multi-RPC setups. Receipt values fetched directly from providers are raw
    JSON-RPC objects, so comparisons use raw hex strings. For Anvil we use the
    normal Web3 receipt wait path because Anvil has one coherent local state view.
    This helper is safe to call after the transaction is already confirmed
    through the primary provider; it checks read-provider visibility separately.

    :param web3:
        Web3 instance, optionally backed by FallbackProvider or MEVBlockerProvider.
    :param tx_hash:
        Transaction hash to wait for.
    :param timeout:
        Maximum seconds to wait.
    :param poll_delay:
        Initial delay between polling rounds.
    :param max_poll_delay:
        Maximum adaptive delay between polling rounds.
    :param confirmation_block_count:
        How many confirmations each read provider should see. This is best-effort
        because provider block numbers are fetched sequentially.
    :param extra_sleep:
        Extra seconds to sleep once after all read providers have seen the
        matching receipt and enough confirmations.
    :return:
        Typed receipt from the original Web3 instance, preserving middleware behaviour.
    """
    assert timeout > 0, f"timeout must be positive, got {timeout}"
    assert poll_delay > 0, f"poll_delay must be positive, got {poll_delay}"
    assert max_poll_delay >= poll_delay, f"max_poll_delay must be >= poll_delay, got {max_poll_delay} < {poll_delay}"
    assert confirmation_block_count >= 0, f"confirmation_block_count must be non-negative, got {confirmation_block_count}"
    assert extra_sleep >= 0, f"extra_sleep must be non-negative, got {extra_sleep}"

    tx_hash_bytes = HexBytes(tx_hash)
    tx_hash_hex = "0x" + tx_hash_bytes.hex()

    if _is_anvil(web3):
        logger.info(
            "Waiting for transaction receipt on Anvil, tx_hash=%s, timeout=%s",
            tx_hash_hex,
            timeout,
        )
        try:
            receipt = web3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=timeout)
        except PROVIDER_READ_EXCEPTIONS:
            logger.error(
                "Failed to wait for transaction receipt on Anvil, tx_hash=%s",
                tx_hash_hex,
                exc_info=True,
            )
            raise
        _log_typed_receipt_status(tx_hash_hex, receipt)
        if extra_sleep > 0:
            logger.info(
                "Sleeping after Anvil transaction receipt confirmation, tx_hash=%s, sleep=%s",
                tx_hash_hex,
                extra_sleep,
            )
            time.sleep(extra_sleep)
        logger.info(
            "Transaction receipt is visible on Anvil, tx_hash=%s, status=%s, blockNumber=%s",
            tx_hash_hex,
            receipt.get("status"),
            receipt.get("blockNumber"),
        )
        return receipt

    providers = get_read_providers(web3)
    provider_entries = list(enumerate(providers))
    deadline = time.monotonic() + timeout
    current_delay = poll_delay
    last_missing: list[str] = []
    last_errors: dict[str, str] = {}
    last_insufficient_confirmations: list[str] = []
    last_mismatch: ReceiptVisibilityMismatch | None = None
    extra_sleep_done = False
    provider_labels = [_get_provider_label(index, provider) for index, provider in provider_entries]

    logger.info(
        "Waiting for transaction receipt visibility on all read providers, tx_hash=%s, providers=%s, timeout=%s, poll_delay=%s, max_poll_delay=%s, confirmation_block_count=%d",
        tx_hash_hex,
        provider_labels,
        timeout,
        poll_delay,
        max_poll_delay,
        confirmation_block_count,
    )

    poll_round = 0
    while time.monotonic() < deadline:
        poll_round += 1
        receipts: dict[str, dict] = {}
        missing = []
        errors = {}

        logger.debug(
            "Polling transaction receipt visibility, tx_hash=%s, round=%d, provider_count=%d",
            tx_hash_hex,
            poll_round,
            len(provider_entries),
        )

        for index, provider in provider_entries:
            label = _get_provider_label(index, provider)
            logger.debug(
                "Requesting transaction receipt from read provider, tx_hash=%s, provider=%s",
                tx_hash_hex,
                label,
            )
            try:
                receipt = _fetch_raw_receipt(provider, tx_hash_hex)
            except PROVIDER_READ_EXCEPTIONS as e:
                errors[label] = str(e)
                missing.append(label)
                logger.warning(
                    "Could not fetch transaction receipt from read provider, tx_hash=%s, provider=%s",
                    tx_hash_hex,
                    label,
                    exc_info=True,
                )
                continue

            if receipt is None:
                missing.append(label)
                logger.debug(
                    "Read provider does not yet see transaction receipt, tx_hash=%s, provider=%s",
                    tx_hash_hex,
                    label,
                )
            else:
                receipts[label] = receipt
                logger.debug(
                    "Read provider sees transaction receipt, tx_hash=%s, provider=%s, blockNumber=%s, blockHash=%s, status=%s",
                    tx_hash_hex,
                    label,
                    receipt.get("blockNumber"),
                    receipt.get("blockHash"),
                    receipt.get("status"),
                )

        if not missing and len(receipts) == len(provider_entries):
            try:
                _assert_receipts_match(receipts, tx_hash_hex)
            except ReceiptVisibilityMismatch as e:
                last_mismatch = e
                logger.warning(
                    "Transaction receipt data mismatch across read providers, retrying until timeout, tx_hash=%s",
                    tx_hash_hex,
                    exc_info=True,
                )
            else:
                last_mismatch = None
                _log_raw_receipt_statuses(tx_hash_hex, receipts)
                last_insufficient_confirmations = _get_insufficient_confirmations(
                    provider_entries,
                    receipts,
                    confirmation_block_count,
                )
                if not last_insufficient_confirmations:
                    if extra_sleep > 0 and not extra_sleep_done:
                        logger.info(
                            "Sleeping after transaction receipt visibility on all read providers, tx_hash=%s, sleep=%s",
                            tx_hash_hex,
                            extra_sleep,
                        )
                        time.sleep(extra_sleep)
                        extra_sleep_done = True

                    # Return through the original Web3 instance so callers get the
                    # typed receipt and any middleware transformations they expect.
                    logger.info(
                        "Transaction receipt is visible on all read providers, fetching typed receipt through original Web3, tx_hash=%s",
                        tx_hash_hex,
                    )
                    try:
                        typed_receipt = web3.eth.get_transaction_receipt(tx_hash_bytes)
                    except PROVIDER_READ_EXCEPTIONS as e:
                        errors["original-web3"] = str(e)
                        missing.append("original-web3")
                        logger.warning(
                            "Failed to fetch typed transaction receipt through original Web3 after raw provider checks, retrying until timeout, tx_hash=%s",
                            tx_hash_hex,
                            exc_info=True,
                        )
                    else:
                        _log_typed_receipt_status(tx_hash_hex, typed_receipt)
                        logger.info(
                            "Transaction receipt robust wait complete, tx_hash=%s, status=%s, blockNumber=%s",
                            tx_hash_hex,
                            typed_receipt.get("status"),
                            typed_receipt.get("blockNumber"),
                        )
                        return typed_receipt
                else:
                    logger.debug(
                        "Transaction receipt is visible on all read providers but confirmations are insufficient, tx_hash=%s, insufficient_confirmations=%s",
                        tx_hash_hex,
                        last_insufficient_confirmations,
                    )

        last_missing = missing
        last_errors = errors

        sleep_for = min(current_delay, max(0, deadline - time.monotonic()))
        if sleep_for > 0:
            logger.debug(
                "Transaction receipt not ready on all read providers, tx_hash=%s, missing_providers=%s, provider_errors=%s, insufficient_confirmations=%s, sleep=%s",
                tx_hash_hex,
                last_missing,
                last_errors,
                last_insufficient_confirmations,
                sleep_for,
            )
            time.sleep(sleep_for)
        current_delay = min(max_poll_delay, current_delay * 1.5)

    detail = ", ".join(last_missing) if last_missing else "none"
    error_detail = "; ".join(f"{label}: {error}" for label, error in last_errors.items())
    confirmation_detail = ", ".join(last_insufficient_confirmations)
    extra = ""
    if error_detail:
        extra += f" Last errors: {error_detail}."
    if confirmation_detail:
        extra += f" Insufficient confirmations: {confirmation_detail}."
    if last_mismatch is not None:
        logger.error(
            "Timed out with persistent transaction receipt mismatch across read providers, tx_hash=%s, timeout=%s",
            tx_hash_hex,
            timeout,
        )
        raise last_mismatch
    logger.error(
        "Timed out waiting for transaction receipt visibility on all read providers, tx_hash=%s, timeout=%s, missing_providers=%s, provider_errors=%s, insufficient_confirmations=%s",
        tx_hash_hex,
        timeout,
        last_missing,
        last_errors,
        last_insufficient_confirmations,
    )
    raise ReceiptVisibilityTimedOut(
        f"Timed out after {timeout} seconds waiting for receipt {tx_hash_hex} on all read providers. Missing providers: {detail}.{extra}"
    )
