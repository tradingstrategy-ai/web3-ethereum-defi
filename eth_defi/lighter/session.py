"""HTTP session management for Lighter API.

This module provides session creation with retry logic and rate limiting
for Lighter API requests.

Rate limiting is thread-safe using SQLite backend, so the session can be
shared across multiple threads when using ``joblib.Parallel`` or similar.

The :py:class:`LighterSession` carries the API URL so that downstream
functions do not need a separate ``api_url`` argument.
"""

import logging
from pathlib import Path

from pyrate_limiter import SQLiteBucket
from requests import Session
from requests_ratelimiter import LimiterAdapter

from eth_defi.lighter.constants import LIGHTER_API_URL, LIGHTER_DEFAULT_REQUESTS_PER_SECOND
from eth_defi.velvet.logging_retry import LoggingRetry

logger = logging.getLogger(__name__)

#: Default SQLite database path for rate limiting state.
#:
#: Using SQLite ensures thread-safe rate limiting across multiple threads
#: when using ``joblib.Parallel`` or similar parallel processing.
LIGHTER_RATE_LIMIT_SQLITE_DATABASE: Path = Path("~/.tradingstrategy/lighter/rate-limit.sqlite").expanduser()

#: Default number of retries for API requests
DEFAULT_RETRIES: int = 5

#: Default backoff factor for retries (seconds)
DEFAULT_BACKOFF_FACTOR: float = 0.5


class LighterSession(Session):
    """A :py:class:`requests.Session` subclass that carries the Lighter API URL.

    All Lighter API functions accept a ``LighterSession`` and read
    :py:attr:`api_url` from it, removing the need for a separate
    ``api_url`` argument on every call.

    Use :py:func:`create_lighter_session` to create instances.
    """

    #: Lighter API base URL (e.g. ``https://mainnet.zklighter.elliot.ai``).
    api_url: str

    def __init__(self, api_url: str = LIGHTER_API_URL):
        super().__init__()
        self.api_url = api_url

    def __repr__(self) -> str:
        return f"<LighterSession api_url={self.api_url!r}>"


def create_lighter_session(
    api_url: str = LIGHTER_API_URL,
    retries: int = DEFAULT_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    requests_per_second: float = LIGHTER_DEFAULT_REQUESTS_PER_SECOND,
    pool_maxsize: int = 32,
    rate_limit_db_path: Path = LIGHTER_RATE_LIMIT_SQLITE_DATABASE,
) -> LighterSession:
    """Create a :py:class:`LighterSession` configured for Lighter API.

    The session is configured with:

    - The API URL stored in :py:attr:`LighterSession.api_url`
    - Rate limiting to respect Lighter API throttling (thread-safe via SQLite)
    - Retry logic for handling transient errors using exponential backoff

    The rate limiter uses SQLite backend for thread-safe coordination across
    multiple threads (e.g., when using ``joblib.Parallel`` with threading backend).

    Example::

        from eth_defi.lighter.session import create_lighter_session

        session = create_lighter_session()

    :param api_url:
        Lighter API base URL. Defaults to mainnet
        (:py:data:`~eth_defi.lighter.constants.LIGHTER_API_URL`).
    :param retries:
        Maximum number of retry attempts for failed requests.
    :param backoff_factor:
        Backoff factor for exponential retry delays.
    :param requests_per_second:
        Maximum requests per second to avoid rate limiting.
    :param pool_maxsize:
        Maximum number of connections to keep in the connection pool.
        Should be at least as large as ``max_workers`` when using parallel requests.
    :param rate_limit_db_path:
        Path to SQLite database for storing rate limit state.
        Using SQLite ensures thread-safe rate limiting across multiple threads.
    :return:
        Configured :py:class:`LighterSession` with rate limiting and retry logic.
    """
    rate_limit_db_path.parent.mkdir(parents=True, exist_ok=True)

    session = LighterSession(api_url=api_url)

    retry_policy = LoggingRetry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
        logger=logger,
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
