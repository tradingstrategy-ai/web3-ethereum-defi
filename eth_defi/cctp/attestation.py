"""Circle CCTP V2 attestation service client.

Poll Circle's Iris API for burn attestations needed to complete
cross-chain USDC transfers.

After calling ``depositForBurn()`` on the source chain, you must wait for
Circle's attestation service to sign the burn event. This module provides
utilities to poll for and retrieve the attestation.

Example::

    from eth_defi.cctp.attestation import fetch_attestation
    from eth_defi.cctp.constants import CCTP_DOMAIN_ETHEREUM

    attestation = fetch_attestation(
        source_domain=CCTP_DOMAIN_ETHEREUM,
        transaction_hash="0x...",
        timeout=300.0,
    )

    # Use attestation.message and attestation.attestation
    # with prepare_receive_message() on the destination chain
"""

import logging
import time
from dataclasses import dataclass
from typing import Callable

import requests

from eth_defi.cctp.constants import IRIS_API_BASE_URL

logger = logging.getLogger(__name__)

#: HTTP 404 status code indicating resource not found
HTTP_NOT_FOUND = 404

#: Range CCTP explorer base URL for transaction status lookup
CCTP_EXPLORER_BASE_URL = "https://usdc.range.org/status"

#: CCTP domain ID → Range explorer chain slug
_DOMAIN_TO_EXPLORER_CHAIN: dict[int, str] = {
    0: "ethereum",
    3: "arbitrum",
    6: "base",
    7: "polygon",
}


def _cctp_explorer_url(source_domain: int, transaction_hash: str) -> str | None:
    """Build a Range CCTP explorer URL for a burn transaction, or None if unknown chain."""
    chain = _DOMAIN_TO_EXPLORER_CHAIN.get(source_domain)
    if chain is None:
        return None
    return f"{CCTP_EXPLORER_BASE_URL}?id={chain}/{transaction_hash}"


@dataclass(slots=True)
class CCTPAttestation:
    """Attestation data for a CCTP burn event.

    Contains the signed message and attestation needed to call
    ``receiveMessage()`` on the destination chain's MessageTransmitterV2.
    """

    #: The CCTP message bytes to relay to the destination chain
    message: bytes

    #: The signed attestation bytes from Circle's Iris service
    attestation: bytes

    #: Status from Iris API (e.g. "complete")
    status: str


