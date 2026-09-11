"""REST helpers for Lighter account activation and API-key registration.

These helpers use Lighter's public REST API and do not require the Lighter SDK
or an API private key. Transaction signing belongs to downstream trading code;
this module only waits for the state created by the Lagoon deployer.

Authoritative Lighter documentation:

- Account creation: https://apidocs.lighter.xyz/docs/create-accounts-programmatically
- API keys: https://apidocs.lighter.xyz/docs/api-keys
"""

import logging
import time
from decimal import Decimal
from http import HTTPStatus
from typing import Any

from eth_typing import HexAddress
from requests import Response
from requests.exceptions import JSONDecodeError, RequestException
from web3 import Web3

from eth_defi.lighter.session import LighterSession
from eth_defi.lighter.valuation import fetch_lighter_account_by_index, parse_lighter_account_equity

logger = logging.getLogger(__name__)

#: Ethereum deposits and secure withdrawals have a documented 1 USDC minimum.
LIGHTER_MIN_MAINNET_USDC = Decimal("1")

#: Seconds to wait before polling public Lighter state again.
LIGHTER_STATE_POLL_SECONDS = 15

#: Tolerance for Lighter API decimal rounding after an L1 USDC deposit.
LIGHTER_COLLATERAL_TOLERANCE = Decimal("0.000010")

#: HTTP status returned while a deposited account is not indexed yet.
HTTP_BAD_REQUEST = 400

#: Lighter error code for a not-yet-indexed account.
LIGHTER_ACCOUNT_NOT_FOUND_CODE = 21100

#: Lighter error code for a registered API key that is not indexed yet.
LIGHTER_API_KEY_NOT_FOUND_CODE = 21109


def _is_transient_lighter_api_error(error: RequestException) -> bool:
    """Check whether a failed Lighter request is safe to retry.

    Connection failures have no response. For HTTP failures, retry only rate
    limits and server errors; permanent client errors remain fail-fast.

    :param error:
        Requests transport or HTTP exception.
    :return:
        ``True`` for connection failures, HTTP 429 and HTTP 5xx responses.
    """
    response = error.response
    return response is None or response.status_code == HTTPStatus.TOO_MANY_REQUESTS or response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR


def _wait_after_transient_lighter_api_error(
    error: RequestException,
    *,
    deadline: float,
    timeout: int,
    operation: str,
) -> None:
    """Pause a polling loop after a transient Lighter API failure.

    The caller retains its original overall deadline. This helper never hides
    permanent HTTP errors and does not add resumable deployment state.

    :param error:
        Requests exception raised by the latest poll.
    :param deadline:
        Monotonic overall polling deadline.
    :param timeout:
        Original overall timeout in seconds, used in the terminal error.
    :param operation:
        Human-readable operation for logs and errors.
    :return:
        ``None`` after the retry delay.
    """
    if not _is_transient_lighter_api_error(error):
        raise error
    if time.monotonic() >= deadline:
        raise TimeoutError(f"{operation} did not complete within {timeout} seconds after transient Lighter API failures") from error
    logger.warning("Transient Lighter API failure while waiting for %s; retrying in %d seconds: %s", operation, LIGHTER_STATE_POLL_SECONDS, error)
    time.sleep(LIGHTER_STATE_POLL_SECONDS)


def _has_lighter_error_code(response: Response, expected_code: int) -> bool:
    """Check a JSON error response for a known Lighter error code.

    Gateways may return HTML or another non-JSON body for an HTTP 400. In that
    case the caller falls through to :meth:`requests.Response.raise_for_status`
    instead of leaking a JSON decoding error into a polling loop.

    :param response:
        Lighter HTTP response.
    :param expected_code:
        Lighter application error code to match.
    :return:
        ``True`` when the response contains the expected code.
    """
    if response.status_code != HTTP_BAD_REQUEST:
        return False
    try:
        data = response.json()
    except JSONDecodeError:
        return False
    return data.get("code") == expected_code


def fetch_lighter_account_index(
    session: LighterSession,
    l1_address: HexAddress | str,
    timeout: float = 30.0,
) -> int | None:
    """Fetch the lowest Lighter account index owned by an L1 address.

    Lighter can return multiple subaccounts for one L1 owner. The SDK path
    previously selected the lowest index, so preserve that deterministic rule.

    Authoritative endpoint documentation:
    https://apidocs.lighter.xyz/reference/accountsbyl1address

    :param session:
        Configured Lighter HTTP session.
    :param l1_address:
        Safe address which owns the Lighter account.
    :param timeout:
        Per-request timeout in seconds.
    :return:
        The lowest account index, or ``None`` until Lighter exposes one.
    """
    response = session.get(
        f"{session.api_url}/api/v1/accountsByL1Address",
        params={"l1_address": Web3.to_checksum_address(l1_address)},
        timeout=timeout,
    )
    if _has_lighter_error_code(response, LIGHTER_ACCOUNT_NOT_FOUND_CODE):
        return None
    response.raise_for_status()
    data = response.json()
    accounts = data.get("sub_accounts") or data.get("subAccounts") or []
    if not accounts:
        return None

    try:
        return min(int(account["index"]) for account in accounts)
    except (KeyError, TypeError, ValueError) as error:
        message = "Lighter accountsByL1Address response contains an invalid account index"
        raise ValueError(message) from error


