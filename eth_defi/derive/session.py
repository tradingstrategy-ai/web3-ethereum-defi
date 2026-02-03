"""HTTP session management for Derive API.

This module provides session creation with retry logic and rate limiting
for Derive API requests.

Rate limiting is thread-safe using SQLite backend, so the session can be
shared across multiple threads when using ``joblib.Parallel`` or similar.
"""

import logging
from pathlib import Path

from pyrate_limiter import SQLiteBucket
from requests import Session
from requests_ratelimiter import LimiterAdapter

from eth_defi.derive.constants import (
    DEFAULT_BACKOFF_FACTOR,
    DEFAULT_REQUESTS_PER_SECOND,
    DEFAULT_RETRIES,
    DERIVE_RATE_LIMIT_SQLITE_DATABASE,
)
from eth_defi.velvet.logging_retry import LoggingRetry

logger = logging.getLogger(__name__)


def create_derive_session(
    retries: int = DEFAULT_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
    pool_maxsize: int = 32,
    rate_limit_db_path: Path = DERIVE_RATE_LIMIT_SQLITE_DATABASE,
) -> Session:
    """Create a requests Session configured for Derive API.

    The session is configured with:

    - Rate limiting to respect Derive API throttling (thread-safe via SQLite)
    - Retry logic for handling transient errors using exponential backoff

    The rate limiter uses SQLite backend for thread-safe coordination across
    multiple threads (e.g., when using ``joblib.Parallel`` with threading backend).

    Example::

        from eth_defi.derive.session import create_derive_session

        session = create_derive_session()
        response = session.post("https://api.lyra.finance/private/get_account", json={...})

    :param retries:
        Maximum number of retry attempts for failed requests
    :param backoff_factor:
        Backoff factor for exponential retry delays
    :param requests_per_second:
        Maximum requests per second to avoid rate limiting.
        Defaults to 2.0 (conservative estimate).
    :param pool_maxsize:
        Maximum number of connections to keep in the connection pool.
        Should be at least as large as max_workers when using parallel requests.
        Defaults to 32.
    :param rate_limit_db_path:
        Path to SQLite database for storing rate limit state.
        Using SQLite ensures thread-safe rate limiting across multiple threads.
        Defaults to ``~/.tradingstrategy/derive/rate-limit.sqlite``.
    :return:
        Configured requests Session with rate limiting and retry logic
    """
    # Ensure parent directory exists
    rate_limit_db_path.parent.mkdir(parents=True, exist_ok=True)

    session = Session()

    # Need to whitelist POST as retry method as Derive API uses POST for JSON-RPC
    retry_policy = LoggingRetry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
        logger=logger,
        allowed_methods=LoggingRetry.DEFAULT_ALLOWED_METHODS | frozenset(["POST"]),
    )

    # LimiterAdapter combines rate limiting with retry logic.
    # SQLite bucket ensures thread-safe rate limiting across all threads sharing this session.
    adapter = LimiterAdapter(
        per_second=requests_per_second,
        max_retries=retry_policy,
        pool_connections=pool_maxsize,
        pool_maxsize=pool_maxsize,
        bucket_class=SQLiteBucket,
        bucket_kwargs={"path": str(rate_limit_db_path)},
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
