"""HTTP session management for GRVT API.

This module provides session creation with:

- Rate limiting that is thread-safe using SQLite backend
- Retry logic for handling transient errors using exponential backoff
- Optional API key authentication for private endpoints

Most GRVT vault data is available from public endpoints
(``market-data.grvt.io``) and does not require authentication.
Use :py:func:`create_grvt_session` for public access, or pass
credentials for authenticated access to private endpoints.

Environment variables (optional, for authenticated endpoints):

- ``GRVT_API_KEY``: API key provisioned via the GRVT UI
- ``GRVT_PRIVATE_KEY``: Private key for the Ethereum address linked to the API key
- ``GRVT_TRADING_ACCOUNT_ID``: Trading account ID

Rate limiting is thread-safe using SQLite backend, so the session can be
shared across multiple threads when using ``joblib.Parallel`` or similar.
"""

import logging
import os
from pathlib import Path

from pyrate_limiter import SQLiteBucket
from requests import Session
from requests_ratelimiter import LimiterAdapter

from eth_defi.velvet.logging_retry import LoggingRetry

logger = logging.getLogger(__name__)

#: Default SQLite database path for rate limiting state.
#:
#: Using SQLite ensures thread-safe rate limiting across multiple threads
#: when using ``joblib.Parallel`` or similar parallel processing.
GRVT_RATE_LIMIT_SQLITE_DATABASE = Path("~/.tradingstrategy/grvt/rate-limit.sqlite").expanduser()

#: Default number of retries for API requests
DEFAULT_RETRIES = 5

#: Default backoff factor for retries (seconds)
DEFAULT_BACKOFF_FACTOR = 0.5


def _authenticate_session(
    session: Session,
    api_url: str,
    api_key: str,
    private_key: str,
    trading_account_id: str,
    timeout: float = 30.0,
) -> Session:
    """Perform GRVT API key login and set session cookies.

    POSTs to ``/auth/api_key/login`` with API key credentials.
    The response sets session cookies for subsequent authenticated requests.
    Also sets the ``X-Grvt-Account-Id`` header.

    :param session:
        The requests session to authenticate.
    :param api_url:
        GRVT API base URL.
    :param api_key:
        GRVT API key.
    :param private_key:
        GRVT private key for the Ethereum address linked to the API key.
    :param trading_account_id:
        GRVT trading account ID.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        The authenticated session with cookies set.
    :raises requests.HTTPError:
        If authentication fails.
    """
    login_url = f"{api_url}/auth/api_key/login"
    payload = {
        "api_key": api_key,
    }
    logger.debug("Authenticating with GRVT API at %s", login_url)

    response = session.post(login_url, json=payload, timeout=timeout)
    response.raise_for_status()

    # Set the account ID header for all subsequent requests
    session.headers["X-Grvt-Account-Id"] = trading_account_id

    logger.info("Authenticated with GRVT API at %s", api_url)
    return session


def create_grvt_session(
    api_url: str | None = None,
    api_key: str | None = None,
    private_key: str | None = None,
    trading_account_id: str | None = None,
    retries: int = DEFAULT_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    requests_per_second: float | None = None,
    pool_maxsize: int = 32,
    rate_limit_db_path: Path = GRVT_RATE_LIMIT_SQLITE_DATABASE,
    timeout: float = 30.0,
    authenticate: bool = False,
) -> Session:
    """Create a requests Session configured for the GRVT API.

    By default, creates an unauthenticated session suitable for
    public endpoints (``market-data.grvt.io``). Set ``authenticate=True``
    and provide credentials for private endpoints (``edge.grvt.io``).

    The session is configured with:

    - Rate limiting to respect GRVT API throttling (thread-safe via SQLite)
    - Retry logic for handling transient errors using exponential backoff
    - Optional cookie-based authentication via ``/auth/api_key/login``

    Example::

        from eth_defi.grvt.session import create_grvt_session

        # Public session (no auth needed)
        session = create_grvt_session()

        # Authenticated session
        session = create_grvt_session(authenticate=True)

    :param api_url:
        GRVT API base URL. Falls back to
        :py:data:`~eth_defi.grvt.constants.GRVT_API_URL`.
    :param api_key:
        GRVT API key. Falls back to ``GRVT_API_KEY`` env var.
    :param private_key:
        GRVT private key. Falls back to ``GRVT_PRIVATE_KEY`` env var.
    :param trading_account_id:
        GRVT trading account ID. Falls back to ``GRVT_TRADING_ACCOUNT_ID`` env var.
    :param retries:
        Maximum number of retry attempts for failed requests.
    :param backoff_factor:
        Backoff factor for exponential retry delays.
    :param requests_per_second:
        Maximum requests per second to avoid rate limiting.
        Falls back to
        :py:data:`~eth_defi.grvt.constants.GRVT_DEFAULT_REQUESTS_PER_SECOND`.
    :param pool_maxsize:
        Maximum number of connections to keep in the connection pool.
        Should be at least as large as ``max_workers`` when using parallel requests.
    :param rate_limit_db_path:
        Path to SQLite database for storing rate limit state.
        Using SQLite ensures thread-safe rate limiting across multiple threads.
    :param timeout:
        HTTP request timeout in seconds.
    :param authenticate:
        If True, authenticate with the GRVT API using credentials.
        Required for private endpoints like ``vault_investor_summary``.
    :return:
        Configured requests Session.
    :raises AssertionError:
        If ``authenticate=True`` and required credentials are not provided.
    """
    from eth_defi.grvt.constants import GRVT_API_URL, GRVT_DEFAULT_REQUESTS_PER_SECOND

    if api_url is None:
        api_url = GRVT_API_URL
    if requests_per_second is None:
        requests_per_second = GRVT_DEFAULT_REQUESTS_PER_SECOND

    # Ensure parent directory exists
    rate_limit_db_path.parent.mkdir(parents=True, exist_ok=True)

    session = Session()

    # Need to whitelist POST as retry method as GRVT endpoints use POST
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

    if authenticate:
        if api_key is None:
            api_key = os.environ.get("GRVT_API_KEY")
        if private_key is None:
            private_key = os.environ.get("GRVT_PRIVATE_KEY")
        if trading_account_id is None:
            trading_account_id = os.environ.get("GRVT_TRADING_ACCOUNT_ID")

        assert api_key, "GRVT_API_KEY must be set (pass directly or via environment variable)"
        assert private_key, "GRVT_PRIVATE_KEY must be set (pass directly or via environment variable)"
        assert trading_account_id, "GRVT_TRADING_ACCOUNT_ID must be set (pass directly or via environment variable)"

        _authenticate_session(session, api_url, api_key, private_key, trading_account_id, timeout)

    return session
