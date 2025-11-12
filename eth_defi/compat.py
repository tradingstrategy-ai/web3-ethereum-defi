# compat.py
"""
v6/v7 compatibility module
"""

import datetime
from importlib.metadata import version

from eth_typing import HexStr
from packaging.version import Version
import eth_abi
from collections import Counter
from typing import Any, Callable
from web3.types import RPCEndpoint, RPCResponse

from web3 import HTTPProvider, Web3
from web3.contract import Contract

pkg_version = version("web3")
WEB3_PY_V7 = Version(pkg_version) >= Version("7.0.0")

# Middleware imports with compatibility
if WEB3_PY_V7:
    from web3.middleware import Web3Middleware
    from web3.providers.rpc.utils import ExceptionRetryConfiguration
    from requests.exceptions import ConnectionError, HTTPError, Timeout

    # Fallback if it moved or doesn't exist in v7
    def check_if_retry_on_failure(method):
        # Default allowlist for v7 if the function is not available
        DEFAULT_ALLOWLIST = (
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
        return method in DEFAULT_ALLOWLIST
else:
    from web3.middleware.exception_retry_request import check_if_retry_on_failure

# Replace the APICallCounterMiddleware class and related functions with this:

if WEB3_PY_V7:

    class APICallCounterMiddleware(Web3Middleware):
        """v7 API call counter middleware"""

        def __init__(self, w3, counter: Counter):
            super().__init__(w3)
            self.counter = counter

        def request_processor(self, method: RPCEndpoint, params: Any) -> tuple[RPCEndpoint, Any]:
            """Process the request and count API calls"""
            self.counter[method] += 1
            self.counter["total"] += 1
            return method, params
else:

    class APICallCounterMiddleware:
        """v6 API call counter middleware"""

        def __init__(self, counter: Counter):
            self.counter = counter

        def __call__(self, make_request, web3):
            """v6 style middleware function"""

            def middleware(method: RPCEndpoint, params: Any):
                self.counter[method] += 1
                self.counter["total"] += 1
                return make_request(method, params)

            return middleware


def install_api_call_counter_middleware_compat(web3):
    """Install API call counter middleware with v6/v7 compatibility"""
    api_counter = Counter()

    if WEB3_PY_V7:
        # v7 class-based middleware - create the class properly
        counter_middleware = APICallCounterMiddleware(web3, api_counter)
        web3.middleware_onion.inject(counter_middleware, layer=0)
    else:
        # v6 function-based middleware
        counter_middleware = APICallCounterMiddleware(api_counter)
        web3.middleware_onion.inject(counter_middleware, layer=0)

    return api_counter


def _add_function_middleware_v7(web3, middleware_func, layer):
    """Wrap v6-style function middleware for v7 - FIXED VERSION"""

    class MiddlewareAdapter(Web3Middleware):
        def __init__(self, w3, func):
            super().__init__(w3)
            self.func = func
            # Create the actual middleware by calling the function
            self.middleware = self.func(self._make_request, w3)

        def _make_request(self, method, params):
            # This should be overridden by the actual middleware
            return method, params

        def request_processor(self, method: RPCEndpoint, params: Any) -> tuple[RPCEndpoint, Any]:
            # Call the wrapped v6 middleware
            return self.middleware(method, params)

    # Inject wrapped middleware
    web3.middleware_onion.inject(MiddlewareAdapter(web3, middleware_func), layer=layer)


def add_middleware(web3, middleware_func_or_name, layer=0):
    """
    Add middleware with v6/v7 compatibility - FIXED VERSION

    Args:
        web3: Web3 instance
        middleware_func_or_name: Either middleware function or string name
        layer: Layer to inject at (default 0)
    """
    if WEB3_PY_V7:
        # v7 class-style middleware handling
        if isinstance(middleware_func_or_name, str):
            # Handle named middlewares
            _add_named_middleware_v7(web3, middleware_func_or_name, layer)
        elif hasattr(middleware_func_or_name, "request_processor"):
            # Already a v7-style middleware class
            web3.middleware_onion.inject(middleware_func_or_name, layer=layer)
        else:
            # Handle function middlewares - need to wrap with v7 class
            _add_function_middleware_v7(web3, middleware_func_or_name, layer)
    else:
        # v6 function-style middleware
        web3.middleware_onion.inject(middleware_func_or_name, layer=layer)


def check_if_retry_on_failure_v6(method):
    """v6 implementation of check_if_retry_on_failure"""
    if not WEB3_PY_V7:
        from web3.middleware.exception_retry_request import check_if_retry_on_failure

        return check_if_retry_on_failure(method)
    return None


def check_if_retry_on_failure_v7(method):
    """v7 implementation of check_if_retry_on_failure"""
    if WEB3_PY_V7:
        # Try to import from v7 location, or use fallback
        try:
            from web3.middleware.exception_retry_request import check_if_retry_on_failure

            return check_if_retry_on_failure(method)
        except ModuleNotFoundError:
            # Fallback allowlist if function is not available in v7
            DEFAULT_ALLOWLIST = (
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
            return method in DEFAULT_ALLOWLIST
    return None


def exception_retry_middleware_v6(
    make_request: Callable[[RPCEndpoint, Any], RPCResponse],
    web3: "Web3",
    retryable_exceptions,
    retryable_status_codes,
    retryable_rpc_error_codes,
    retries: int = 10,
    sleep: float = 5.0,
    backoff: float = 1.6,
) -> Callable[[RPCEndpoint, Any], RPCResponse | None] | None:
    """v6 implementation of exception_retry_middleware"""
    if not WEB3_PY_V7:
        import time
        from pprint import pformat
        from eth_defi.event_reader.fast_json_rpc import get_last_headers

        # Import the helper function we need
        from eth_defi.middleware import is_retryable_http_exception
        import logging

        logger = logging.getLogger(__name__)

        def middleware(method: RPCEndpoint, params: Any):
            nonlocal sleep
            current_sleep = sleep

            # Check if the RPC method is whitelisted for multiple retries
            if check_if_retry_on_failure(method):
                # Try to recover from any JSON-RPC node error, sleep and try again
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
    return None


def exception_retry_middleware_v7(
    make_request: Callable[[RPCEndpoint, Any], RPCResponse],
    web3: "Web3",
    retryable_exceptions,
    retryable_status_codes,
    retryable_rpc_error_codes,
    retries: int = 10,
    sleep: float = 5.0,
    backoff: float = 1.6,
) -> Callable[[RPCEndpoint, Any], RPCResponse | None] | None:
    """v7 implementation of exception_retry_middleware - uses provider config when possible"""
    if WEB3_PY_V7:
        import logging

        logger = logging.getLogger(__name__)

        # In v7, we recommend using provider-level retry configuration
        # But if someone really wants middleware, we'll provide it
        logger.warning("exception_retry_middleware is deprecated in web3.py v7+. Consider using ExceptionRetryConfiguration on your provider instead.")

        # For v7, we'll still provide the middleware but recommend against it
        import time
        from pprint import pformat
        from eth_defi.event_reader.fast_json_rpc import get_last_headers
        from eth_defi.middleware import is_retryable_http_exception

        def middleware(method: RPCEndpoint, params: Any):
            nonlocal sleep
            current_sleep = sleep

            # Check if the RPC method is whitelisted for multiple retries
            if check_if_retry_on_failure(method):
                # Try to recover from any JSON-RPC node error, sleep and try again
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
    return None


def _add_named_middleware_v7(web3, middleware_name, layer):
    """Handle named middlewares in v7"""
    # Map common middleware names to v7 equivalents
    middleware_map = {
        "static_call_cache": "static_call_cache_middleware",
        "retry": "retry_middleware",
    }

    actual_name = middleware_map.get(middleware_name, middleware_name)
    # v7 implementation for named middleware
    # TODO: Implement based v7 API
    pass


def clear_middleware(web3_or_provider: Web3 | HTTPProvider) -> None:
    """Clear all middleware with v6/v7 compatibility - handles both Web3 instances and providers"""

    # Check if it's a Web3 instance
    if hasattr(web3_or_provider, "middleware_onion"):
        # It's a Web3 instance - clear middleware onion
        web3_or_provider.middleware_onion.clear()
        return

    # Check if it's a provider with middlewares (v6 style)
    if hasattr(web3_or_provider, "middlewares"):
        # It's a v6 provider - clear middlewares
        web3_or_provider.middlewares.clear()
        return

    # For v7 providers or unknown objects, do nothing
    # In v7, providers don't have middleware - it's managed at Web3 level
    pass


def install_poa_middleware(web3, layer=0):
    """Install POA middleware with v6/v7 compatibility"""
    if WEB3_PY_V7:
        # v7 uses ExtraDataToPOAMiddleware - instantiate it properly
        poa_middleware = ExtraDataToPOAMiddleware(web3)
        web3.middleware_onion.inject(poa_middleware, layer=layer)
    else:
        # v6 uses geth_poa_middleware
        web3.middleware_onion.inject(geth_poa_middleware, layer=layer)


def install_retry_middleware_compat(web3: HTTPProvider, layer: int = 0):
    """Install retry middleware with v6/v7 compatibility"""
    if WEB3_PY_V7:
        # v7 uses ExceptionRetryConfiguration on provider
        provider = web3.provider
        if hasattr(provider, "exception_retry_configuration"):
            provider.exception_retry_configuration = ExceptionRetryConfiguration(
                errors=(ConnectionError, HTTPError, Timeout),
                retries=10,  # defaults to 5
                backoff_factor=0.5,  # defaults to 0.125
            )
    else:
        # v6 uses middleware injection
        from eth_defi.middleware import http_retry_request_with_sleep_middleware

        web3.middleware_onion.inject(http_retry_request_with_sleep_middleware, layer=layer)


def encode_function_args_v6(func, args):
    """v6 implementation"""
    if not WEB3_PY_V7:
        from web3._utils.contracts import get_function_info

        web3 = func.w3
        fn_abi, fn_selector, aligned_fn_arguments = get_function_info(
            func.fn_name,
            web3.codec,
            func.contract_abi,
            args=args,
        )
        arg_types = [t["type"] for t in fn_abi["inputs"]]
        encoded_args = eth_abi.encode(arg_types, args)
        return encoded_args
    return None


def encode_function_args_v7(func, args):
    """v7 implementation"""
    if WEB3_PY_V7:
        from web3.utils import get_abi_element_info

        web3 = func.w3
        fn_info = get_abi_element_info(func.contract_abi, func.fn_name, *args, abi_codec=web3.codec)
        fn_abi = fn_info["abi"]
        arg_types = [t["type"] for t in fn_abi["inputs"]]
        encoded_args = eth_abi.encode(arg_types, args)
        return encoded_args
    return None


def get_function_info_v6(*args, **kwargs):
    """v6 get_function_info - handles multiple signatures"""
    if not WEB3_PY_V7:
        from web3._utils.contracts import get_function_info

        if len(args) == 4:
            # Called as: get_function_info(fn_name, codec, contract_abi, args=args)
            return get_function_info(args[0], args[1], args[2], args=args[3])
        elif len(args) == 5:
            # Called as: get_function_info(fn_identifier, codec, contract_abi, fn_abi, args)
            return get_function_info(args[0], args[1], args[2], args[3], args[4])
        else:
            # Pass through with whatever was provided
            return get_function_info(*args, **kwargs)
    return None


def get_function_info_v7(*args, **kwargs):
    """v7 get_function_info equivalent - returns v6-compatible format"""
    from web3.utils import get_abi_element_info
    from eth_utils.abi import abi_to_signature
    from eth_utils import function_signature_to_4byte_selector

    # Handle the case: get_function_info(fn_name, codec, contract_abi, args=func.args)
    if len(args) == 3 and "args" in kwargs:
        fn_name, codec, contract_abi = args
        fn_args = kwargs["args"]

        fn_info = get_abi_element_info(contract_abi, fn_name, *fn_args, abi_codec=codec)
        fn_abi = fn_info["abi"]

        signature = abi_to_signature(fn_abi)
        fn_selector = function_signature_to_4byte_selector(signature)

        return fn_abi, fn_selector, fn_args

    # Handle the original 4-argument case: get_function_info(fn_name, codec, contract_abi, fn_args)
    elif len(args) == 4:
        fn_name, codec, contract_abi, fn_args = args
        fn_info = get_abi_element_info(contract_abi, fn_name, *fn_args, abi_codec=codec)
        fn_abi = fn_info["abi"]

        signature = abi_to_signature(fn_abi)
        fn_selector = function_signature_to_4byte_selector(signature)

        return fn_abi, fn_selector, fn_args

    # Handle the 5-argument case: get_function_info(fn_identifier, codec, contract_abi, fn_abi, fn_args)
    elif len(args) == 5:
        fn_identifier, codec, contract_abi, fn_abi, fn_args = args

        signature = abi_to_signature(fn_abi)
        fn_selector = function_signature_to_4byte_selector(signature)

        return fn_abi, fn_selector, fn_args

    # If no pattern matches, raise an informative error instead of returning None
    else:
        raise ValueError(f"Unsupported argument pattern for get_function_info: {len(args)} positional args, kwargs: {list(kwargs.keys())}")


def encode_abi_compat(contract: Contract, fn_name: str, args: list[Any]) -> HexStr:
    """Encode ABI with v6/v7 compatibility.

    In v6: contract.encodeABI(fn_name="function_name", args=[...])
    In v7: contract.encode_abi(fn_name="function_name", args=[...])

    :param contract: Web3 contract instance
    :param fn_name: Function name to encode
    :param args: Arguments for the function
    :return: Encoded ABI string
    """

    # TODO: Web3 v6 can have both encodeABI and encode_abi

    # Fall back to v6 method
    if hasattr(contract, "encodeABI"):
        return contract.encodeABI(fn_name=fn_name, args=args)
    # Check if v7 method exists
    elif hasattr(contract, "encode_abi"):
        return contract.encode_abi(abi_element_identifier=fn_name, args=args)
    else:
        raise AttributeError(f"Contract {contract} has neither encode_abi nor encodeABI methods")


# Version-based aliasing
if WEB3_PY_V7:
    from web3.middleware import SignAndSendRawMiddlewareBuilder, ExtraDataToPOAMiddleware
    from eth_utils.abi import abi_to_signature as _abi_to_signature
    from web3._utils.http_session_manager import HTTPSessionManager

    sessions = HTTPSessionManager()
    _get_response_from_post_request = sessions.get_response_from_post_request

    def construct_sign_and_send_raw_middleware(private_key_or_account):
        """v7 wrapper for SignAndSendRawMiddlewareBuilder to maintain v6 compatibility"""

        def create_middleware(w3):
            return SignAndSendRawMiddlewareBuilder.build(private_key_or_account, w3)

        return create_middleware

    encode_function_args = encode_function_args_v7
    get_function_info = get_function_info_v7
    exception_retry_middleware = exception_retry_middleware_v7
    check_if_retry_on_failure_compat = check_if_retry_on_failure_v7
    _geth_poa_middleware = ExtraDataToPOAMiddleware
else:
    from web3.middleware import construct_sign_and_send_raw_middleware, geth_poa_middleware
    from eth_utils.abi import _abi_to_signature
    from web3._utils.request import get_response_from_post_request as _get_response_from_post_request

    encode_function_args = encode_function_args_v6
    get_function_info = get_function_info_v6
    exception_retry_middleware = exception_retry_middleware_v6
    check_if_retry_on_failure_compat = check_if_retry_on_failure_v6
    _geth_poa_middleware = geth_poa_middleware

abi_to_signature = _abi_to_signature
get_response_from_post_request = _get_response_from_post_request
geth_poa_middleware = _geth_poa_middleware


def native_datetime_utc_now() -> datetime.datetime:
    """
    Get current UTC time as a native datetime object.

    Replacement for the deprecated datetime.datetime.utcnow().
    Returns a native datetime object (no timezone info) representing UTC time.

    This is optimized for blockchain contexts where:
    - All timestamps are assumed to be UTC
    - Timezone-aware objects add unnecessary overhead
    - native datetimes are sufficient and faster

    Returns:
        datetime.datetime: Native datetime object in UTC
    """
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def native_datetime_utc_fromtimestamp(timestamp: float) -> datetime.datetime:
    """
    Convert timestamp to native UTC datetime object.

    Replacement for the deprecated datetime.datetime.utcfromtimestamp().
    Returns a native datetime object (no timezone info) representing UTC time.

    This is optimized for blockchain contexts where:
    - All timestamps are assumed to be UTC
    - Timezone-aware objects add unnecessary overhead
    - native datetimes are sufficient and faster

    Args:
        timestamp (float): Unix timestamp (seconds since epoch)

    Returns:
        datetime.datetime: native datetime object in UTC
    """
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc).replace(tzinfo=None)


def create_http_provider(*args, **kwargs) -> HTTPProvider:
    """Web3 6/7 compatible HTTPProvider constructor.

    Example:

    .. code-block:: python

        @pytest.fixture()
        def provider_1(anvil):
            provider = create_http_provider(anvil.json_rpc_url, exception_retry_configuration=None)
            clear_middleware(provider)
            return provider

    """
    if WEB3_PY_V7:
        return HTTPProvider(*args, **kwargs)
    else:
        # v6 does not know about exception_retry_configuration
        if "exception_retry_configuration" in kwargs:
            del kwargs["exception_retry_configuration"]

    return HTTPProvider(*args, **kwargs)