def wait_for_lighter_account(
    session: LighterSession,
    l1_address: HexAddress | str,
    timeout: int = 900,
) -> int:
    """Wait for Lighter to expose a Safe-owned account.

    An Ethereum L1 deposit is processed asynchronously by Lighter. This wait
    is observable through concise logs and terminates with ``TimeoutError`` if
    the account never becomes visible.

    :param session:
        Configured Lighter HTTP session.
    :param l1_address:
        Safe address which owns the account.
    :param timeout:
        Maximum wait in seconds.
    :return:
        Lighter account index.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            account_index = fetch_lighter_account_index(session, l1_address)
        except RequestException as error:
            _wait_after_transient_lighter_api_error(error, deadline=deadline, timeout=timeout, operation=f"Lighter account creation for {l1_address}")
            continue
        if account_index is not None:
            logger.info("Lighter account index for %s: %d", l1_address, account_index)
            return account_index
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Lighter did not expose an account for {l1_address} within {timeout} seconds")
        logger.info("Waiting for Lighter account creation (%ds)", LIGHTER_STATE_POLL_SECONDS)
        time.sleep(LIGHTER_STATE_POLL_SECONDS)


def wait_for_lighter_collateral(
    session: LighterSession,
    account_index: int,
    expected_usdc: Decimal,
    timeout: int = 900,
) -> Decimal:
    """Wait for a Lighter account to show collateral from an L1 deposit.

    The public account endpoint reports collateral after Lighter has processed
    the mined L1 transaction. A small fixed tolerance accounts for decimal
    display rounding in the public API response.

    :param session:
        Configured Lighter HTTP session.
    :param account_index:
        Lighter account index.
    :param expected_usdc:
        Human-readable collateral amount expected after activation.
    :param timeout:
        Maximum wait in seconds.
    :return:
        Observed collateral in USDC.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            account = fetch_lighter_account_by_index(session, account_index)
        except RequestException as error:
            _wait_after_transient_lighter_api_error(error, deadline=deadline, timeout=timeout, operation=f"collateral for Lighter account {account_index}")
            continue
        equity = parse_lighter_account_equity(account)
        if equity.collateral >= expected_usdc - LIGHTER_COLLATERAL_TOLERANCE:
            logger.info(
                "Lighter collateral credited for account %d: %s USDC, available %s USDC",
                account_index,
                equity.collateral,
                equity.available_balance,
            )
            return equity.collateral
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Lighter collateral did not reach {expected_usdc} USDC within {timeout} seconds; current collateral {equity.collateral}, available {equity.available_balance}")
        logger.info(
            "Waiting for Lighter collateral credit: collateral %s USDC, available %s USDC (%ds)",
            equity.collateral,
            equity.available_balance,
            LIGHTER_STATE_POLL_SECONDS,
        )
        time.sleep(LIGHTER_STATE_POLL_SECONDS)


def _normalise_public_key(public_key: str) -> bytes:
    """Decode a Lighter public-key string without exposing it in errors.

    :param public_key:
        Hexadecimal public key returned by Lighter.
    :return:
        Decoded public-key bytes.
    """
    try:
        return bytes.fromhex(public_key.removeprefix("0x"))
    except ValueError as error:
        message = "Lighter API response contains a malformed public key"
        raise ValueError(message) from error


def fetch_lighter_api_key(
    session: LighterSession,
    account_index: int,
    api_key_index: int,
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """Fetch one Lighter API-key record from the public account API.

    :param session:
        Configured Lighter HTTP session.
    :param account_index:
        Lighter account index.
    :param api_key_index:
        Requested API-key slot.
    :param timeout:
        Per-request timeout in seconds.
    :return:
        Matching API-key response object, or ``None`` if it is not visible.
    """
    response = session.get(
        f"{session.api_url}/api/v1/apikeys",
        params={"account_index": account_index, "api_key_index": api_key_index},
        timeout=timeout,
    )
    if _has_lighter_error_code(response, LIGHTER_API_KEY_NOT_FOUND_CODE):
        return None
    response.raise_for_status()
    data = response.json()
    api_keys = data.get("api_keys") or data.get("apiKeys") or []
    for api_key in api_keys:
        try:
            response_index = api_key.get("api_key_index", api_key.get("apiKeyIndex"))
            if int(response_index) == api_key_index:
                return api_key
        except (AttributeError, TypeError, ValueError) as error:
            message = "Lighter apikeys response contains an invalid API-key index"
            raise ValueError(message) from error
    return None


def wait_for_lighter_api_key(
    session: LighterSession,
    account_index: int,
    api_key_index: int,
    expected_public_key: str,
    timeout: int = 300,
) -> None:
    """Wait until Lighter exposes a registered API key matching a public key.

    :param session:
        Configured Lighter HTTP session.
    :param account_index:
        Lighter account index.
    :param api_key_index:
        Requested API-key slot.
    :param expected_public_key:
        Locally generated public key, encoded as hexadecimal.
    :param timeout:
        Maximum wait in seconds.
    :return:
        ``None`` once the exact key is visible.
    """
    expected = _normalise_public_key(expected_public_key)
    deadline = time.monotonic() + timeout
    while True:
        try:
            api_key = fetch_lighter_api_key(session, account_index, api_key_index)
        except RequestException as error:
            _wait_after_transient_lighter_api_error(error, deadline=deadline, timeout=timeout, operation=f"API key {api_key_index} for Lighter account {account_index}")
            continue
        if api_key is not None:
            public_key = api_key.get("public_key", api_key.get("publicKey"))
            if not isinstance(public_key, str):
                message = "Lighter apikeys response does not contain a public key"
                raise ValueError(message)
            if _normalise_public_key(public_key) == expected:
                logger.info("Lighter API key %d is active for account %d", api_key_index, account_index)
                return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Lighter API key {api_key_index} was not visible for account {account_index} within {timeout} seconds")
        logger.info("Waiting for Lighter API-key registration (%ds)", LIGHTER_STATE_POLL_SECONDS)
        time.sleep(LIGHTER_STATE_POLL_SECONDS)
