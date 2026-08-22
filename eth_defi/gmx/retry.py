"""
GMX API Retry and Failover Logic

Centralised retry and backup failover handling for all GMX API calls.

HTTP status codes (408, 429, 400, 500, ...) are the domain vocabulary here;
the magic-number lint is silenced per-line on the status-code comparisons.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

from eth_defi.gmx.constants import (
    GMX_API_URLS,
    GMX_API_URLS_BACKUP,
    GMX_API_URLS_FALLBACK,
    GMX_API_URLS_FALLBACK_2,
    GMX_API_URLS_FALLBACK_3,
)
from eth_defi.gmx.ticker_validation import GMXInvalidPayloadError

logger = logging.getLogger(__name__)


def is_retryable_http_status(status_code: int) -> bool:
    """Return ``True`` if an HTTP status should be retried.

    5xx, 408 (request timeout) and 429 (rate limit) are transient and retried
    with backoff. Other 4xx (404, 400, 403, ...) are permanent for that
    endpoint and fail over immediately without consuming the backoff budget.

    :param status_code:
        HTTP status code from the response.
    :return:
        ``True`` when the status is retryable; ``False`` otherwise.
    """
    if status_code in {408, 429}:
        return True
    if 400 <= status_code < 500:  # noqa: PLR2004  # HTTP status code range literal
        return False
    return True


class GMXAPIUnavailable(RuntimeError):  # noqa: N818  # Name specified by the failover plan and public API; not an Error suffix
    """Raised when every GMX API endpoint fails for a request.

    Carries a per-endpoint attempt summary so callers and logs can distinguish
    "GMX is down" from "you asked for something that does not exist".
    Subclasses :class:`RuntimeError` so existing ``except RuntimeError``
    handlers keep working.
    """

    #: Human-readable summary of each endpoint tried and why it failed.
    attempts: tuple[str, ...]

    def __init__(self, chain: str, endpoint: str, attempts: tuple[str, ...] | list[str]) -> None:
        """Initialise the exception.

        :param chain:
            Chain name the request was for.
        :param endpoint:
            API endpoint that failed (e.g. ``"/prices/tickers"``).
        :param attempts:
            Per-endpoint failure summaries collected during failover.
        """
        self.attempts = tuple(attempts)
        detail = "; ".join(self.attempts) if self.attempts else "no endpoints tried"
        super().__init__(f"GMX API unavailable for {endpoint} on {chain}: {detail}")


@dataclass(slots=True)
class GMXRetryConfig:
    """Configuration for GMX API retry and failover behaviour.

    Controls how aggressively the GMX API client retries failed requests
    across multiple endpoints. Production defaults are tuned for reliability;
    tests should use :func:`get_test_retry_config` for faster feedback.

    .. code-block:: python

        # Production (default)
        config = GMXRetryConfig()

        # Fast-fail for tests
        config = GMXRetryConfig.create_test_config()
    """

    #: Maximum retry attempts per endpoint (primary, backup, fallback, fallback-2)
    max_retries: int = 3

    #: Initial delay in seconds between retries (grows with backoff)
    initial_delay: float = 2.0

    #: Maximum delay cap in seconds for exponential backoff
    max_delay: float = 30.0

    #: Multiplier applied to delay after each failed attempt
    backoff_multiplier: float = 2.0

    #: Number of full cycles through all endpoints before giving up
    full_cycle_retries: int = 2

    #: Minimum number of tickers a ``/prices/tickers`` payload must contain to
    #: pass validation (live Arbitrum count is ~124).
    min_expected_tickers: int = 100

    #: Maximum age (seconds) of a last-known-good snapshot that may be served
    #: when ``allow_stale_prices`` is enabled.
    max_stale_seconds: float = 120.0

    #: Serve a last-known-good snapshot on read-only paths when every endpoint
    #: fails. Default ``False``: existing behaviour unchanged until a consumer
    #: opts in. Never applies to signed-price paths.
    allow_stale_prices: bool = False

    @classmethod
    def create_test_config(cls) -> "GMXRetryConfig":
        """Create a retry config tuned for fast test feedback.

        Reduces retries and delays so tests fail quickly instead of
        burning minutes on unreachable API endpoints.
        """
        return cls(
            max_retries=1,
            initial_delay=0.1,
            max_delay=1.0,
            backoff_multiplier=2.0,
            full_cycle_retries=1,
        )


#: Default production retry configuration
DEFAULT_RETRY_CONFIG = GMXRetryConfig()


def _try_api_with_retries(  # noqa: PLR0917  # endpoint-retry state passed positionally; pre-existing signature style
    base_url: str,
    endpoint: str,
    params: dict | None,
    timeout: float,
    retry_config: GMXRetryConfig,
    api_name: str,
    validate: Callable[[Any], bool] | None = None,
) -> tuple[dict | None, Exception | None]:
    """Try API endpoint with retries, exponential backoff, and validation.

    A non-retryable 4xx or an invalid payload fails over immediately (no
    backoff). A retryable failure (5xx, 408, 429, transport error) is retried
    with exponential backoff.

    :param base_url:
        Base URL of the API
    :param endpoint:
        API endpoint path
    :param params:
        Optional query parameters
    :param timeout:
        Request timeout in seconds
    :param retry_config:
        Retry behaviour configuration
    :param api_name:
        Name for logging (e.g., "primary", "backup")
    :param validate:
        Optional callable taking the parsed payload and returning ``True``
        when valid. A ``False`` result treats that endpoint's response as a
        failure.
    :return:
        Tuple of (result, error). If successful, result is dict and error is None.
        If failed, result is None and error is the last exception.
    """
    delay = retry_config.initial_delay
    last_error: Exception | None = None

    for attempt in range(retry_config.max_retries):
        try:
            url = f"{base_url}{endpoint}"
            response = requests.get(url, params=params, timeout=timeout)

            if response.status_code >= 400 and not is_retryable_http_status(response.status_code):  # noqa: PLR2004  # HTTP error threshold literal
                last_error = requests.HTTPError(
                    f"{response.status_code} {response.reason} for url {url}",
                    response=response,
                )
                logger.warning(
                    "GMX %s API non-retryable %d for %s. Failing over immediately.",
                    api_name,
                    response.status_code,
                    endpoint,
                )
                return None, last_error

            response.raise_for_status()

            payload = response.json()
            if validate is not None and not validate(payload):
                last_error = GMXInvalidPayloadError(f"{api_name} returned invalid payload for {endpoint}")
                logger.warning(
                    "GMX %s API invalid payload for %s. Failing over.",
                    api_name,
                    endpoint,
                )
                return None, last_error

            return payload, None

        except requests.RequestException as e:
            last_error = e
            if attempt < retry_config.max_retries - 1:
                logger.warning(
                    "GMX %s API attempt %d/%d failed: %s. Retrying in %.1fs",
                    api_name,
                    attempt + 1,
                    retry_config.max_retries,
                    e,
                    delay,
                )
                time.sleep(delay)
                delay = min(delay * retry_config.backoff_multiplier, retry_config.max_delay)
            else:
                logger.warning(
                    "GMX %s API failed after %d attempts: %s",
                    api_name,
                    retry_config.max_retries,
                    e,
                )

    return None, last_error


def make_gmx_api_request(  # noqa: PLR0917  # central failover entry point; deprecated kwargs kept for backwards compat
    chain: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    timeout: float = 10.0,
    retry_config: GMXRetryConfig | None = None,
    max_retries: int | None = None,
    retry_delay: float | None = None,
    validate: Callable[[Any], bool] | None = None,
) -> dict[str, Any]:
    """Make a GMX API request with full-cycle retry.

    This is the SINGLE centralised function for all GMX API calls. It handles:

    - Retry with exponential backoff per endpoint
    - Automatic failover from primary to backup to fallback APIs
    - Full-cycle retry: primary → backup → fallback → fallback-2 → wait → repeat

    Retry flow:

    1. Try primary API (max_retries attempts with exponential backoff)
    2. Try backup API (max_retries attempts with exponential backoff)
    3. Try fallback API (max_retries attempts with exponential backoff)
    4. Try fallback-2 API (max_retries attempts with exponential backoff)
    5. Wait initial_delay, then repeat full cycle
    6. After full_cycle_retries full cycles, raise GMXAPIUnavailable

    :param chain:
        Chain name (e.g., "arbitrum", "avalanche")
    :param endpoint:
        API endpoint path (e.g., "/tokens", "/signed_prices/latest")
    :param params:
        Optional query parameters
    :param timeout:
        HTTP request timeout in seconds
    :param retry_config:
        Retry behaviour configuration. Uses :data:`DEFAULT_RETRY_CONFIG` when ``None``.
    :param max_retries:
        Deprecated. Kept for backwards compatibility but ignored.
    :param retry_delay:
        Deprecated. Kept for backwards compatibility but ignored.
    :param validate:
        Optional callable taking the parsed payload and returning ``True``
        when valid. A ``False`` result treats that endpoint's response as a
        failure.
    :return:
        Parsed JSON response
    :raises GMXAPIUnavailable:
        If all retries and backup attempts fail
    """
    _ = max_retries, retry_delay  # Backwards compat — ignored

    if retry_config is None:
        retry_config = DEFAULT_RETRY_CONFIG

    chain_lower = chain.lower()

    # Get primary, backup, and fallback URLs
    primary_url = GMX_API_URLS.get(chain_lower)
    backup_url = GMX_API_URLS_BACKUP.get(chain_lower)
    fallback_url = GMX_API_URLS_FALLBACK.get(chain_lower)
    fallback_url_2 = GMX_API_URLS_FALLBACK_2.get(chain_lower)
    fallback_url_3 = GMX_API_URLS_FALLBACK_3.get(chain_lower)

    if not primary_url and not backup_url and not fallback_url and not fallback_url_2 and not fallback_url_3:
        raise ValueError(f"No GMX API URLs configured for chain: {chain}")

    last_error = None
    attempts: list[str] = []

    for cycle in range(retry_config.full_cycle_retries):
        if cycle > 0:
            wait_time = retry_config.initial_delay * (retry_config.backoff_multiplier ** (cycle - 1))
            wait_time = min(wait_time, retry_config.max_delay)
            logger.warning(
                "GMX API: Starting retry cycle %d/%d after %.1fs wait",
                cycle + 1,
                retry_config.full_cycle_retries,
                wait_time,
            )
            time.sleep(wait_time)

        # Try primary API
        if primary_url:
            result, error = _try_api_with_retries(
                primary_url,
                endpoint,
                params,
                timeout,
                retry_config,
                "primary",
                validate=validate,
            )
            if result is not None:
                return result
            last_error = error
            attempts.append(f"primary: {error}")

        # Try backup API
        if backup_url:
            result, error = _try_api_with_retries(
                backup_url,
                endpoint,
                params,
                timeout,
                retry_config,
                "backup",
                validate=validate,
            )
            if result is not None:
                return result
            last_error = error
            attempts.append(f"backup: {error}")

        # Try fallback API
        if fallback_url:
            result, error = _try_api_with_retries(
                fallback_url,
                endpoint,
                params,
                timeout,
                retry_config,
                "fallback",
                validate=validate,
            )
            if result is not None:
                return result
            last_error = error
            attempts.append(f"fallback: {error}")

        # Try second fallback API
        if fallback_url_2:
            result, error = _try_api_with_retries(
                fallback_url_2,
                endpoint,
                params,
                timeout,
                retry_config,
                "fallback-2",
                validate=validate,
            )
            if result is not None:
                return result
            last_error = error
            attempts.append(f"fallback-2: {error}")

        # Try third fallback API (gmxapi.ai) — only for v2 paths
        # (e.g. /markets, /tokens, /apy).  The DigitalOcean v1 host serves
        # /prices* and /signed_prices* instead; gmxapi.ai 404s on those.
        price_path = endpoint.startswith("/prices") or endpoint.startswith("/signed_prices")
        if fallback_url_3 and not price_path:
            result, error = _try_api_with_retries(
                fallback_url_3,
                endpoint,
                params,
                timeout,
                retry_config,
                "fallback-3",
                validate=validate,
            )
            if result is not None:
                return result
            last_error = error
            attempts.append(f"fallback-3: {error}")

    logger.error(
        "GMX API unavailable for %s on %s after %d cycle(s): %s",
        endpoint,
        chain,
        retry_config.full_cycle_retries,
        "; ".join(attempts),
    )
    raise GMXAPIUnavailable(chain, endpoint, attempts) from last_error
