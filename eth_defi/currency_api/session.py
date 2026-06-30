"""HTTP session factory for the fawazahmed0 Exchange API.

The endpoints are static JSON files served from a CDN (jsDelivr) with a
Cloudflare Pages fallback, and the provider documents no rate limit. We
therefore only need retry/backoff on the transport, not the SQLite rate-limiter
used by the GRVT/Hyperliquid sessions.

Canonical API documentation: https://github.com/fawazahmed0/exchange-api
"""

import logging

from requests import Session
from requests.adapters import HTTPAdapter

from eth_defi.logging_retry import LoggingRetry

logger = logging.getLogger(__name__)

#: Default number of transport-level retries.
DEFAULT_RETRIES = 5

#: Default exponential backoff factor between retries.
DEFAULT_BACKOFF_FACTOR = 0.5

#: Default connection pool size.
DEFAULT_POOL_MAXSIZE = 16


def create_currency_api_session(
    retries: int = DEFAULT_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    pool_maxsize: int = DEFAULT_POOL_MAXSIZE,
) -> Session:
    """Create a :py:class:`requests.Session` configured for the Exchange API.

    Installs an :py:class:`~eth_defi.logging_retry.LoggingRetry` policy with
    exponential backoff that retries on ``429`` and ``5xx`` responses, and a
    connection pool sized for the threaded scanner. Host fallback
    (jsDelivr → pages.dev) is handled by the client, not here.

    :param retries:
        Maximum number of transport-level retries per request.
    :param backoff_factor:
        Exponential backoff factor passed to the retry policy.
    :param pool_maxsize:
        Connection pool size; should be >= the scanner ``max_workers``.
    :return:
        A configured session. Safe to share across threads.
    """
    session = Session()

    retry_policy = LoggingRetry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
        logger=logger,
    )

    adapter = HTTPAdapter(
        max_retries=retry_policy,
        pool_connections=pool_maxsize,
        pool_maxsize=pool_maxsize,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session
