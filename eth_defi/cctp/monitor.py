"""CCTP V2 transfer status monitoring via Circle's Iris API.

Programmatically inspect the status of cross-chain USDC transfers
using the ``/v2/messages`` endpoint. Supports both one-shot status
checks and blocking/parallel polling for multiple transfers.

The Iris API tracks transfers from ``depositForBurn()`` through to
attestation readiness. Status progresses as:

1. **404 Not Found** — burn transaction not yet indexed by Iris
2. **pending_confirmations** — burn detected, awaiting block finality
3. **complete** — attestation signed, ready for ``receiveMessage()``

Additionally, the ``delay_reason`` field explains holds on transfers:

- ``insufficient_fee`` — Fast Transfer fee too low
- ``amount_above_max`` — exceeds single-transfer cap
- ``insufficient_allowance_available`` — Fast Transfer allowance exhausted

Example (one-shot status check)::

    from eth_defi.cctp.monitor import fetch_transfer_status

    status = fetch_transfer_status(
        source_domain=3,  # Arbitrum
        transaction_hash="0xabc...",
    )
    if status and status.is_complete:
        print("Transfer ready for receive!")

Example (parallel polling for multiple transfers)::

    from eth_defi.cctp.monitor import CCTPTransferQuery, poll_transfers_parallel

    queries = [
        CCTPTransferQuery(source_domain=3, transaction_hash="0xabc..."),
        CCTPTransferQuery(source_domain=3, transaction_hash="0xdef..."),
    ]
    statuses = poll_transfers_parallel(queries, timeout=1200.0)
    for s in statuses:
        print(f"Domain {s.source_domain} -> {s.dest_domain}: {s.status}")

Rate limits
-----------

The Iris API allows 35 requests/second. Exceeding this triggers a
5-minute block (HTTP 429). When polling multiple transfers, the
default 10-second interval keeps well within limits.

For the full API reference, see:
`Circle CCTP V2 messages endpoint <https://developers.circle.com/api-reference/cctp/all/get-messages-v-2>`_
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import requests

from eth_defi.cctp.constants import IRIS_API_BASE_URL

logger = logging.getLogger(__name__)

#: HTTP 404 status code indicating resource not found
HTTP_NOT_FOUND = 404

#: HTTP 429 status code indicating rate limit exceeded
HTTP_TOO_MANY_REQUESTS = 429


@dataclass(slots=True)
class CCTPTransferStatus:
    """Status of a CCTP transfer from Circle's Iris V2 API.

    Represents the full state of a single cross-chain USDC transfer
    as reported by the ``/v2/messages/{sourceDomainId}`` endpoint.
    """

    #: Transfer status: ``"complete"`` or ``"pending_confirmations"``
    status: str

    #: CCTP source domain ID
    source_domain: int

    #: CCTP destination domain ID (from decoded message, 0 if unavailable)
    dest_domain: int

    #: Signed attestation bytes, or ``None`` if not yet available
    attestation: bytes | None

    #: Raw CCTP message bytes, or ``None`` if not yet available
    message: bytes | None

    #: Event nonce as string, or ``None`` if unavailable
    nonce: str | None

    #: Reason for delay, or ``None`` if no delay.
    #: Values: ``"insufficient_fee"``, ``"amount_above_max"``,
    #: ``"insufficient_allowance_available"``
    delay_reason: str | None

    #: Transaction hash of the burn on the source chain
    transaction_hash: str

    #: CCTP protocol version (1 or 2)
    cctp_version: int | None = None

    @property
    def is_complete(self) -> bool:
        """Whether the attestation is signed and ready for ``receiveMessage()``."""
        return self.status == "complete" and self.attestation is not None

    @property
    def is_pending(self) -> bool:
        """Whether the transfer is still awaiting block finality."""
        return self.status == "pending_confirmations"

    @property
    def is_delayed(self) -> bool:
        """Whether the transfer has a delay reason set."""
        return self.delay_reason is not None


@dataclass(slots=True)
class CCTPTransferQuery:
    """Query parameters for looking up a CCTP transfer.

    Pass to :func:`poll_transfers_parallel` to poll multiple
    transfers concurrently.
    """

    #: CCTP domain ID of the source chain
    source_domain: int

    #: Transaction hash of the ``depositForBurn()`` call
    transaction_hash: str


def fetch_transfer_status(
    source_domain: int,
    transaction_hash: str,
    api_base_url: str = IRIS_API_BASE_URL,
) -> CCTPTransferStatus | None:
    """One-shot check of a CCTP transfer's status.

    Returns ``None`` if the transaction is not yet indexed by Iris (HTTP 404).
    Does not block or retry.

    :param source_domain:
        CCTP domain ID of the source chain (e.g. 3 for Arbitrum).

    :param transaction_hash:
        Transaction hash of the ``depositForBurn()`` call.

    :param api_base_url:
        Iris API base URL. Defaults to mainnet.

    :return:
        :class:`CCTPTransferStatus` or ``None`` if not yet indexed.

    :raises requests.HTTPError:
        If the API returns a non-retryable error (not 404).
    """
    if not transaction_hash.startswith("0x"):
        transaction_hash = f"0x{transaction_hash}"

    url = f"{api_base_url}/v2/messages/{source_domain}?transactionHash={transaction_hash}"

    response = requests.get(url, timeout=30)

    if response.status_code == HTTP_NOT_FOUND:
        return None

    response.raise_for_status()

    data = response.json()
    messages = data.get("messages", [])

    if not messages:
        return None

    msg = messages[0]
    return _parse_transfer_status(msg, source_domain, transaction_hash)


def poll_transfer_status(
    source_domain: int,
    transaction_hash: str,
    timeout: float = 300.0,
    poll_interval: float = 10.0,
    api_base_url: str = IRIS_API_BASE_URL,
) -> CCTPTransferStatus:
    """Block until a CCTP transfer reaches ``complete`` status.

    Polls the Iris V2 API at regular intervals. Handles 404 (not yet indexed)
    and ``pending_confirmations`` by retrying.

    :param source_domain:
        CCTP domain ID of the source chain.

    :param transaction_hash:
        Transaction hash of the ``depositForBurn()`` call.

    :param timeout:
        Maximum seconds to wait. Default 5 minutes.

    :param poll_interval:
        Seconds between polling attempts. Default 10 seconds.

    :param api_base_url:
        Iris API base URL.

    :return:
        :class:`CCTPTransferStatus` with ``is_complete == True``.

    :raises TimeoutError:
        If the transfer does not reach ``complete`` within the timeout.
    """
    if not transaction_hash.startswith("0x"):
        transaction_hash = f"0x{transaction_hash}"

    start_time = time.time()
    attempt = 0

    while True:
        elapsed = time.time() - start_time
        if elapsed >= timeout:
            raise TimeoutError(f"CCTP transfer not complete after {timeout}s for tx {transaction_hash} on domain {source_domain}")

        attempt += 1
        logger.info(
            "Polling CCTP transfer status: domain=%s, tx=%s, attempt=%d, elapsed=%.1fs",
            source_domain,
            transaction_hash,
            attempt,
            elapsed,
        )

        status = fetch_transfer_status(source_domain, transaction_hash, api_base_url)

        if status is None:
            logger.info("Transfer not yet indexed (404), retrying in %.1fs...", poll_interval)
        elif status.is_complete:
            logger.info("Transfer complete: domain=%s, tx=%s", source_domain, transaction_hash)
            return status
        else:
            logger.info(
                "Transfer status: %s, delay_reason: %s, retrying in %.1fs...",
                status.status,
                status.delay_reason,
                poll_interval,
            )

        time.sleep(poll_interval)


def poll_transfers_parallel(
    transfers: list[CCTPTransferQuery],
    timeout: float = 300.0,
    poll_interval: float = 10.0,
    api_base_url: str = IRIS_API_BASE_URL,
    max_workers: int | None = None,
) -> list[CCTPTransferStatus]:
    """Poll multiple CCTP transfers in parallel until all complete.

    Uses threads to poll the Iris API concurrently. Each transfer
    is polled independently; all must reach ``complete`` within
    the timeout or a :class:`TimeoutError` is raised.

    Results are returned in the same order as the input ``transfers`` list.

    :param transfers:
        List of transfer queries to monitor.

    :param timeout:
        Maximum seconds to wait for all transfers. Default 5 minutes.

    :param poll_interval:
        Seconds between polling attempts per transfer. Default 10 seconds.

    :param api_base_url:
        Iris API base URL.

    :param max_workers:
        Maximum number of polling threads. Defaults to number of transfers.

    :return:
        List of completed :class:`CCTPTransferStatus` in input order.

    :raises TimeoutError:
        If any transfer does not complete within the timeout.
    """
    if not transfers:
        return []

    if max_workers is None:
        max_workers = len(transfers)

    logger.info("Polling %d CCTP transfers in parallel (timeout=%.0fs)", len(transfers), timeout)

    # Map future → index for ordered results
    results: dict[int, CCTPTransferStatus] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for idx, query in enumerate(transfers):
            future = executor.submit(
                poll_transfer_status,
                source_domain=query.source_domain,
                transaction_hash=query.transaction_hash,
                timeout=timeout,
                poll_interval=poll_interval,
                api_base_url=api_base_url,
            )
            futures[future] = idx

        for future in as_completed(futures):
            idx = futures[future]
            # Let exceptions propagate (TimeoutError, HTTPError, etc.)
            results[idx] = future.result()

    # Return in input order
    return [results[i] for i in range(len(transfers))]


def _parse_transfer_status(
    msg: dict,
    source_domain: int,
    transaction_hash: str,
) -> CCTPTransferStatus:
    """Parse a single message object from the Iris V2 API response.

    :param msg:
        Message dict from the ``messages`` array in the API response.

    :param source_domain:
        Source domain for context.

    :param transaction_hash:
        Transaction hash for context.

    :return:
        Parsed :class:`CCTPTransferStatus`.
    """
    status = msg.get("status", "")
    attestation_hex = msg.get("attestation")
    message_hex = msg.get("message", "")

    # Parse attestation bytes (None if pending)
    attestation_bytes = None
    if attestation_hex and attestation_hex != "PENDING":
        attestation_bytes = bytes.fromhex(attestation_hex.replace("0x", ""))

    # Parse message bytes (None if empty)
    message_bytes = None
    if message_hex and message_hex != "0x":
        message_bytes = bytes.fromhex(message_hex.replace("0x", ""))

    # Extract destination domain from decoded message if available
    dest_domain = 0
    decoded = msg.get("decodedMessage", {})
    if decoded:
        try:
            dest_domain = int(decoded.get("destinationDomain", 0))
        except (ValueError, TypeError):
            pass

    return CCTPTransferStatus(
        status=status,
        source_domain=source_domain,
        dest_domain=dest_domain,
        attestation=attestation_bytes,
        message=message_bytes,
        nonce=msg.get("eventNonce"),
        delay_reason=msg.get("delayReason"),
        transaction_hash=transaction_hash,
        cctp_version=msg.get("cctpVersion"),
    )
