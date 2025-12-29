"""HTTP session management for Hyperliquid API.

This module provides session creation with retry logic for Hyperliquid API requests.
"""

import logging

from requests import Session
from requests.adapters import HTTPAdapter

from eth_defi.velvet.logging_retry import LoggingRetry

logger = logging.getLogger(__name__)

#: Default number of retries for API requests
DEFAULT_RETRIES = 5

#: Default backoff factor for retries (seconds)
DEFAULT_BACKOFF_FACTOR = 0.5


def create_hyperliquid_session(
    retries: int = DEFAULT_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
) -> Session:
    """Create a requests Session configured for Hyperliquid API.

    The session is configured with retry logic for handling API throttling
    and transient errors using exponential backoff.

    Example::

        from eth_defi.hyperliquid.session import create_hyperliquid_session

        session = create_hyperliquid_session()
        response = session.get("https://api.hyperliquid.xyz/info")

    :param retries:
        Maximum number of retry attempts for failed requests
    :param backoff_factor:
        Backoff factor for exponential retry delays
    :return:
        Configured requests Session with retry logic
    """
    session = Session()
    retry_policy = LoggingRetry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        logger=logger,
    )
    session.mount("http://", HTTPAdapter(max_retries=retry_policy))
    session.mount("https://", HTTPAdapter(max_retries=retry_policy))
    return session
