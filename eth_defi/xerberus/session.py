"""HTTP session management for Xerberus public REST API.

Provides session creation with retry logic, paced rate limiting under the
public 10 requests / 60 s quota, and dual auth headers (``x-api-key`` +
``x-user-email``).

Authentication requires both an API key and the email registered with that
key. Pass them explicitly via ``api_key`` / ``api_email``, or set
``XERBERUS_API_KEY`` and ``XERBERUS_API_EMAIL`` (alias ``XERBERUS_USER_EMAIL``).
Do not invent or probe candidate emails; only use operator-supplied values.

For API fetch helpers, see :py:mod:`eth_defi.xerberus.api`.
See ``README-xerberus.md`` for the full authentication contract.
"""

import logging
import os
import time
from pathlib import Path

from pyrate_limiter import SQLiteBucket
from requests import Session
from requests_ratelimiter import LimiterAdapter

from eth_defi.logging_retry import LoggingRetry
from eth_defi.xerberus.constants import (
    XERBERUS_API_URL,
    XERBERUS_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
    XERBERUS_DEFAULT_REQUESTS_PER_MINUTE,
    XERBERUS_RATE_LIMIT_SQLITE_DATABASE,
    XERBERUS_USER_AGENT,
    resolve_xerberus_api_email,
)

logger = logging.getLogger(__name__)

#: Default number of retries for API requests
DEFAULT_RETRIES: int = 5

#: Default backoff factor for retries (seconds)
DEFAULT_BACKOFF_FACTOR: float = 0.5


class XerberusSession(Session):
    """A :py:class:`requests.Session` subclass for Xerberus API.

    Carries the API URL, API key, and registered email. Sets default headers
    (``x-api-key``, ``x-user-email``) and enforces minimum spacing between
    authenticated requests so the client does not burst under the minute bucket.

    Both credentials are required. Prefer passing them explicitly::

        create_xerberus_session(api_key=key, api_email=email)

    When omitted, values are read from ``XERBERUS_API_KEY`` and
    ``XERBERUS_API_EMAIL`` (or ``XERBERUS_USER_EMAIL``). Agents must not
    invent email addresses; only use operator-supplied env vars or
    explicit arguments.

    Use :py:func:`create_xerberus_session` to create instances.
    """

    #: Xerberus API base URL.
    api_url: str

    #: Xerberus API key.
    api_key: str

    #: Registered email associated with the API key (sent as ``x-user-email``).
    api_email: str

    #: Minimum seconds between authenticated requests.
    min_request_interval: float

    def __init__(
        self,
        api_url: str = XERBERUS_API_URL,
        api_key: str | None = None,
        api_email: str | None = None,
        min_request_interval: float = XERBERUS_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
    ):
        """Create a session.

        :param api_url:
            Xerberus public API base URL.
        :param api_key:
            API key. If ``None``, reads ``XERBERUS_API_KEY``.
        :param api_email:
            Email registered with the key. If ``None``, reads
            ``XERBERUS_API_EMAIL`` / ``XERBERUS_USER_EMAIL``. Pass this
            explicitly when available; never invent a value.
        :param min_request_interval:
            Minimum seconds between authenticated requests.
        """
        super().__init__()
        self.api_url = api_url
        if api_key is None:
            api_key = os.environ.get("XERBERUS_API_KEY")
        if api_email is None:
            api_email = resolve_xerberus_api_email()
        assert api_key, "XERBERUS_API_KEY environment variable or api_key= parameter is required"
        assert api_email, "XERBERUS_API_EMAIL (or XERBERUS_USER_EMAIL) environment variable or api_email= parameter is required. Pass the email registered with the API key explicitly — do not guess candidate addresses."
        self.api_key = api_key
        self.api_email = api_email
        self.min_request_interval = min_request_interval
        self._last_request_monotonic: float | None = None
        self.headers.update(
            {
                "User-Agent": XERBERUS_USER_AGENT,
                "x-api-key": api_key,
                "x-user-email": api_email,
            }
        )

    def __repr__(self) -> str:
        # Do not log full email/key secrets; show only that email is configured.
        email_set = "yes" if self.api_email else "no"
        return f"<XerberusSession api_url={self.api_url!r} api_email_set={email_set}>"

    def request(self, method, url, *args, **kwargs):  # noqa: ANN001
        """Send an HTTP request with paced spacing for authenticated calls.

        Healthcheck paths under ``/healthcheck`` skip the min-interval sleep
        so liveness probes stay cheap and outside the paced budget.

        :param method:
            HTTP method.
        :param url:
            Request URL.
        :return:
            Response object from the parent session.
        """
        is_healthcheck = "healthcheck" in str(url)
        if not is_healthcheck and self.min_request_interval > 0:
            now = time.monotonic()
            if self._last_request_monotonic is not None:
                elapsed = now - self._last_request_monotonic
                if elapsed < self.min_request_interval:
                    time.sleep(self.min_request_interval - elapsed)
            self._last_request_monotonic = time.monotonic()
        return super().request(method, url, *args, **kwargs)


