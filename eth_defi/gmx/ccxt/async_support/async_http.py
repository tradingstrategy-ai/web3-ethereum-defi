"""Async HTTP utilities for GMX API requests."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from eth_defi.gmx.constants import GMX_API_URLS, GMX_API_URLS_BACKUP, GMX_API_URLS_FALLBACK, GMX_API_URLS_FALLBACK_2
from eth_defi.gmx.retry import GMXAPIUnavailable, is_retryable_http_status
from eth_defi.gmx.ticker_validation import GMXInvalidPayloadError

logger = logging.getLogger(__name__)


async def async_make_gmx_api_request(  # noqa: PLR0917  # failover driver mirrors the sync signature; kept explicit
    chain: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    session: aiohttp.ClientSession | None = None,
    timeout: float = 10.0,
    max_retries: int = 3,
    retry_delay: float = 0.1,
    validate: Callable[[Any], bool] | None = None,
) -> dict[str, Any]:
    """Make async GMX API request with retry logic and failover.

    Shares 4xx classification, validation, and the ``GMXAPIUnavailable`` final
    exception with :func:`eth_defi.gmx.retry.make_gmx_api_request`, but drives
    four tiers (``primary``, ``backup``, ``fallback``, ``fallback-2`` — no
    ``gmxapi.ai`` v2 tier) with a single cycle and ad-hoc
    ``max_retries``/``retry_delay`` kwargs rather than
    :class:`~eth_defi.gmx.retry.GMXRetryConfig`.

    :param chain: Chain name (e.g., "arbitrum", "avalanche")
    :param endpoint: API endpoint path (e.g., "/prices/tickers")
    :param params: Optional query parameters
    :param session: Optional aiohttp session for connection pooling
    :param timeout: HTTP request timeout in seconds
    :param max_retries: Maximum retry attempts per URL
    :param retry_delay: Initial delay between retries (exponential backoff)
    :param validate: Optional callable returning ``True`` when the payload is valid
    :raises GMXAPIUnavailable: If all URLs and retries are exhausted
    """
    chain_lower = chain.lower()

    urls_to_try = []
    if chain_lower in GMX_API_URLS:
        urls_to_try.append((GMX_API_URLS[chain_lower] + endpoint, "primary"))
    if chain_lower in GMX_API_URLS_BACKUP:
        urls_to_try.append((GMX_API_URLS_BACKUP[chain_lower] + endpoint, "backup"))
    if chain_lower in GMX_API_URLS_FALLBACK:
        urls_to_try.append((GMX_API_URLS_FALLBACK[chain_lower] + endpoint, "fallback"))
    if chain_lower in GMX_API_URLS_FALLBACK_2:
        urls_to_try.append((GMX_API_URLS_FALLBACK_2[chain_lower] + endpoint, "fallback-2"))

    if not urls_to_try:
        raise ValueError(f"No GMX API URLs configured for chain: {chain}")

    close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        close_session = True

    attempts: list[str] = []
    last_error: Exception | None = None

    try:  # noqa: PLR1702  # failover loop mirrors the sync driver's nesting
        for url, url_type in urls_to_try:
            logger.debug("Trying %s GMX API: %s", url_type, url)

            for attempt in range(max_retries):
                try:
                    async with session.get(
                        url,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as response:
                        if response.status >= 400 and not is_retryable_http_status(response.status):  # noqa: PLR2004  # HTTP error threshold literal
                            last_error = aiohttp.ClientResponseError(
                                response.request_info,
                                response.history,
                                status=response.status,
                                message=response.reason,
                            )
                            logger.warning(
                                "GMX %s API non-retryable %d for %s. Failing over.",
                                url_type,
                                response.status,
                                endpoint,
                            )
                            break

                        response.raise_for_status()

                        payload = await response.json()
                        if validate is not None and not validate(payload):
                            last_error = GMXInvalidPayloadError(f"{url_type} returned invalid payload for {endpoint}")
                            logger.warning(
                                "GMX %s API invalid payload for %s. Failing over.",
                                url_type,
                                endpoint,
                            )
                            break

                        if url_type in ("backup", "fallback", "fallback-2") or attempt > 0:  # noqa: PLR6201  # tier-name membership; tuple keeps spec parity with sync driver
                            logger.info(
                                "Successfully connected to %s GMX API for %s",
                                url_type,
                                endpoint,
                            )

                        return payload

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_error = e
                    if attempt < max_retries - 1:
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

            attempts.append(f"{url_type}: {last_error}")

        logger.error(
            "GMX API unavailable for %s on %s: %s",
            endpoint,
            chain,
            "; ".join(attempts),
        )
        raise GMXAPIUnavailable(chain, endpoint, attempts)

    finally:
        if close_session:
            await session.close()
