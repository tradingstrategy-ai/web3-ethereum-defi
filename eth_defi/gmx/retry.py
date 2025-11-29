"""
GMX API Retry and Failover Logic

Centralized retry and backup failover handling for all GMX API calls.
"""

import logging
import time
from typing import Optional, Callable, Any

import requests

from eth_defi.gmx.constants import GMX_API_URLS, GMX_API_URLS_BACKUP

logger = logging.getLogger(__name__)


def make_gmx_api_request(
    chain: str,
    endpoint: str,
    params: Optional[dict[str, Any]] = None,
    timeout: float = 10.0,
    max_retries: int = 2,
    retry_delay: float = 0.1,
) -> dict[str, Any]:
    """
    Make a GMX API request with retry logic and automatic backup failover.

    This is the SINGLE centralized function for all GMX API calls. It handles:
    - Retry with exponential backoff
    - Automatic failover from primary to backup API
    - Proper error logging and reporting

    :param chain: Chain name (e.g., "arbitrum", "avalanche")
    :param endpoint: API endpoint path (e.g., "/tokens", "/signed_prices/latest")
    :param params: Optional query parameters
    :param timeout: HTTP request timeout in seconds
    :param max_retries: Maximum retry attempts per URL
    :param retry_delay: Initial delay between retries (exponential backoff)
    :return: Parsed JSON response
    :raises RuntimeError: If all retries and backup attempts fail
    """
    chain_lower = chain.lower()

    # Build list of URLs to try (primary first, then backup)
    urls_to_try = []
    if chain_lower in GMX_API_URLS:
        urls_to_try.append((GMX_API_URLS[chain_lower] + endpoint, "primary"))
    if chain_lower in GMX_API_URLS_BACKUP:
        urls_to_try.append((GMX_API_URLS_BACKUP[chain_lower] + endpoint, "backup"))

    if not urls_to_try:
        raise ValueError(f"No GMX API URLs configured for chain: {chain}")

    last_error = None

    # Try each URL with retries
    for url, url_type in urls_to_try:
        logger.debug("Trying %s GMX API: %s", url_type, url)

        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=timeout)
                response.raise_for_status()

                # Log success if using backup or after retries
                if url_type == "backup" or attempt > 0:
                    logger.debug("Successfully connected to %s GMX API for %s", url_type, endpoint)

                return response.json()

            except requests.RequestException as e:
                last_error = e
                if attempt < max_retries - 1:
                    # Exponential backoff: 0.1s, 0.2s
                    delay = retry_delay * (2**attempt)
                    logger.warning(
                        "Attempt %d/%d failed for %s API %s: %s. Retrying in %.1fs...",
                        attempt + 1,
                        max_retries,
                        url_type,
                        url,
                        e,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    logger.warning(
                        "All %d attempts failed for %s API %s: %s",
                        max_retries,
                        url_type,
                        url,
                        e,
                    )

    # All URLs and retries failed
    error_msg = f"Failed to connect to GMX API endpoint {endpoint} for chain {chain} after trying all available URLs"
    if last_error:
        error_msg += f". Last error: {last_error}"
    raise RuntimeError(error_msg) from last_error
