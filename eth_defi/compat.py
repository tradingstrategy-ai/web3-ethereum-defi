"""Web3.py compatibility module.

Provides helper functions and re-exports for web3.py v7.
"""

import datetime
import logging
import time
from collections import Counter
from pprint import pformat
from typing import Any, Callable

import eth_abi
from eth_typing import HexStr
from eth_utils import function_signature_to_4byte_selector
from eth_utils.abi import abi_to_signature
from requests.exceptions import ConnectionError, HTTPError, Timeout
from web3 import HTTPProvider, Web3
from web3._utils.http_session_manager import HTTPSessionManager
from web3.contract import Contract
from web3.middleware import ExtraDataToPOAMiddleware, SignAndSendRawMiddlewareBuilder, Web3Middleware
from web3.providers.rpc.utils import ExceptionRetryConfiguration
from web3.types import RPCEndpoint, RPCResponse
from web3.utils import get_abi_element_info

logger = logging.getLogger(__name__)

#: Deprecated. Always True. Kept for backwards compatibility with external consumers.
WEB3_PY_V7 = True

#: Default allowlist for RPC methods that are safe to retry on failure
DEFAULT_RETRY_ALLOWLIST = (
    "eth_call",
    "eth_getBalance",
    "eth_getCode",
    "eth_getTransactionCount",
    "eth_getBlockByNumber",
    "eth_getBlockByHash",
    "eth_getLogs",
    "eth_chainId",
    "net_version",
    "eth_blockNumber",
    "eth_gasPrice",
    "eth_estimateGas",
    "eth_getTransactionByHash",
    "eth_getTransactionReceipt",
)


def check_if_retry_on_failure(method: str) -> bool:
    """Check if an RPC method is in the retry allowlist."""
    return method in DEFAULT_RETRY_ALLOWLIST


class APICallCounterMiddleware(Web3Middleware):
    """Middleware that counts API calls per RPC method."""

    def __init__(self, w3, counter: Counter):
        super().__init__(w3)
        self.counter = counter

    def request_processor(self, method: RPCEndpoint, params: Any) -> tuple[RPCEndpoint, Any]:
        self.counter[method] += 1
        self.counter["total"] += 1
        return method, params


def install_api_call_counter_middleware_compat(web3: Web3) -> Counter:
    """Install API call counter middleware.

    :return: Counter instance tracking API calls
    """
    api_counter = Counter()
    counter_middleware = APICallCounterMiddleware(web3, api_counter)
    web3.middleware_onion.inject(counter_middleware, layer=0)
    return api_counter


def add_middleware(web3: Web3, middleware_func_or_name, layer: int = 0):
    """Add middleware to a Web3 instance.

    :param web3: Web3 instance
    :param middleware_func_or_name: Middleware class/instance or string name
    :param layer: Layer to inject at (default 0)
    """
    if isinstance(middleware_func_or_name, str):
        _add_named_middleware(web3, middleware_func_or_name, layer)
    elif hasattr(middleware_func_or_name, "request_processor"):
        web3.middleware_onion.inject(middleware_func_or_name, layer=layer)
    else:
        web3.middleware_onion.inject(middleware_func_or_name, layer=layer)


def _add_named_middleware(web3: Web3, middleware_name: str, layer: int):
    """Handle named middlewares."""
    middleware_map = {
        "static_call_cache": "static_call_cache_middleware",
        "retry": "retry_middleware",
    }
    actual_name = middleware_map.get(middleware_name, middleware_name)
    # TODO: Implement based on v7 API
    pass


def exception_retry_middleware(
    make_request: Callable[[RPCEndpoint, Any], RPCResponse],
    web3: "Web3",
    retryable_exceptions,
    retryable_status_codes,
    retryable_rpc_error_codes,
    retries: int = 10,
    sleep: float = 5.0,
    backoff: float = 1.6,
) -> Callable[[RPCEndpoint, Any], RPCResponse | None]:
    """Exception retry middleware.

    Consider using ExceptionRetryConfiguration on your provider instead.
    """
    from eth_defi.event_reader.fast_json_rpc import get_last_headers
    from eth_defi.middleware import is_retryable_http_exception

    def middleware(method: RPCEndpoint, params: Any):
        nonlocal sleep
        current_sleep = sleep

        if check_if_retry_on_failure(method):
            for i in range(retries):
                try:
                    return make_request(method, params)
                except Exception as e:
                    if is_retryable_http_exception(
                        e,
                        retryable_rpc_error_codes=retryable_rpc_error_codes,
                        retryable_status_codes=retryable_status_codes,
                        retryable_exceptions=retryable_exceptions,
                    ):
                        if i < retries - 1:
                            headers = get_last_headers()
                            logger.warning(
                                "Encountered JSON-RPC retryable error %s when calling method %s, retrying in %f seconds, retry #%d\nHeaders are: %s",
                                e,
                                method,
                                current_sleep,
                                i,
                                pformat(headers),
                            )
                            time.sleep(current_sleep)
                            current_sleep *= backoff
                            continue
                        else:
                            raise
                    raise
            return None
        else:
            try:
                return make_request(method, params)
            except Exception as e:
                raise RuntimeError(f"JSON-RPC failed for non-whitelisted method {method}: {e}") from e

    return middleware


