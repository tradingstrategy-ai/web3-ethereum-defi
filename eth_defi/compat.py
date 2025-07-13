# compat.py
"""
v6/v7 compatibility module
"""

from importlib.metadata import version
from packaging.version import Version
import eth_abi

pkg_version = version("web3")
WEB3_PY_V7 = Version(pkg_version) >= Version("7.0.0")


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


# ABI compatibility
if WEB3_PY_V7:
    from eth_utils.abi import abi_to_signature as _abi_to_signature

    encode_function_args = encode_function_args_v7
else:
    from eth_utils.abi import _abi_to_signature

    encode_function_args = encode_function_args_v6

abi_to_signature = _abi_to_signature
