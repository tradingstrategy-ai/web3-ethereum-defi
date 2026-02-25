"""
GMX API Retry and Failover Logic

Centralised retry and backup failover handling for all GMX API calls.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from eth_defi.gmx.constants import (
    GMX_API_URLS,
    GMX_API_URLS_BACKUP,
    GMX_API_URLS_FALLBACK,
    GMX_API_URLS_FALLBACK_2,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GMXRetryConfig:
    """Configuration for GMX API retry and failover behaviour.

    Controls how aggressively the GMX API client retries failed requests
    across multiple endpoints. Production defaults are tuned for reliability;
    tests should use :func:`get_test_retry_config` for faster feedback.

    Example:

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


def _try_api_with_retries(
    base_url: str,
    endpoint: str,
    params: dict | None,
    timeout: float,
    retry_config: GMXRetryConfig,
    api_name: str,
) -> tuple[dict | None, Exception | None]:
    """Try API endpoint with retries and exponential backoff.

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
    :return:
        Tuple of (result, error). If successful, result is dict and error is None.
        If failed, result is None and error is the last exception.
    """
    delay = retry_config.initial_delay
    last_error = None

    for attempt in range(retry_config.max_retries):
        try:
            url = f"{base_url}{endpoint}"
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json(), None

        except Exception as e:
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


def make_gmx_api_request(
    chain: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    timeout: float = 10.0,
    retry_config: GMXRetryConfig | None = None,
    max_retries: int | None = None,
    retry_delay: float | None = None,
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
    6. After full_cycle_retries full cycles, raise RuntimeError

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
    :return:
        Parsed JSON response
    :raises RuntimeError:
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

    if not primary_url and not backup_url and not fallback_url and not fallback_url_2:
        raise ValueError(f"No GMX API URLs configured for chain: {chain}")

    last_error = None

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
            )
            if result is not None:
                return result
            last_error = error

        # Try backup API
        if backup_url:
            result, error = _try_api_with_retries(
                backup_url,
                endpoint,
                params,
                timeout,
                retry_config,
                "backup",
            )
            if result is not None:
                return result
            last_error = error

        # Try fallback API
        if fallback_url:
            result, error = _try_api_with_retries(
                fallback_url,
                endpoint,
                params,
                timeout,
                retry_config,
                "fallback",
            )
            if result is not None:
                return result
            last_error = error

        # Try second fallback API
        if fallback_url_2:
            result, error = _try_api_with_retries(
                fallback_url_2,
                endpoint,
                params,
                timeout,
                retry_config,
                "fallback-2",
            )
            if result is not None:
                return result
            last_error = error

    raise RuntimeError(f"Failed to connect to GMX API endpoint {endpoint} for chain {chain} after {retry_config.full_cycle_retries} full cycles. Last error: {last_error}") from last_error
