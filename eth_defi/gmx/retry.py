"""
GMX API Retry and Failover Logic

Centralized retry and backup failover handling for all GMX API calls.
"""

import logging
import time
from typing import Any

import requests

from eth_defi.gmx.constants import (
    GMX_API_BACKOFF_MULTIPLIER,
    GMX_API_FULL_CYCLE_RETRIES,
    GMX_API_INITIAL_DELAY,
    GMX_API_MAX_DELAY,
    GMX_API_MAX_RETRIES,
    GMX_API_URLS,
    GMX_API_URLS_BACKUP,
)

logger = logging.getLogger(__name__)


def _try_api_with_retries(
    base_url: str,
    endpoint: str,
    params: dict | None,
    timeout: float,
    max_retries: int,
    initial_delay: float,
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
    :param max_retries:
        Maximum retry attempts
    :param initial_delay:
        Initial delay between retries
    :param api_name:
        Name for logging (e.g., "primary", "backup")
    :return:
        Tuple of (result, error). If successful, result is dict and error is None.
        If failed, result is None and error is the last exception.
    """
    delay = initial_delay
    last_error = None

    for attempt in range(max_retries):
        try:
            url = f"{base_url}{endpoint}"
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json(), None

        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                logger.warning(
                    "GMX %s API attempt %d/%d failed: %s. Retrying in %.1fs",
                    api_name,
                    attempt + 1,
                    max_retries,
                    e,
                    delay,
                )
                time.sleep(delay)
                delay = min(delay * GMX_API_BACKOFF_MULTIPLIER, GMX_API_MAX_DELAY)
            else:
                logger.warning(
                    "GMX %s API failed after %d attempts: %s",
                    api_name,
                    max_retries,
                    e,
                )

    return None, last_error


def make_gmx_api_request(
    chain: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    timeout: float = 10.0,
    max_retries: int | None = None,
    retry_delay: float | None = None,
) -> dict[str, Any]:
    """Make a GMX API request with full-cycle retry.

    This is the SINGLE centralised function for all GMX API calls. It handles:

    - Retry with exponential backoff per endpoint
    - Automatic failover from primary to backup API
    - Full-cycle retry: primary → backup → wait → repeat

    Retry flow:

    1. Try primary API (GMX_API_MAX_RETRIES attempts with exponential backoff)
    2. Try backup API (GMX_API_MAX_RETRIES attempts with exponential backoff)
    3. Wait GMX_API_INITIAL_DELAY, then repeat full cycle
    4. After GMX_API_FULL_CYCLE_RETRIES full cycles, raise RuntimeError

    :param chain:
        Chain name (e.g., "arbitrum", "avalanche")
    :param endpoint:
        API endpoint path (e.g., "/tokens", "/signed_prices/latest")
    :param params:
        Optional query parameters
    :param timeout:
        HTTP request timeout in seconds
    :param max_retries:
        Deprecated. Kept for backwards compatibility but ignored.
        Retry count is now controlled by GMX_API_MAX_RETRIES constant.
    :param retry_delay:
        Deprecated. Kept for backwards compatibility but ignored.
        Delay is now controlled by GMX_API_INITIAL_DELAY constant.
    :return:
        Parsed JSON response
    :raises RuntimeError:
        If all retries and backup attempts fail
    """
    # Note: max_retries and retry_delay are ignored - using constants instead
    _ = max_retries, retry_delay  # Silence unused variable warnings
    chain_lower = chain.lower()

    # Get primary and backup URLs
    primary_url = GMX_API_URLS.get(chain_lower)
    backup_url = GMX_API_URLS_BACKUP.get(chain_lower)

    if not primary_url and not backup_url:
        raise ValueError(f"No GMX API URLs configured for chain: {chain}")

    last_error = None

    for cycle in range(GMX_API_FULL_CYCLE_RETRIES):
        if cycle > 0:
            wait_time = GMX_API_INITIAL_DELAY * (GMX_API_BACKOFF_MULTIPLIER ** (cycle - 1))
            wait_time = min(wait_time, GMX_API_MAX_DELAY)
            logger.warning(
                "GMX API: Starting retry cycle %d/%d after %.1fs wait",
                cycle + 1,
                GMX_API_FULL_CYCLE_RETRIES,
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
                GMX_API_MAX_RETRIES,
                GMX_API_INITIAL_DELAY,
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
                GMX_API_MAX_RETRIES,
                GMX_API_INITIAL_DELAY,
                "backup",
            )
            if result is not None:
                return result
            last_error = error

    raise RuntimeError(f"Failed to connect to GMX API endpoint {endpoint} for chain {chain} after {GMX_API_FULL_CYCLE_RETRIES} full cycles. Last error: {last_error}") from last_error
