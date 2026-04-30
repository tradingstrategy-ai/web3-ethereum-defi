"""HTTP session management for Hibachi API.

This module provides session creation with retry logic for
Hibachi API requests.

The :py:class:`HibachiSession` carries the API URL so that downstream
functions do not need a separate ``api_url`` argument.

No rate limiting is applied — the ``data-api.hibachi.xyz`` endpoint
has not shown any rate limiting behaviour.
"""

import logging

from requests import Session

from eth_defi.hibachi.constants import HIBACHI_DATA_API_URL
from eth_defi.velvet.logging_retry import LoggingRetry

logger = logging.getLogger(__name__)

#: Default number of retries for API requests
DEFAULT_RETRIES: int = 5

#: Default backoff factor for retries (seconds)
DEFAULT_BACKOFF_FACTOR: float = 0.5


class HibachiSession(Session):
    """A :py:class:`requests.Session` subclass that carries the Hibachi API URL.

    All Hibachi API functions accept a ``HibachiSession`` and read
    :py:attr:`api_url` from it, removing the need for a separate
    ``api_url`` argument on every call.

    Use :py:func:`create_hibachi_session` to create instances.
    """

    #: Hibachi data API base URL (e.g. ``https://data-api.hibachi.xyz``).
    api_url: str

    def __init__(self, api_url: str = HIBACHI_DATA_API_URL):
        super().__init__()
        self.api_url = api_url

    def __repr__(self) -> str:
        return f"<HibachiSession api_url={self.api_url!r}>"


def create_hibachi_session(
    api_url: str = HIBACHI_DATA_API_URL,
    retries: int = DEFAULT_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
) -> HibachiSession:
    """Create a :py:class:`HibachiSession` configured for Hibachi API.

    The session is configured with retry logic for handling transient
    errors using exponential backoff. No rate limiting is applied —
    the Hibachi data API has not shown rate limiting behaviour.

    Example::

        from eth_defi.hibachi.session import create_hibachi_session

        session = create_hibachi_session()

    :param api_url:
        Hibachi data API base URL. Defaults to
        :py:data:`~eth_defi.hibachi.constants.HIBACHI_DATA_API_URL`.
    :param retries:
        Maximum number of retry attempts for failed requests.
    :param backoff_factor:
        Backoff factor for exponential retry delays.
    :return:
        Configured :py:class:`HibachiSession` with retry logic.
    """
    session = HibachiSession(api_url=api_url)

    retry_policy = LoggingRetry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
        logger=logger,
    )

    from requests.adapters import HTTPAdapter

    adapter = HTTPAdapter(
        max_retries=retry_policy,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
