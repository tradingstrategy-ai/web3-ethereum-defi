"""HTTP session management for Hyperliquid API.

This module provides session creation with retry logic and rate limiting
for Hyperliquid API requests.

Rate limiting is thread-safe using SQLite backend, so the session can be
shared across multiple threads when using ``joblib.Parallel`` or similar.

The :py:class:`HyperliquidSession` carries the API URL so that downstream
functions do not need a separate ``server_url`` argument.

Proxy support
-------------

When a :py:class:`~eth_defi.event_reader.webshare.ProxyRotator` is configured
(via :py:meth:`HyperliquidSession.configure_rotator` or the ``rotator``
parameter to :py:func:`create_hyperliquid_session`), the session automatically
routes API requests through proxies. :py:meth:`~HyperliquidSession.post_info`
rotates to the next proxy on connection failures or rate-limit responses, and
records failures via the rotator's
:py:class:`~eth_defi.event_reader.webshare.ProxyStateManager` so that
persistently bad proxies are skipped in future runs.

Rate limiting with proxies
--------------------------

Hyperliquid rate limits are **per IP**. When proxies are used, each worker
thread gets its own session clone (via :py:meth:`HyperliquidSession.clone_for_worker`)
with an **independent rate limiter** so that each proxy IP can use its full
rate allowance independently.
"""

import logging
import os
import tempfile
from pathlib import Path

import requests as requests_lib
from pyrate_limiter import SQLiteBucket
from requests import Session
from requests_ratelimiter import LimiterAdapter

from eth_defi.event_reader.webshare import ProxyRotator
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

#: Maximum proxy rotation attempts in a single post_info() call
#: before falling back to direct connection
MAX_PROXY_ROTATIONS = 3


def _create_adapter(
    requests_per_second: float,
    retries: int,
    backoff_factor: float,
    pool_maxsize: int,
    rate_limit_db_path: Path,
    retry_log_level: int = logging.WARNING,
) -> LimiterAdapter:
    """Create a :class:`LimiterAdapter` with rate limiting and retry logic.

    :param requests_per_second:
        Maximum requests per second.
    :param retries:
        Maximum retry attempts.
    :param backoff_factor:
        Backoff factor for exponential retry delays.
    :param pool_maxsize:
        Maximum connection pool size.
    :param rate_limit_db_path:
        Path to SQLite database for rate limiting state.
    :return:
        Configured adapter.
    """
    rate_limit_db_path.parent.mkdir(parents=True, exist_ok=True)

    retry_policy = LoggingRetry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
        logger=logger,
        log_level=retry_log_level,
        allowed_methods=LoggingRetry.DEFAULT_ALLOWED_METHODS | frozenset(["POST"]),
    )

    return LimiterAdapter(
        per_second=requests_per_second,
        max_retries=retry_policy,
        pool_connections=pool_maxsize,
        pool_maxsize=pool_maxsize,
        bucket_class=SQLiteBucket,
        bucket_kwargs={"path": str(rate_limit_db_path)},
    )


