"""JSON-RPC decoding optimised for web3.py.

Monkey-patches JSON decoder to use ujson.
"""

import logging
from json import JSONDecodeError
from typing import Any, cast

import ujson
from web3 import Web3
from web3._utils.request import get_response_from_post_request
from web3.providers import JSONBaseProvider
from web3.providers.rpc import HTTPProvider
from web3.types import RPCEndpoint, RPCResponse

logger = logging.getLogger(__name__)


class PartialHttpResponseException(JSONDecodeError):
    """IPCProvider expects JSONDecodeErrors, not value errors."""


def _fast_decode_rpc_response(raw_response: bytes) -> RPCResponse:
    """Uses ujson for speeding up JSON decoding instead of web3.py default JSON."""
    try:
        decoded = ujson.loads(raw_response)
    except ValueError as e:
        # We received partial JSON-RPC response over IPC.
        # Signal the underlying stack to keep reading
        # See IPCProvider.make_request()
        raise PartialHttpResponseException("Suspected partial HTTP response", "", 0) from e
    return cast(RPCResponse, decoded)


def _make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
    """Add response headers logging in case of exception raised."""

    request_data = self.encode_rpc_request(method, params)
    raw_response = get_response_from_post_request(
        self.endpoint_uri,
        data=request_data,
        **self.get_request_kwargs(),
    )
    raw_response.raise_for_status()

    try:
        return _fast_decode_rpc_response(raw_response.content)
    except Exception as e:
        logger.error(
            "Unexpected decode RPC response error: %s, current provider ID is %s",
            str(e),
            raw_response.headers.get("x-drpc-provider-id", ""),
            extra={"response_headers": raw_response.headers},
        )
        raise


def patch_provider(provider: JSONBaseProvider):
    """Monkey-patch web3.py provider for faster JSON decoding and additional logging."""
    if isinstance(provider, HTTPProvider):
        provider.make_request = _make_request.__get__(provider)
    provider.decode_rpc_response = _fast_decode_rpc_response


def patch_web3(web3: Web3):
    """Monkey-patch web3.py provider for faster JSON decoding and additional logging.

    This greatly improves JSON-RPC API access speeds, when fetching
    multiple and large responses.

    Example:

    .. code-block:: python

        from eth_defi.event_reader.fast_json_rpc import patch_web3
        patch_web3(web3)
    """
    patch_provider(web3.provider)
