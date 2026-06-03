"""HTTP session management and fetch helpers for Core3 API.

This module provides session creation with retry logic and rate limiting
for Core3 Projects Data API requests, plus helper functions to fetch
and unwrap individual endpoints.

Rate limiting is thread-safe using SQLite backend, so the session can be
shared across multiple threads when using ``joblib.Parallel`` or similar.

The :py:class:`Core3Session` carries the API URL and API key so that
downstream functions do not need separate arguments on every call.

See `Core3 API documentation <https://docs.core3.io/projects-data-api>`__
for endpoint details.
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


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


def fetch_project_list(session: Core3Session, timeout: float = 30.0) -> list[dict]:
    """Fetch the full project list from ``/v1/list``.

    Returns the unwrapped list (the API wraps it as ``{"list": [...]}``)
    containing slug, name, coingecko_id, and PoL score per project.

    :param session:
        Core3 API session.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of project dicts.
    """
    url = f"{session.api_url}/v1/list"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["list"]


def fetch_project_detail(session: Core3Session, slug: str, timeout: float = 30.0) -> dict:
    """Fetch full project detail from ``/v1/{slug}``.

    Returns the top-level project object including description, rank,
    PoL score, market cap, links, top_risks, and seals.

    :param session:
        Core3 API session.
    :param slug:
        Project identifier (e.g. ``'ethereum'``, ``'aave'``).
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Project detail dict (raw JSON response).
    """
    url = f"{session.api_url}/v1/{slug}"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_pol_history(session: Core3Session, slug: str, timeout: float = 30.0) -> list[dict]:
    """Fetch all-time PoL history chart from ``/v1/{slug}/pol/history/chart``.

    Returns a list of ``{score, timestamp}`` points, unwrapped from
    ``{"points": [...]}``. Used for initial backfill.

    :param session:
        Core3 API session.
    :param slug:
        Project slug.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of point dicts with ``score`` and ``timestamp`` keys.
    """
    url = f"{session.api_url}/v1/{slug}/pol/history/chart"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["points"]


def fetch_pol_history_incremental(
    session: Core3Session,
    slug: str,
    from_ts: int,
    to_ts: int,
    timeout: float = 30.0,
) -> list[dict]:
    """Fetch ranged PoL history from ``/v1/{slug}/pol/history``.

    Uses the ``from`` and ``to`` query parameters (unix timestamps in
    seconds) to fetch only the requested range. Used for incremental
    updates after the initial backfill.

    :param session:
        Core3 API session.
    :param slug:
        Project slug.
    :param from_ts:
        Start unix timestamp (inclusive).
    :param to_ts:
        End unix timestamp (inclusive).
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of point dicts with ``score`` and ``timestamp`` keys.
    """
    url = f"{session.api_url}/v1/{slug}/pol/history"
    resp = session.get(url, params={"from": from_ts, "to": to_ts}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["points"]


def fetch_pol_category_history(session: Core3Session, slug: str, timeout: float = 30.0) -> list[dict]:
    """Fetch all-time PoL category breakdown history from ``/v1/{slug}/pol/by_category/history/chart``.

    Returns a list of points, each containing a timestamp and per-category
    PoL scores (security, financial, operational, reputational, regulatory).

    :param session:
        Core3 API session.
    :param slug:
        Project slug.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of point dicts with category score breakdowns.
    """
    url = f"{session.api_url}/v1/{slug}/pol/by_category/history/chart"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["points"]


def fetch_pol_category_history_incremental(
    session: Core3Session,
    slug: str,
    from_ts: int,
    to_ts: int,
    timeout: float = 30.0,
) -> list[dict]:
    """Fetch ranged PoL category breakdown history from ``/v1/{slug}/pol/by_category/history``.

    :param session:
        Core3 API session.
    :param slug:
        Project slug.
    :param from_ts:
        Start unix timestamp (inclusive).
    :param to_ts:
        End unix timestamp (inclusive).
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of point dicts with category score breakdowns.
    """
    url = f"{session.api_url}/v1/{slug}/pol/by_category/history"
    resp = session.get(url, params={"from": from_ts, "to": to_ts}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["points"]


def fetch_index_pol_history(session: Core3Session, timeout: float = 30.0) -> list[dict]:
    """Fetch all-time index-level aggregate PoL history from ``/v1/pol/history/chart``.

    :param session:
        Core3 API session.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of point dicts with ``score`` and ``timestamp`` keys.
    """
    url = f"{session.api_url}/v1/pol/history/chart"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["points"]


def fetch_index_pol_history_incremental(
    session: Core3Session,
    from_ts: int,
    to_ts: int,
    timeout: float = 30.0,
) -> list[dict]:
    """Fetch ranged index-level aggregate PoL history from ``/v1/pol/history``.

    :param session:
        Core3 API session.
    :param from_ts:
        Start unix timestamp (inclusive).
    :param to_ts:
        End unix timestamp (inclusive).
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of point dicts with ``score`` and ``timestamp`` keys.
    """
    url = f"{session.api_url}/v1/pol/history"
    resp = session.get(url, params={"from": from_ts, "to": to_ts}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["points"]


def fetch_section_detail(session: Core3Session, slug: str, section: str, timeout: float = 30.0) -> dict:
    """Fetch a project section endpoint (security, financial, etc.).

    :param session:
        Core3 API session.
    :param slug:
        Project slug.
    :param section:
        Section name: ``'security'``, ``'financial'``, ``'operational'``,
        ``'reputational'``, or ``'regulatory'``.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Section detail dict (raw JSON response).
    """
    url = f"{session.api_url}/v1/{slug}/{section}"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()