def fetch_attestation(
    source_domain: int,
    transaction_hash: str,
    timeout: float = 300.0,
    poll_interval: float = 5.0,
    api_base_url: str = IRIS_API_BASE_URL,
    on_phase_change: Callable[[str, int], None] | None = None,
) -> CCTPAttestation:
    """Poll the Iris API until attestation is ready or timeout.

    Circle's Iris service observes burn events on the source chain and
    produces a cryptographic attestation after block finality is reached.
    This function polls until the attestation is available.

    The attestation goes through these Iris API statuses:

    - **404** — transaction not yet indexed by Circle
    - **pending_confirmations** — burn detected, waiting for block finality
    - **complete** — attestation signed and ready

    :param source_domain:
        CCTP domain ID of the source chain (e.g. 0 for Ethereum).

    :param transaction_hash:
        Transaction hash of the ``depositForBurn()`` call on the source chain.

    :param timeout:
        Maximum seconds to wait for attestation. Default 5 minutes.

    :param poll_interval:
        Seconds between polling attempts. Default 5 seconds.

    :param api_base_url:
        Iris API base URL. Defaults to mainnet.

    :param on_phase_change:
        Optional callback invoked on every poll attempt.
        Receives ``(status, attempt)`` where *status* is one of
        ``"waiting_for_indexing"``, ``"pending_confirmations"``, or
        ``"complete"`` and *attempt* is the 1-based poll count.
        Used by :func:`~eth_defi.cctp.bridge.bridge_usdc_cctp_parallel`
        for progress bar updates.

    :return:
        :class:`CCTPAttestation` with message and attestation bytes.

    :raises TimeoutError:
        If attestation is not ready within the timeout period.

    :raises requests.HTTPError:
        If the Iris API returns a non-retryable error response.
    """
    # Iris API requires 0x-prefixed transaction hash
    if not transaction_hash.startswith("0x"):
        transaction_hash = f"0x{transaction_hash}"

    from eth_defi.cctp.constants import CCTP_DOMAIN_NAMES

    domain_name = CCTP_DOMAIN_NAMES.get(source_domain, f"domain-{source_domain}")
    url = f"{api_base_url}/v2/messages/{source_domain}?transactionHash={transaction_hash}"
    explorer_url = _cctp_explorer_url(source_domain, transaction_hash)

    explorer_suffix = f"\n  Explorer: {explorer_url}" if explorer_url else ""
    logger.info(
        "Waiting for CCTP attestation on %s: tx=%s\n  Iris API: %s%s",
        domain_name,
        transaction_hash,
        url,
        explorer_suffix,
    )

    start_time = time.time()
    attempt = 0
    last_phase = None

    def _notify(phase: str):
        nonlocal last_phase
        last_phase = phase
        if on_phase_change is not None:
            on_phase_change(phase, attempt)

    _notify("waiting_for_indexing")

    while True:
        elapsed = time.time() - start_time
        if elapsed >= timeout:
            raise TimeoutError(f"CCTP attestation not ready after {timeout}s for tx {transaction_hash} on {domain_name}")

        attempt += 1
        # Log first attempt at INFO so the user sees the poll started,
        # then DEBUG to avoid drowning out the tqdm progress bar.
        log_level = logging.INFO if attempt == 1 else logging.DEBUG
        logger.log(
            log_level,
            "Polling CCTP attestation: %s (domain %s), tx=%s, attempt=%d, elapsed=%.1fs, status=%s",
            domain_name,
            source_domain,
            transaction_hash,
            attempt,
            elapsed,
            last_phase or "unknown",
        )

        response = requests.get(url, timeout=30)

        # Iris API returns 404 when the transaction is not yet indexed;
        # treat it as "pending" and retry.
        if response.status_code == HTTP_NOT_FOUND:
            _notify("waiting_for_indexing")
            logger.debug("Attestation not yet indexed (404) for %s, retrying...", domain_name)
            time.sleep(poll_interval)
            continue

        response.raise_for_status()

        data = response.json()
        messages = data.get("messages", [])

        if messages:
            msg = messages[0]
            status = msg.get("status", "")
            attestation_hex = msg.get("attestation")

            if status == "complete" and attestation_hex and attestation_hex != "PENDING":
                _notify("complete")
                logger.info(
                    "Attestation complete for %s after %d attempts (%.1fs): tx=%s",
                    domain_name,
                    attempt,
                    elapsed,
                    transaction_hash,
                )
                message_hex = msg.get("message", "")
                return CCTPAttestation(
                    message=bytes.fromhex(message_hex.replace("0x", "")),
                    attestation=bytes.fromhex(attestation_hex.replace("0x", "")),
                    status=status,
                )

            _notify(status)
            logger.debug(
                "Attestation status for %s: %s (waiting for 'complete')",
                domain_name,
                status,
            )

        time.sleep(poll_interval)


def is_attestation_complete(
    source_domain: int,
    transaction_hash: str,
    api_base_url: str = IRIS_API_BASE_URL,
) -> bool:
    """One-shot check if attestation is ready.

    :param source_domain:
        CCTP domain ID of the source chain.

    :param transaction_hash:
        Transaction hash of the ``depositForBurn()`` call.

    :param api_base_url:
        Iris API base URL.

    :return:
        ``True`` if attestation is complete and available.
    """
    if not transaction_hash.startswith("0x"):
        transaction_hash = f"0x{transaction_hash}"

    url = f"{api_base_url}/v2/messages/{source_domain}?transactionHash={transaction_hash}"

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        messages = data.get("messages", [])
        if messages:
            msg = messages[0]
            return msg.get("status") == "complete" and msg.get("attestation") not in {None, "PENDING"}
    except requests.RequestException:
        logger.warning(
            "Failed to check attestation status for tx %s",
            transaction_hash,
            exc_info=True,
        )

    return False