def clear_middleware(web3_or_provider: Web3 | HTTPProvider) -> None:
    """Clear all middleware.

    Handles both Web3 instances and providers.
    """
    if hasattr(web3_or_provider, "middleware_onion"):
        web3_or_provider.middleware_onion.clear()


def install_poa_middleware(web3: Web3, layer: int = 0):
    """Install proof-of-authority middleware."""
    poa_middleware = ExtraDataToPOAMiddleware(web3)
    web3.middleware_onion.inject(poa_middleware, layer=layer)


def install_retry_middleware_compat(web3: Web3, layer: int = 0):
    """Install retry middleware using provider-level ExceptionRetryConfiguration."""
    provider = web3.provider
    if hasattr(provider, "exception_retry_configuration"):
        provider.exception_retry_configuration = ExceptionRetryConfiguration(
            errors=(ConnectionError, HTTPError, Timeout),
            retries=10,
            backoff_factor=0.5,
        )


def encode_function_args(func, args):
    """Encode function arguments for a contract call."""
    web3 = func.w3
    fn_info = get_abi_element_info(func.contract_abi, func.fn_name, *args, abi_codec=web3.codec)
    fn_abi = fn_info["abi"]
    arg_types = [t["type"] for t in fn_abi["inputs"]]
    encoded_args = eth_abi.encode(arg_types, args)
    return encoded_args


def get_function_info(*args, **kwargs):
    """Get function info from contract ABI.

    Returns a tuple of (fn_abi, fn_selector, fn_args) compatible with
    the legacy v6 get_function_info interface.
    """
    # Handle: get_function_info(fn_name, codec, contract_abi, args=func.args)
    if len(args) == 3 and "args" in kwargs:
        fn_name, codec, contract_abi = args
        fn_args = kwargs["args"]

        fn_info = get_abi_element_info(contract_abi, fn_name, *fn_args, abi_codec=codec)
        fn_abi = fn_info["abi"]

        signature = abi_to_signature(fn_abi)
        fn_selector = function_signature_to_4byte_selector(signature)

        return fn_abi, fn_selector, fn_args

    # Handle: get_function_info(fn_name, codec, contract_abi, fn_args)
    elif len(args) == 4:
        fn_name, codec, contract_abi, fn_args = args
        fn_info = get_abi_element_info(contract_abi, fn_name, *fn_args, abi_codec=codec)
        fn_abi = fn_info["abi"]

        signature = abi_to_signature(fn_abi)
        fn_selector = function_signature_to_4byte_selector(signature)

        return fn_abi, fn_selector, fn_args

    # Handle: get_function_info(fn_identifier, codec, contract_abi, fn_abi, fn_args)
    elif len(args) == 5:
        fn_identifier, codec, contract_abi, fn_abi, fn_args = args

        signature = abi_to_signature(fn_abi)
        fn_selector = function_signature_to_4byte_selector(signature)

        return fn_abi, fn_selector, fn_args

    else:
        raise ValueError(f"Unsupported argument pattern for get_function_info: {len(args)} positional args, kwargs: {list(kwargs.keys())}")


def encode_abi_compat(contract: Contract, fn_name: str, args: list[Any]) -> HexStr:
    """Encode ABI for a contract function call.

    :param contract: Web3 contract instance
    :param fn_name: Function name to encode
    :param args: Arguments for the function
    :return: Encoded ABI string
    """
    return contract.encode_abi(abi_element_identifier=fn_name, args=args)


# Re-exports
sessions = HTTPSessionManager()
get_response_from_post_request = sessions.get_response_from_post_request
geth_poa_middleware = ExtraDataToPOAMiddleware


def construct_sign_and_send_raw_middleware(private_key_or_account):
    """Wrapper for SignAndSendRawMiddlewareBuilder."""

    def create_middleware(w3):
        return SignAndSendRawMiddlewareBuilder.build(private_key_or_account, w3)

    return create_middleware


#: Kept for backwards compatibility
check_if_retry_on_failure_compat = check_if_retry_on_failure


def native_datetime_utc_now() -> datetime.datetime:
    """Get current UTC time as a naive datetime object.

    Replacement for the deprecated datetime.datetime.utcnow().
    """
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def native_datetime_utc_fromtimestamp(timestamp: float) -> datetime.datetime:
    """Convert timestamp to naive UTC datetime object.

    Replacement for the deprecated datetime.datetime.utcfromtimestamp().

    :param timestamp: Unix timestamp (seconds since epoch)
    """
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc).replace(tzinfo=None)


def create_http_provider(*args, **kwargs) -> HTTPProvider:
    """Create an HTTPProvider instance.

    Example:

    .. code-block:: python

        @pytest.fixture()
        def provider_1(anvil):
            provider = create_http_provider(anvil.json_rpc_url, exception_retry_configuration=None)
            clear_middleware(provider)
            return provider

    """
    return HTTPProvider(*args, **kwargs)
