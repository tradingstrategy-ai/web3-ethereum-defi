"""Web3 middleware.

Most for dealing with JSON-RPC unreliability issues with retries.

- Taken from exception_retry_request.py from Web3.py

- Modified to support sleep and throttling

- Logs warnings to Python logging subsystem in the case there is need to retry
"""


import time
from typing import Callable, Any, Collection, Type
import logging

from requests.exceptions import (
    ConnectionError,
    HTTPError,
    Timeout,
    TooManyRedirects,
)

from web3.middleware.exception_retry_request import check_if_retry_on_failure
from web3.types import RPCEndpoint, RPCResponse


logger = logging.getLogger(__name__)


def exception_retry_middleware(
    make_request: Callable[[RPCEndpoint, Any], RPCResponse],
    web3: "Web3",
    errors: Collection[Type[BaseException]],
    retries: int = 5,
    sleep: int = 5,
    backoff: float = 1.2,
) -> Callable[[RPCEndpoint, Any], RPCResponse]:
    """
    Creates middleware that retries failed HTTP requests. Is a default
    middleware for HTTPProvider.

    See :py:func:`http_retry_request_with_sleep_middleware` for usage.

    """
    def middleware(method: RPCEndpoint, params: Any) -> RPCResponse:
        nonlocal sleep

        # Check if the method is whitelisted
        if check_if_retry_on_failure(method):
            for i in range(retries):
                try:
                    return make_request(method, params)
                # https://github.com/python/mypy/issues/5349
                except errors as e:  # type: ignore
                    if i < retries - 1:
                        logger.warning("Encountered JSON-RPC retryable error %s when calling method %s, retrying in %f seconds", e, method, sleep)
                        time.sleep(sleep)
                        sleep *= backoff
                        continue
                    else:
                        raise
            return None
        else:
            try:
                return make_request(method, params)
            except Exception as e:
                # Be verbose so that we know our whitelist is missing methods
                raise RuntimeError(f"JSON-RPC failed for non-whitelisted method {method}: {e}") from e
    return middleware


def http_retry_request_with_sleep_middleware(
    make_request: Callable[[RPCEndpoint, Any], Any], web3: "Web3"
) -> Callable[[RPCEndpoint, Any], Any]:
    """A HTTP retry middleware with sleep and backoff.

    Usage:

    .. code-block::

        web3.middleware_onion.clear()
        web3.middleware_onion.inject(http_retry_request_with_sleep_middleware, layer=0)

    TODO: Make sleep and backoff parameters customizable
    """
    return exception_retry_middleware(
        make_request,
        web3,
        (ConnectionError, HTTPError, Timeout, TooManyRedirects)
    )