class HyperliquidSession(Session):
    """A :py:class:`requests.Session` subclass that carries the Hyperliquid API URL.

    All Hyperliquid API functions accept a ``HyperliquidSession`` and read
    :py:attr:`api_url` from it, removing the need for a separate
    ``server_url`` argument on every call.

    The session optionally manages a
    :py:class:`~eth_defi.event_reader.webshare.ProxyRotator` for automatic
    proxy rotation on failure. Use :py:meth:`configure_rotator` or pass
    ``rotator`` to :py:func:`create_hyperliquid_session`.

    Use :py:func:`create_hyperliquid_session` to create instances.
    """

    #: Hyperliquid API base URL (e.g. ``https://api.hyperliquid.xyz``).
    api_url: str

    def __init__(self, api_url: str = HYPERLIQUID_API_URL):
        super().__init__()
        self.api_url = api_url
        self._rotator: ProxyRotator | None = None
        # Store adapter config so clone_for_worker can create independent rate limiters
        self._adapter_config: dict | None = None
        #: Maximum proxy rotations per post_info() call before giving up
        self.max_proxy_rotations: int = MAX_PROXY_ROTATIONS
        #: Total HTTP requests made via post_info()
        self._request_count: int = 0
        #: Total proxy rotations triggered by failures
        self._rotation_count: int = 0

    def __repr__(self) -> str:
        proxy_info = f", proxy={self.active_proxy_url[:30]}..." if self.active_proxy_url else ""
        return f"<HyperliquidSession api_url={self.api_url!r}{proxy_info}>"

    # ──────────────────────────────────────────────
    # Proxy configuration
    # ──────────────────────────────────────────────

    def configure_rotator(self, rotator: ProxyRotator) -> None:
        """Configure proxy rotation using a :class:`ProxyRotator`.

        The rotator provides thread-safe proxy selection and persistent
        failure tracking via its optional
        :py:class:`~eth_defi.event_reader.webshare.ProxyStateManager`.

        :param rotator:
            A :class:`ProxyRotator` (typically from
            :py:func:`~eth_defi.event_reader.webshare.load_proxy_rotator`).
        """
        self._rotator = rotator

    @property
    def rotator(self) -> ProxyRotator | None:
        """The configured :class:`ProxyRotator`, or None if proxies are disabled."""
        return self._rotator

    @property
    def proxy_urls(self) -> list[str]:
        """All configured proxy URLs."""
        if self._rotator is None:
            return []
        return [p.to_proxy_url() for p in self._rotator.proxies]

    @property
    def proxy_count(self) -> int:
        """Number of configured proxies."""
        if self._rotator is None:
            return 0
        return len(self._rotator)

    @property
    def proxy_enabled(self) -> bool:
        """Whether proxy support is enabled (at least one proxy configured)."""
        return self._rotator is not None and len(self._rotator) > 0

    @property
    def active_proxy_url(self) -> str | None:
        """Currently active proxy URL, or None if proxies are disabled."""
        if not self.proxy_enabled:
            return None
        return self._rotator.current().to_proxy_url()

    @property
    def proxy_failures(self) -> int:
        """Rotation generation count (increases with each rotation)."""
        if self._rotator is None:
            return 0
        return self._rotator.generation

    @property
    def request_count(self) -> int:
        """Total HTTP requests made via :meth:`post_info`."""
        return self._request_count

    @property
    def rotation_count(self) -> int:
        """Total proxy rotations triggered by failures in :meth:`post_info`."""
        return self._rotation_count

    def _build_proxy_dict(self) -> dict[str, str] | None:
        """Build a ``requests``-compatible proxies dict for the active proxy."""
        url = self.active_proxy_url
        if url is None:
            return None
        return {"http": url, "https": url}

    def _rotate_proxy(self, reason: str = "") -> str | None:
        """Rotate to the next proxy after a failure.

        Records the failure in the :class:`ProxyStateManager` (if one is
        attached to the rotator) so that persistently bad proxies are
        skipped in future runs.

        :param reason:
            Short description of the failure (logged).
        :return:
            The new proxy URL, or None if no rotator is configured.
        """
        if self._rotator is None:
            return None
        self._rotator.rotate(failure_reason=reason)
        return self.active_proxy_url

    # ──────────────────────────────────────────────
    # API helpers
    # ──────────────────────────────────────────────

    def post_info(self, payload: dict, timeout: float = 30.0) -> requests_lib.Response:
        """POST to the Hyperliquid ``/info`` endpoint with graceful proxy rotation.

        Uses the session's configured proxy (if any). Rotation policy:

        - **Rate-limit / backend overload responses** (HTTP 429, 500,
          502, 503, 504): rotate to the next proxy but do NOT record
          the current proxy as dead — it is only throttled, and will
          recover within minutes. This avoids shrinking the working
          pool across runs via the
          :py:class:`~eth_defi.event_reader.webshare.ProxyStateManager`
          14-day grace period.
        - **Connection errors** (``requests.ConnectionError``,
          ``requests.Timeout``, ``OSError``): rotate AND record the
          current proxy as dead via the state manager, because the
          proxy itself is unreachable.

        After :data:`MAX_PROXY_ROTATIONS` consecutive rotations in a
        single call, returns whatever response comes back (or re-raises
        the last exception) rather than continuing to rotate.

        :param payload:
            JSON request body for the ``/info`` endpoint.
        :param timeout:
            HTTP request timeout in seconds.
        :return:
            The :py:class:`requests.Response` object.
        :raises requests.ConnectionError:
            If the request fails and no proxy rotation is available.
        :raises requests.Timeout:
            If the request times out and no proxy rotation is available.
        """
        # HTTP statuses that signal "slow down / upstream is overloaded",
        # but the proxy itself is fine. Rotate without marking dead.
        throttle_statuses = {429, 500, 502, 503, 504}

        rotations = 0
        while True:
            req_proxies = self._build_proxy_dict()
            self._request_count += 1
            try:
                response = self.post(
                    f"{self.api_url}/info",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                    proxies=req_proxies,
                )
                if response.status_code in throttle_statuses and self.proxy_enabled and rotations < self.max_proxy_rotations:
                    rotations += 1
                    self._rotation_count += 1
                    # Rotate without failure_reason — the ProxyStateManager
                    # must NOT mark this proxy as dead. It is only throttled
                    # and will recover within minutes.
                    self._rotator.rotate(failure_reason=None)
                    logger.log(
                        self._rotator.log_level,
                        "Rotated on HTTP %d (throttled, proxy not marked dead)",
                        response.status_code,
                    )
                    continue
                return response
            except (requests_lib.ConnectionError, requests_lib.Timeout, OSError) as exc:
                if self.proxy_enabled and rotations < self.max_proxy_rotations:
                    rotations += 1
                    self._rotation_count += 1
                    # Genuine connection failure — record via state manager
                    # so the proxy is blocked for the grace period.
                    self._rotate_proxy(reason=str(exc)[:80])
                    continue
                raise

    def clone_for_worker(self, proxy_start_index: int = 0) -> "HyperliquidSession":
        """Create a lightweight clone for a worker thread.

        The clone shares the same API URL. When proxies are configured, the
        clone gets its own
        :py:class:`~eth_defi.event_reader.webshare.ProxyRotator` starting
        at ``proxy_start_index`` so that each worker hits a different proxy.
        The underlying
        :py:class:`~eth_defi.event_reader.webshare.ProxyStateManager` is
        shared, so failures recorded by any worker are persisted globally.

        Each clone gets its own **independent rate limiter** because
        Hyperliquid rate limits are per IP. When workers use different
        proxies, each proxy IP gets its full rate allowance.

        :param proxy_start_index:
            Starting proxy index for this worker (typically the worker
            ordinal: 0, 1, 2, ...).
        :return:
            A new :py:class:`HyperliquidSession` with its own proxy
            rotation state and rate limiter.
        """
        clone = HyperliquidSession(api_url=self.api_url)

        # Each worker gets its own rate limiter since rate limits are per IP.
        # When using proxies, each proxy is a different IP so they need
        # independent rate limiting.
        if self._adapter_config is not None:
            # Create a fresh adapter with a unique SQLite database for this worker
            fd, tmp_path = tempfile.mkstemp(
                suffix=".sqlite",
                prefix=f"hl-rate-limit-worker-{proxy_start_index}-",
                dir=self._adapter_config["rate_limit_db_path"].parent,
            )
            os.close(fd)
            worker_db = Path(tmp_path)
            adapter = _create_adapter(
                requests_per_second=self._adapter_config["requests_per_second"],
                retries=self._adapter_config["retries"],
                backoff_factor=self._adapter_config["backoff_factor"],
                pool_maxsize=self._adapter_config["pool_maxsize"],
                rate_limit_db_path=worker_db,
                retry_log_level=self._adapter_config.get("retry_log_level", logging.WARNING),
            )
            clone.mount("http://", adapter)
            clone.mount("https://", adapter)
            clone._adapter_config = self._adapter_config
        else:
            # No adapter config stored — share the parent's adapters (fallback)
            clone.adapters = self.adapters.copy()

        # Propagate proxy rotation budget
        clone.max_proxy_rotations = self.max_proxy_rotations

        # Each worker gets its own rotator clone starting at a different proxy,
        # but sharing the same ProxyStateManager for persistent failure tracking
        if self._rotator is not None:
            clone._rotator = self._rotator.clone_for_worker(start_index=proxy_start_index)

        return clone


