"""HTTP session management for Core3 API.

This module provides session creation with retry logic and rate limiting
for Core3 Projects Data API requests.

Rate limiting is thread-safe using SQLite backend, so the session can be
shared across multiple threads when using ``joblib.Parallel`` or similar.

The :py:class:`Core3Session` carries the API URL and API key so that
downstream functions do not need separate arguments on every call.

For API fetch helpers, see :py:mod:`eth_defi.core3.api`.
"""

import logging
import os
from pathlib import Path

from pyrate_limiter import SQLiteBucket
from requests import Session
from requests_ratelimiter import LimiterAdapter

from eth_defi.core3.constants import (
    CORE3_API_URL,
    CORE3_DEFAULT_REQUESTS_PER_SECOND,
    CORE3_RATE_LIMIT_SQLITE_DATABASE,
    CORE3_USER_AGENT,
)
from eth_defi.velvet.logging_retry import LoggingRetry

logger = logging.getLogger(__name__)

#: Default number of retries for API requests
DEFAULT_RETRIES: int = 5

#: Default backoff factor for retries (seconds)
DEFAULT_BACKOFF_FACTOR: float = 0.5


class Core3Session(Session):
    """A :py:class:`requests.Session` subclass for Core3 API.

    Carries the API URL and API key, and sets required default headers
    (``User-Agent`` and ``x-api-key``). All Core3 fetch functions accept
    a ``Core3Session`` and read :py:attr:`api_url` from it.

    Use :py:func:`create_core3_session` to create instances.
    """

    #: Core3 API base URL (e.g. ``https://api.core3.io/projects_data``).
    api_url: str

    #: Core3 API key (prefixed ``core3_``).
    api_key: str

    def __init__(self, api_url: str = CORE3_API_URL, api_key: str | None = None):
        super().__init__()
        self.api_url = api_url
        if api_key is None:
            api_key = os.environ.get("CORE3_API_KEY")
        assert api_key, "CORE3_API_KEY environment variable or api_key parameter is required"
        self.api_key = api_key
        self.headers.update(
            {
                "User-Agent": CORE3_USER_AGENT,
                "x-api-key": api_key,
            }
        )

    def __repr__(self) -> str:
        return f"<Core3Session api_url={self.api_url!r}>"


def create_core3_session(
    api_url: str = CORE3_API_URL,
    api_key: str | None = None,
    retries: int = DEFAULT_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    requests_per_second: float = CORE3_DEFAULT_REQUESTS_PER_SECOND,
    pool_maxsize: int = 32,
    rate_limit_db_path: Path = CORE3_RATE_LIMIT_SQLITE_DATABASE,
) -> Core3Session:
    """Create a :py:class:`Core3Session` configured for Core3 API.

    The session is configured with:

    - The API URL and API key stored on the session
    - Rate limiting to respect Cloudflare throttling (thread-safe via SQLite)
    - Retry logic for handling transient errors using exponential backoff

    The rate limiter uses SQLite backend for thread-safe coordination across
    multiple threads (e.g., when using ``joblib.Parallel`` with threading backend).

    Example::

        from eth_defi.core3.session import create_core3_session

        session = create_core3_session()

    :param api_url:
        Core3 API base URL. Defaults to
        :py:data:`~eth_defi.core3.constants.CORE3_API_URL`.
    :param api_key:
        Core3 API key. If ``None``, reads from ``CORE3_API_KEY`` env var.
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
    :return:
        Configured :py:class:`Core3Session` with rate limiting and retry logic.
    """
    rate_limit_db_path.parent.mkdir(parents=True, exist_ok=True)

    session = Core3Session(api_url=api_url, api_key=api_key)

    retry_policy = LoggingRetry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
        logger=logger,
    )

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


# Backwards-compatible re-exports — fetch helpers moved to eth_defi.core3.api
from eth_defi.core3.api import (  # noqa: E402, F401
    fetch_index_pol_history,
    fetch_index_pol_history_incremental,
    fetch_pol_category_history,
    fetch_pol_category_history_incremental,
    fetch_pol_history,
    fetch_pol_history_incremental,
    fetch_project_detail,
    fetch_project_list,
    fetch_section_detail,
)
