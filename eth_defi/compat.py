# compat.py
"""
v6/v7 compatibility module
"""

from collections import Counter
from importlib.metadata import version
from typing import Any

import eth_abi
from packaging.version import Version

pkg_version = version("web3")
WEB3_PY_V7 = Version(pkg_version) >= Version("7.0.0")

# Middleware imports with compatibility
if WEB3_PY_V7:
    from requests.exceptions import ConnectionError, HTTPError, Timeout
    from web3.middleware import ExtraDataToPOAMiddleware, Web3Middleware
    from web3.providers.rpc.utils import ExceptionRetryConfiguration
    from web3.types import RPCEndpoint
else:
    from web3.middleware import geth_poa_middleware


class APICallCounterMiddleware:
    """API call counter middleware that works for both v6 and v7"""

    def __init__(self, counter: Counter, w3=None):
        self.counter = counter
        if WEB3_PY_V7 and w3:
            # v7 - inherit from Web3Middleware
            self.__class__ = type(self.__class__.__name__, (Web3Middleware,), dict(self.__class__.__dict__))
            super().__init__(w3)

    def request_processor(self, method: RPCEndpoint, params: Any) -> tuple[RPCEndpoint, Any]:
        """Process the request and count API calls - v7 style"""
        self.counter[method] += 1
        self.counter["total"] += 1
        return method, params

    def __call__(self, make_request, web3):
        """v6 style middleware function"""

        def middleware(method: RPCEndpoint, params: Any):
            self.counter[method] += 1
            self.counter["total"] += 1
            return make_request(method, params)

        return middleware


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


def _add_function_middleware_v7(web3, middleware_func, layer):
    """Wrap v6-style function middleware for v7"""

    # Create v7-compatible class wrapper
    class MiddlewareAdapter:
        def __init__(self, func):
            self.func = func

        def __call__(self, make_request, web3):
            return self.func(make_request, web3)

    # Inject wrapped middleware
    web3.middleware_onion.inject(MiddlewareAdapter(middleware_func), layer=layer)


def add_middleware(web3, middleware_func_or_name, layer=0):
    """
    Add middleware with v6/v7 compatibility

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
        else:
            # Handle function middlewares - need to wrap with v7 class
            _add_function_middleware_v7(web3, middleware_func_or_name, layer)
    else:
        # v6 function-style middleware
        web3.middleware_onion.inject(middleware_func_or_name, layer=layer)


def clear_middleware(web3):
    """Clear all middleware with compatibility"""
    web3.middleware_onion.clear()


def install_poa_middleware(web3, layer=0):
    """Install POA middleware with v6/v7 compatibility"""
    if WEB3_PY_V7:
        # v7 uses ExtraDataToPOAMiddleware
        web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=layer)
    else:
        # v6 uses geth_poa_middleware
        web3.middleware_onion.inject(geth_poa_middleware, layer=layer)


def install_retry_middleware_compat(web3):
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

        web3.middleware_onion.inject(http_retry_request_with_sleep_middleware, layer=0)


def install_api_call_counter_middleware_compat(web3):
    """Install API call counter middleware with v6/v7 compatibility"""
    api_counter = Counter()

    if WEB3_PY_V7:
        # v7 class-based middleware
        counter_middleware = APICallCounterMiddleware(api_counter, web3)
        web3.middleware_onion.inject(lambda w3: counter_middleware, layer=0)
    else:
        # v6 function-based middleware
        counter_middleware = APICallCounterMiddleware(api_counter)
        web3.middleware_onion.inject(counter_middleware, layer=0)

    return api_counter


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
    if WEB3_PY_V7:
        from web3.utils import get_abi_element_info

        if len(args) == 4:
            # Called as: get_function_info(fn_name, codec, contract_abi, args)
            fn_name, codec, contract_abi, fn_args = args
            fn_info = get_abi_element_info(contract_abi, fn_name, *fn_args, abi_codec=codec)
            fn_abi = fn_info["abi"]

            # Create function selector manually for v6 compatibility
            from eth_utils import function_signature_to_4byte_selector

            input_types = [inp["type"] for inp in fn_abi["inputs"]]
            signature = f"{fn_abi['name']}({','.join(input_types)})"
            fn_selector = function_signature_to_4byte_selector(signature)

            return fn_abi, fn_selector, fn_args

        elif len(args) == 5:
            # Called as: get_function_info(fn_identifier, codec, contract_abi, fn_abi, args)
            fn_identifier, codec, contract_abi, fn_abi, fn_args = args

            # For this signature, fn_abi is already provided, so we can use it directly
            # Just need to create the selector
            from eth_utils import function_signature_to_4byte_selector

            input_types = [inp["type"] for inp in fn_abi["inputs"]]
            signature = f"{fn_abi['name']}({','.join(input_types)})"
            fn_selector = function_signature_to_4byte_selector(signature)

            return fn_abi, fn_selector, fn_args
    return None


# Version-based aliasing
if WEB3_PY_V7:
    from eth_utils.abi import abi_to_signature as _abi_to_signature
    from web3._utils.http_session_manager import HTTPSessionManager
    from web3.middleware import SignAndSendRawMiddlewareBuilder

    sessions = HTTPSessionManager()
    _get_response_from_post_request = sessions.get_response_from_post_request

    encode_function_args = encode_function_args_v7
    get_function_info = get_function_info_v7
    construct_sign_and_send_raw_middleware = SignAndSendRawMiddlewareBuilder
else:
    from eth_utils.abi import _abi_to_signature
    from web3._utils.request import get_response_from_post_request as _get_response_from_post_request

    from eth_defi.compat import construct_sign_and_send_raw_middleware

    encode_function_args = encode_function_args_v6
    get_function_info = get_function_info_v6

abi_to_signature = _abi_to_signature
get_response_from_post_request = _get_response_from_post_request
