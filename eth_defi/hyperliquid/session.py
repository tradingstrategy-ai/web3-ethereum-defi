"""HTTP session management for Hyperliquid API.

This module provides session creation with retry logic and rate limiting
for Hyperliquid API requests.

Rate limiting is thread-safe using SQLite backend, so the session can be
shared across multiple threads when using ``joblib.Parallel`` or similar.

The :py:class:`HyperliquidSession` carries the API URL so that downstream
functions do not need a separate ``server_url`` argument.
"""

import logging
from pathlib import Path

from pyrate_limiter import SQLiteBucket
from requests import Session
from requests_ratelimiter import LimiterAdapter

from eth_defi.velvet.logging_retry import LoggingRetry

logger = logging.getLogger(__name__)

#: Hyperliquid mainnet API URL.
HYPERLIQUID_API_URL: str = "https://api.hyperliquid.xyz"

#: Hyperliquid testnet API URL.
HYPERLIQUID_TESTNET_API_URL: str = "https://api.hyperliquid-testnet.xyz"

#: Default SQLite database path for rate limiting state.
#:
#: Using SQLite ensures thread-safe rate limiting across multiple threads
#: when using ``joblib.Parallel`` or similar parallel processing.
HYPERLIQUID_RATE_LIMIT_SQLITE_DATABASE = Path("~/.tradingstrategy/hyperliquid/rate-limit.sqlite").expanduser()

#: Default number of retries for API requests
DEFAULT_RETRIES = 5

#: Default backoff factor for retries (seconds)
DEFAULT_BACKOFF_FACTOR = 0.5

#: Default rate limit for Hyperliquid API requests per second.
#:
#: Hyperliquid has a limit of 1200 weight per minute per IP.
#: Most info endpoints have weight 20, so: 1200 / 20 = 60 requests/minute = 1 request/second.
#:
#: See https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits
DEFAULT_REQUESTS_PER_SECOND = 1.0


class HyperliquidSession(Session):
    """A :py:class:`requests.Session` subclass that carries the Hyperliquid API URL.

    All Hyperliquid API functions accept a ``HyperliquidSession`` and read
    :py:attr:`api_url` from it, removing the need for a separate
    ``server_url`` argument on every call.

    Use :py:func:`create_hyperliquid_session` to create instances.
    """

    #: Hyperliquid API base URL (e.g. ``https://api.hyperliquid.xyz``).
    api_url: str

    def __init__(self, api_url: str = HYPERLIQUID_API_URL):
        super().__init__()
        self.api_url = api_url

    def __repr__(self) -> str:
        return f"<HyperliquidSession api_url={self.api_url!r}>"


def create_hyperliquid_session(
    api_url: str = HYPERLIQUID_API_URL,
    retries: int = DEFAULT_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
    pool_maxsize: int = 32,
    rate_limit_db_path: Path = HYPERLIQUID_RATE_LIMIT_SQLITE_DATABASE,
) -> HyperliquidSession:
    """Create a :py:class:`HyperliquidSession` configured for Hyperliquid API.

    The session is configured with:

    - The API URL stored in :py:attr:`HyperliquidSession.api_url`
    - Rate limiting to respect Hyperliquid API throttling (thread-safe via SQLite)
    - Retry logic for handling transient errors using exponential backoff

    The rate limiter uses SQLite backend for thread-safe coordination across
    multiple threads (e.g., when using ``joblib.Parallel`` with threading backend).

    - `See rate limits here <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits>`__.

    Example::

        from eth_defi.hyperliquid.session import create_hyperliquid_session, HYPERLIQUID_TESTNET_API_URL

        # Mainnet (default)
        session = create_hyperliquid_session()

        # Testnet
        session = create_hyperliquid_session(api_url=HYPERLIQUID_TESTNET_API_URL)

    :param api_url:
        Hyperliquid API base URL. Defaults to mainnet
        (:py:data:`HYPERLIQUID_API_URL`). Pass
        :py:data:`HYPERLIQUID_TESTNET_API_URL` for testnet.
    :param retries:
        Maximum number of retry attempts for failed requests
    :param backoff_factor:
        Backoff factor for exponential retry delays
    :param requests_per_second:
        Maximum requests per second to avoid rate limiting.
        Defaults to 1.0 based on Hyperliquid's 1200 weight/minute limit
        with most info endpoints having weight 20.
    :param pool_maxsize:
        Maximum number of connections to keep in the connection pool.
        Should be at least as large as max_workers when using parallel requests.
        Defaults to 32.
    :param rate_limit_db_path:
        Path to SQLite database for storing rate limit state.
        Using SQLite ensures thread-safe rate limiting across multiple threads.
        Defaults to ``~/.tradingstrategy/hyperliquid/rate-limit.sqlite``.
    :return:
        Configured :py:class:`HyperliquidSession` with rate limiting and retry logic
    """
    # Ensure parent directory exists
    rate_limit_db_path.parent.mkdir(parents=True, exist_ok=True)

    session = HyperliquidSession(api_url=api_url)

    # Need to whitelist POST as retry method as some Hyperliquid endpoints use POST
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
