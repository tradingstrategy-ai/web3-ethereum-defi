"""Web3 middleware.

Most for dealing with JSON-RPC unreliability issues with retries.

- Taken from exception_retry_request.py from Web3.py

- Modified to support sleep and throttling

- Logs warnings to Python logging subsystem in the case there is need to retry
"""

from web3 import Web3
import time
from typing import Callable, Any, Collection, Type, Tuple, Optional, TypeAlias, Union
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


#: List of Web3 exceptions we know we should retry after some timeout
DEFAULT_RETRYABLE_EXCEPTIONS: Tuple[BaseException] = (
    ConnectionError,
    HTTPError,
    Timeout,
    TooManyRedirects,
)

#: List of HTTP status codes we know we might want to retry after a timeout
#:
#: Taken from https://stackoverflow.com/a/72302017/315168
DEFAULT_RETRYABLE_HTTP_STATUS_CODES = (
    429,
    500,
    502,
    503,
    504,
)

#: List of ValueError status codes we know we might want to retry after a timeout
#:
#: This is a self-managed list curated by pain.
#:
#: JSON-RPC error might be mapped to :py:class:`ValueError`
#: if nothing else is available.
#:
#: Example from Pokt Network:
#:
#: `ValueError: {'message': 'Internal JSON-RPC error.', 'code': -32603}`
#:
#: We assume this is a broken RPC node and Pokt will reroute the
#: the next retried request to some other node.
#:
#: See GoEthereum error codes https://github.com/ethereum/go-ethereum/blob/master/rpc/errors.go
#:
DEFAULT_RETRYABLE_RPC_ERROR_CODES = (-32603,)


def is_retryable_http_exception(
    exc: Exception,
    retryable_exceptions: Tuple[BaseException] = DEFAULT_RETRYABLE_EXCEPTIONS,
    retryable_status_codes: Collection[int] = DEFAULT_RETRYABLE_HTTP_STATUS_CODES,
    retryable_rpc_error_codes: Collection[int] = DEFAULT_RETRYABLE_RPC_ERROR_CODES,
):
    """Helper to check retryable errors from JSON-RPC calls.

    Retryable reasons are connection timeouts, API throttling and such.

    We support various kind of exceptions and HTTP status codes
    we know we can try.

    :param exc:
        Exception raised by :py:mod:`requests`
        or Web3 machinery.

    :param retryable_exceptions:
        Exception raised by :py:mod:`requests`
        or Web3 machinery.

    :param retryable_status_codes:
        HTTP status codes we can retry. E.g. 429 Too Many requests.
    """

    if isinstance(exc, ValueError):
        # raise ValueError(response["error"])
        # ValueError: {'message': 'Internal JSON-RPC error.', 'code': -32603}
        if len(exc.args) > 0:
            arg = exc.args[0]
            if type(arg) == dict:
                code = arg.get("code")
                if code is None or type(code) != int:
                    raise RuntimeError(f"Bad ValueError: {arg} - {exc}")
                return code in retryable_rpc_error_codes

    if isinstance(exc, HTTPError):
        return exc.response.status_code in retryable_status_codes

    if isinstance(exc, retryable_exceptions):
        return True

    return False


def exception_retry_middleware(
    make_request: Callable[[RPCEndpoint, Any], RPCResponse],
    web3: "Web3",
    retryable_exceptions: Tuple[BaseException],
    retryable_status_codes: Collection[int],
    retryable_rpc_error_codes: Collection[int],
    retries: int = 10,
    sleep: float = 5.0,
    backoff: float = 1.6,
) -> Callable[[RPCEndpoint, Any], RPCResponse]:
    """
    Creates middleware that retries failed HTTP requests. Is a default
    middleware for HTTPProvider.

    See :py:func:`http_retry_request_with_sleep_middleware` for usage.

    """

    def middleware(method: RPCEndpoint, params: Any) -> Optional[RPCResponse]:
        nonlocal sleep

        current_sleep = sleep

        # Check if the RPC method is whitelisted for multiple retries
        if check_if_retry_on_failure(method):
            # Try to recover from any JSON-RPC node error, sleep and try again
            for i in range(retries):
                try:
                    return make_request(method, params)
                # https://github.com/python/mypy/issues/5349
                except Exception as e:  # type: ignore
                    if is_retryable_http_exception(
                        e,
                        retryable_rpc_error_codes=retryable_rpc_error_codes,
                        retryable_status_codes=retryable_status_codes,
                        retryable_exceptions=retryable_exceptions,
                    ):
                        if i < retries - 1:
                            logger.warning("Encountered JSON-RPC retryable error %s when calling method %s, retrying in %f seconds, retry #%d", e, method, current_sleep, i)
                            time.sleep(current_sleep)
                            current_sleep *= backoff
                            continue
                        else:
                            raise  # Out of retries
                    raise  # Not retryable exception
            return None
        else:
            try:
                return make_request(method, params)
            except Exception as e:
                # Be verbose so that we know our whitelist is missing methods
                raise RuntimeError(f"JSON-RPC failed for non-whitelisted method {method}: {e}") from e

    return middleware


def http_retry_request_with_sleep_middleware(
    make_request: Callable[[RPCEndpoint, Any], Any],
    web3: "Web3",
) -> Callable[[RPCEndpoint, Any], Any]:
    """A HTTP retry middleware with sleep and backoff.

    If you want to customise timeouts, supported exceptions and such
    you can directly create your own middleware
    using :py:func:`exception_retry_middleware`.

    Usage:

    .. code-block::

        web3.middleware_onion.clear()
        web3.middleware_onion.inject(http_retry_request_with_sleep_middleware, layer=0)

    :param make_request:
        Part of middleware call signature

    :param web3:
        Part of middleware call signature

    :return:
        Web3.py middleware
    """
    return exception_retry_middleware(
        make_request,
        web3,
        retryable_exceptions=DEFAULT_RETRYABLE_EXCEPTIONS,
        retryable_status_codes=DEFAULT_RETRYABLE_HTTP_STATUS_CODES,
        retryable_rpc_error_codes=DEFAULT_RETRYABLE_RPC_ERROR_CODES,
    )