def create_hyperliquid_session(
    api_url: str = HYPERLIQUID_API_URL,
    retries: int = DEFAULT_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
    pool_maxsize: int = 32,
    rate_limit_db_path: Path = HYPERLIQUID_RATE_LIMIT_SQLITE_DATABASE,
    rotator: ProxyRotator | None = None,
    verbose_throttling: bool | None = None,
    proxy_failure_log_level: int | None = None,
) -> HyperliquidSession:
    """Create a :py:class:`HyperliquidSession` configured for Hyperliquid API.

    The session is configured with:

    - The API URL stored in :py:attr:`HyperliquidSession.api_url`
    - Rate limiting to respect Hyperliquid API throttling (thread-safe via SQLite)
    - Retry logic for handling transient errors using exponential backoff
    - Optional proxy support with automatic rotation on failure

    The rate limiter uses SQLite backend for thread-safe coordination across
    multiple threads (e.g., when using ``joblib.Parallel`` with threading backend).

    When proxies are configured and workers are cloned via
    :py:meth:`HyperliquidSession.clone_for_worker`, each worker gets its own
    rate limiter because Hyperliquid rate limits are per IP.

    - `See rate limits here <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits>`__.

    Example::

        from eth_defi.hyperliquid.session import create_hyperliquid_session, HYPERLIQUID_TESTNET_API_URL

        # Mainnet (default)
        session = create_hyperliquid_session()

        # Testnet
        session = create_hyperliquid_session(api_url=HYPERLIQUID_TESTNET_API_URL)

        # With Webshare rotator (persistent failure tracking via ProxyStateManager)
        from eth_defi.event_reader.webshare import load_proxy_rotator

        rotator = load_proxy_rotator()
        session = create_hyperliquid_session(rotator=rotator)

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
    :param rotator:
        Optional :class:`ProxyRotator` (typically from
        :py:func:`~eth_defi.event_reader.webshare.load_proxy_rotator`).
        Provides proxy rotation with persistent failure tracking via
        :class:`~eth_defi.event_reader.webshare.ProxyStateManager`.
    :param verbose_throttling:
        Control rate-limit / throttling log messages.

        - ``None`` (default): off when proxies are used, on otherwise.
          With proxies, every worker thread hits the rate limiter independently
          and the resulting log spam is not useful.
        - ``True``: always log throttling messages.
        - ``False``: always suppress throttling messages.
    :param proxy_failure_log_level:
        Log level for proxy failure/rotation messages (from
        :class:`~eth_defi.event_reader.webshare.ProxyRotator`,
        :class:`~eth_defi.event_reader.webshare.ProxyStateManager`,
        and :class:`~eth_defi.velvet.logging_retry.LoggingRetry`).

        - ``None`` (default): ``logging.DEBUG`` when proxies are used,
          ``logging.WARNING`` otherwise.
        - Pass e.g. ``logging.DEBUG`` to suppress or ``logging.WARNING``
          to always show.
    :return:
        Configured :py:class:`HyperliquidSession` with rate limiting and retry logic
    """
    session = HyperliquidSession(api_url=api_url)

    # When proxies are enabled, disable urllib3-level retries so that
    # connection failures go straight to post_info() for proxy rotation
    # instead of retrying 5 times through the same broken proxy.
    effective_retries = 0 if rotator is not None else retries

    # Resolve proxy_failure_log_level: None → DEBUG with proxies, WARNING without
    if proxy_failure_log_level is None:
        proxy_failure_log_level = logging.DEBUG if rotator is not None else logging.WARNING

    adapter = _create_adapter(
        requests_per_second=requests_per_second,
        retries=effective_retries,
        backoff_factor=backoff_factor,
        pool_maxsize=pool_maxsize,
        rate_limit_db_path=rate_limit_db_path,
        retry_log_level=proxy_failure_log_level,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    # Store config so clone_for_worker can create independent rate limiters
    session._adapter_config = {
        "requests_per_second": requests_per_second,
        "retries": effective_retries,
        "backoff_factor": backoff_factor,
        "pool_maxsize": pool_maxsize,
        "rate_limit_db_path": rate_limit_db_path,
        "retry_log_level": proxy_failure_log_level,
    }

    if rotator is not None:
        session.configure_rotator(rotator)
        # Compensate for disabled adapter retries by allowing more proxy rotations
        session.max_proxy_rotations = min(len(rotator), 10)
        # Apply log level to rotator and its state manager
        rotator.log_level = proxy_failure_log_level
        if rotator.state_manager is not None:
            rotator.state_manager.log_level = proxy_failure_log_level

    # Resolve verbose_throttling: None → off with proxies, on without
    if verbose_throttling is None:
        verbose_throttling = rotator is None

    if not verbose_throttling:
        # Silence rate-limiter delay messages and bucket-fill info logs
        logging.getLogger("pyrate_limiter.limit_context_decorator").setLevel(logging.WARNING)
        logging.getLogger("requests_ratelimiter.requests_ratelimiter").setLevel(logging.WARNING)

    return session
