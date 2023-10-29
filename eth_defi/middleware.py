"""Web3 middleware.

Most for dealing with JSON-RPC unreliability issues with retries.

- Taken from exception_retry_request.py from Web3.py

- Modified to support sleep and throttling

- Logs warnings to Python logging subsystem in the case there is need to retry

- See also :py:mod:`eth_defi.provider.broken_provider`.
"""

import logging
import time
from typing import (
    Any,
    Callable,
    Collection,
    Counter,
    Optional,
    Tuple,
    Type,
    TypeAlias,
    Union,
)

from eth_utils.toolz import assoc
from requests.exceptions import (
    ChunkedEncodingError,
    ConnectionError,
    HTTPError,
    Timeout,
    TooManyRedirects,
)
from web3 import Web3
from web3._utils.transactions import get_buffered_gas_estimate
from web3.exceptions import BlockNotFound, ContractLogicError
from web3.middleware.exception_retry_request import check_if_retry_on_failure
from web3.types import Middleware, RPCEndpoint, RPCResponse

logger = logging.getLogger(__name__)


#: List of Web3 exceptions we know we should retry after some timeout
#:
#: For ``BlockNotFound`` see also :py:mod:`eth_defi.rpc.broken_provider`.
#:
DEFAULT_RETRYABLE_EXCEPTIONS: Tuple[BaseException] = (
    ConnectionError,
    HTTPError,
    Timeout,
    TooManyRedirects,
    # This happens when you ask web3.eth.block_number from Ankr,
    # but if you use it as `block_identifier` in the following call
    # it gives BlockNotFound. This is a problem with Ankr itself,
    # but we'll add it here just to work around this crappy provider
    # by default.
    BlockNotFound,
    # Spit out by LlamaNodes.
    #
    # Their server give invalid HTTP reply.
    #
    # requests.exceptions.ChunkedEncodingError: ("Connection broken: InvalidChunkLength(got length b'', 0 bytes read)", InvalidChunkLength(got length b'', 0 bytes read))
    #
    ChunkedEncodingError,
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
DEFAULT_RETRYABLE_RPC_ERROR_CODES = (
    # The node provider has corrupted database or something GoEthereum
    # cannot handle gracefully.
    # ValueError: {'message': 'Internal JSON-RPC error.', 'code': -32603}
    -32603,

    # ValueError: {'code': -32000, 'message': 'nonce too low'}.
    # Might happen when we are broadcasting multiple transactions through multiple RPC providers
    # using eth_sendRawTransaction
    # One provide has not yet seeing a transaction broadcast through the other provider.
    # CRAP! -32000 is also Execution reverted on Alchemy.
    # -32000,

    # ValueError: {'code': -32003, 'message': 'nonce too low'}.
    # Anvil variant for nonce too low, same as above
    -32003,
    # Some error we are getting from LlamaNodes eth_getLogs RPC that we do not know what it is all about
    # {'code': -32043, 'message': 'Requested data is not available'}
    -32043,
    # eth_getLogs size limit exceeded for a provider
    # eth_getLogs disabled on some providers
    # https://github.com/bnb-chain/bsc/issues/1215
    # {'code': -32005, 'message': 'limit exceeded'}
    -32005,
    # Some JSON-RPC provider is buying nodes from allondes.com have have screwed it up
    # ValueError: {'code': -32701, 'message': 'Please specify address in your request or, to remove restrictions, order a dedicated full node here: https://www.allnodes.com/bnb/host'}
    -32701,
)


#: Because Ethreum JSON-RPC API is horribly broken,
#: we also need to check for error messages besides error codes.
#:
#: See :py:data:`DEFAULT_RETRYABLE_RPC_ERROR_CODES`.
#:
DEFAULT_RETRYABLE_RPC_ERROR_MESSAGES = {
    "nonce too low"
}

#: Ethereum JSON-RPC calls where the value never changes
#:
STATIC_CALL_LIST = ("eth_chainId",)


class ProbablyNodeHasNoBlock(Exception):
    """A special exception raised when we suspect JSON-RPC node does not yet have data for a block we asked.

    - Calling a contract on a block before contract was deployed

    - Calling a contract on a block where the node does not have the block data yet

    See :py:mod:`eth_defi.provider.fallback` for details.
    """


def is_retryable_http_exception(
    exc: Exception,
    retryable_exceptions: Tuple[BaseException] = DEFAULT_RETRYABLE_EXCEPTIONS,
    retryable_status_codes: Collection[int] = DEFAULT_RETRYABLE_HTTP_STATUS_CODES,
    retryable_rpc_error_codes: Collection[int] = DEFAULT_RETRYABLE_RPC_ERROR_CODES,
    retryable_rpc_error_messages: Collection[str] = DEFAULT_RETRYABLE_RPC_ERROR_MESSAGES,
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

    :param retryable_rpc_error_messages:
        See :py:data:`DEFAULT_RETRYABLE_RPC_ERROR_MESSAGES`.

    """

    if isinstance(exc, ValueError):
        # raise ValueError(response["error"])
        # ValueError: {'message': 'Internal JSON-RPC error.', 'code': -32603}
        if len(exc.args) > 0:
            arg = exc.args[0]
            if type(arg) == dict:

                code = arg.get("code")
                message = arg.get("message", "")

                if code is None or type(code) != int:
                    raise RuntimeError(f"Bad ValueError: {arg} - {exc}")

                if code in retryable_rpc_error_codes:
                    return True

                if message in retryable_rpc_error_messages:
                    return True

                return False

    if isinstance(exc, ProbablyNodeHasNoBlock):
        return True

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


def raise_on_revert_middleware(
    make_request: Callable[[RPCEndpoint, Any], Any],
    web3: "Web3",
) -> Callable[[RPCEndpoint, Any], Any]:
    """Automatically show the transaction revert reason in Python traceback.

    - Designed to make writing unit tests more productive

    - Transaction will already revert in `eth_estimateGas` call unless you have manually
      set the gas limit for your transaction

    - If a transaction fails, this middleware display its revert reason in Python exception message

    - Tested with Anvil testing backend

    - May interfere with :py:func:`http_retry_request_with_sleep_middleware`, others,
      so don't use in production

    .. code-block::

        from eth_defi.middleware import revert_reason_middleware

        # Fix the web3.py stock gas estimate middlware with smarted one
        web3.middleware_onion.replace("gas_estimate", revert_reason_aware_buffered_gas_estimate_middleware)

        # Now you check the revert reason as the following

    """

    def middleware(method: RPCEndpoint, params: Any) -> RPCResponse:
        if method == "eth_sendTransaction":
            transaction = params[0]
            if "gas" not in transaction:
                transaction = assoc(
                    transaction,
                    "gas",
                    hex(get_buffered_gas_estimate(web3, transaction)),
                )
                return make_request(method, [transaction])
        return make_request(method, params)

    return middleware


#
#
#
# Monkey patch
# https://github.com/ethereum/web3.py/issues/2936
#
#

from eth_utils.toolz import compose
from web3._utils.transactions import fill_nonce, fill_transaction_defaults
from web3.middleware.signing import format_transaction, gen_normalized_accounts
from web3.types import Middleware, RPCEndpoint, RPCResponse


def construct_sign_and_send_raw_middleware_anvil(
    private_key_or_account,
) -> Middleware:
    """Capture transactions sign and send as raw transactions

    .. note ::

        This is web3.py middleware that has been fixed for Anvil/other JSON-RPC compatibility.

    Keyword arguments:
    private_key_or_account -- A single private key or a tuple,
    list or set of private keys. Keys can be any of the following formats:
      - An eth_account.LocalAccount object
      - An eth_keys.PrivateKey object
      - A raw private key as a hex string or byte string
    """

    accounts = gen_normalized_accounts(private_key_or_account)

    def sign_and_send_raw_middleware(make_request: Callable[[RPCEndpoint, Any], Any], w3: "Web3") -> Callable[[RPCEndpoint, Any], RPCResponse]:
        format_and_fill_tx = compose(format_transaction, fill_transaction_defaults(w3), fill_nonce(w3))

        def middleware(method: RPCEndpoint, params: Any) -> RPCResponse:
            if method != "eth_sendTransaction":
                return make_request(method, params)
            else:
                transaction = format_and_fill_tx(params[0])

            if "from" not in transaction:
                return make_request(method, params)
            elif transaction.get("from") not in accounts:
                return make_request(method, params)

            account = accounts[transaction["from"]]
            raw_tx = account.sign_transaction(transaction).rawTransaction
            return make_request(RPCEndpoint("eth_sendRawTransaction"), [raw_tx.hex()])

        return middleware

    return sign_and_send_raw_middleware


def static_call_cache_middleware(
    make_request: Callable[[RPCEndpoint, Any], Any],
    web3: "Web3",
) -> Callable[[RPCEndpoint, Any], Any]:
    """Cache JSON-RPC call values that never chance.

    The cache is web3 instance itself, to allow sharing the cache
    between different JSON-RPC providers.
    """
    def middleware(method: RPCEndpoint, params: Any) -> RPCResponse:

        cache = getattr(web3, "static_call_cache", {})
        if method in STATIC_CALL_LIST:
            cached = cache.get(method)
            if cached:
                return cached

        resp = make_request(method, params)
        cache[method] = resp
        web3.static_call_cache = cache
        return resp

    return middleware