def create_xerberus_session(
    api_url: str = XERBERUS_API_URL,
    api_key: str | None = None,
    api_email: str | None = None,
    retries: int = DEFAULT_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    requests_per_minute: float = XERBERUS_DEFAULT_REQUESTS_PER_MINUTE,
    min_request_interval: float = XERBERUS_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
    pool_maxsize: int = 8,
    rate_limit_db_path: Path = XERBERUS_RATE_LIMIT_SQLITE_DATABASE,
) -> XerberusSession:
    """Create a :py:class:`XerberusSession` configured for Xerberus API.

    The session is configured with:

    - Dual auth headers (``x-api-key`` + ``x-user-email``)
    - Minute-bucket rate limiting under the public 10/min quota (default 8/min)
    - Minimum request spacing to avoid burst-then-idle behaviour
    - Retry logic for 429/5xx with ``Retry-After`` respect

    Prefer passing credentials explicitly when they are already available
    (e.g. after ``source .local-test.env`` loaded operator secrets)::

        import os

        from eth_defi.xerberus.session import create_xerberus_session

        session = create_xerberus_session(
            api_key=os.environ["XERBERUS_API_KEY"],
            api_email=os.environ["XERBERUS_API_EMAIL"],
        )

    Or rely on environment defaults (operator-set only; agents must never
    invent the email)::

        session = create_xerberus_session()

    :param api_url:
        Xerberus API base URL.
    :param api_key:
        API key. If ``None``, reads from ``XERBERUS_API_KEY``.
        Pass explicitly when known.
    :param api_email:
        Email registered with the API key (``x-user-email``). If ``None``,
        reads from ``XERBERUS_API_EMAIL`` or alias ``XERBERUS_USER_EMAIL``.
        Pass explicitly when known. **Do not guess** this value.
    :param retries:
        Maximum retry attempts for failed requests.
    :param backoff_factor:
        Exponential backoff factor.
    :param requests_per_minute:
        Hard minute-bucket cap (default 8, under the 10/min public limit).
    :param min_request_interval:
        Minimum seconds between authenticated requests.
    :param pool_maxsize:
        Connection pool size (serial clients can keep this small).
    :param rate_limit_db_path:
        SQLite path for rate-limit state.
    :return:
        Configured :py:class:`XerberusSession`.
    """
    rate_limit_db_path.parent.mkdir(parents=True, exist_ok=True)

    session = XerberusSession(
        api_url=api_url,
        api_key=api_key,
        api_email=api_email,
        min_request_interval=min_request_interval,
    )

    retry_policy = LoggingRetry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
        logger=logger,
    )

    adapter = LimiterAdapter(
        per_minute=requests_per_minute,
        max_retries=retry_policy,
        pool_connections=pool_maxsize,
        pool_maxsize=pool_maxsize,
        bucket_class=SQLiteBucket,
        bucket_kwargs={"path": str(rate_limit_db_path)},
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
