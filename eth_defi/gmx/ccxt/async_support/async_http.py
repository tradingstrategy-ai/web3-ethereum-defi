"""Async HTTP utilities for GMX API requests."""

import asyncio
import logging
from typing import Any

import aiohttp

from eth_defi.gmx.constants import GMX_API_URLS, GMX_API_URLS_BACKUP

logger = logging.getLogger(__name__)


async def async_make_gmx_api_request(
    chain: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    session: aiohttp.ClientSession | None = None,
    timeout: float = 10.0,
    max_retries: int = 2,
    retry_delay: float = 0.1,
) -> dict[str, Any]:
    """Make async GMX API request with retry logic and failover.

    Async version of eth_defi.gmx.retry.make_gmx_api_request with same behavior.

    Args:
        chain: Chain name (e.g., "arbitrum", "avalanche")
        endpoint: API endpoint path (e.g., "/tokens", "/prices/tickers")
        params: Optional query parameters
        session: Optional aiohttp session for connection pooling
        timeout: HTTP request timeout in seconds
        max_retries: Maximum retry attempts per URL
        retry_delay: Initial delay between retries (exponential backoff)

    Returns:
        Parsed JSON response

    Raises:
        RuntimeError: If all retries and backup attempts fail
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

    # Create session if not provided
    close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        close_session = True

    last_error = None

    try:
        # Try each URL with retries
        for url, url_type in urls_to_try:
            logger.debug("Trying %s GMX API: %s", url_type, url)

            for attempt in range(max_retries):
                try:
                    async with session.get(
                        url,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as response:
                        response.raise_for_status()

                        # Log success if using backup or after retries
                        if url_type == "backup" or attempt > 0:
                            logger.info(
                                "Successfully connected to %s GMX API for %s",
                                url_type,
                                endpoint,
                            )

                        return await response.json()

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        # Exponential backoff: 0.1s, 0.2s
                        delay = retry_delay * (2**attempt)
                        logger.warning(
                            "Attempt %d/%d failed for %s API %s: %s. Retrying in %.1fs...",
                            attempt + 1,
                            max_retries,
                            url_type,
                            endpoint,
                            str(e),
                            delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.warning(
                            "All %d attempts failed for %s API %s: %s",
                            max_retries,
                            url_type,
                            endpoint,
                            str(e),
                        )

        # All URLs and retries exhausted
        error_msg = f"All GMX API requests failed for {endpoint}. Last error: {last_error}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    finally:
        # Close session only if we created it
        if close_session:
            await session.close()
