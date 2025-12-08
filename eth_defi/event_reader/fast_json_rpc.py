"""JSON-RPC decoding optimised for web3.py.

Monkey-patches JSON decoder to use ujson.
"""

import logging
import threading
from json import JSONDecodeError
from typing import Any, cast

import ujson
from web3 import Web3
from web3.providers import JSONBaseProvider
from web3.providers.rpc import HTTPProvider
from web3.types import RPCEndpoint, RPCResponse

from eth_defi.compat import get_response_from_post_request
from eth_defi.utils import get_url_domain

logger = logging.getLogger(__name__)


last_headers_storage = threading.local()


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

    # Clear the last headers
    last_headers_storage.headers = {}

    try:
        raw_response = get_response_from_post_request(
            self.endpoint_uri,
            data=request_data,
            **self.get_request_kwargs(),
        )
    except Exception as e:
        # Low level network error like ConnectionErro
        last_headers_storage.headers = {"exception": str(e), "method": method, "endpoint_uri": get_url_domain(self.endpoint_uri)}
        raise

    counter = getattr(last_headers_storage, "counter", 0)
    counter += 1
    last_headers_storage.counter = counter

    # Pass dRPC / etc upstream RPC headers all along for debug
    if raw_response.status_code >= 300:
        # Only record headers in the case of problems
        thread_id = threading.get_ident()
        headers_id = f"{counter}-{thread_id}"
        last_headers_storage.headers = {k: v for k, v in raw_response.headers.items()}
        last_headers_storage.headers["method"] = method
        last_headers_storage.headers["endpoint_uri"] = get_url_domain(self.endpoint_uri)
        last_headers_storage.headers["status_code"] = raw_response.status_code
        last_headers_storage.headers["headers-track-id"] = headers_id
        if raw_response.status_code >= 400:
            last_headers_storage.headers["status_text"] = raw_response.text

    raw_response.raise_for_status()

    try:
        decoded = _fast_decode_rpc_response(raw_response.content)
        return decoded
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


def get_last_headers() -> dict:
    """Get last HTTP reply headers of the JSON-RPC API call.

    - Debug for RPC providers
    - Gives insight for routing of proxy providers like dRPC so you can disable faulty market place providers

    Example output:

    .. code-block:: plain

        {'Date': 'Wed, 09 Apr 2025 14:56:48 GMT', 'Content-Type': 'application/json', 'Content-Length': '112', 'Connection': 'keep-alive', 'access-control-allow-origin': '*', 'Content-Encoding': 'gzip', 'vary': 'Accept-Encoding', 'x-drpc-owner-id': '2580e13b-d8a6-48a3-bdaa-67bc5972c7f5', 'x-drpc-owner-tier': 'paid', 'x-drpc-provider-id': 'drpc-core-free', 'x-drpc-trace-id': '3ac007d0de35bae3d390789476db31cd', 'strict-transport-security': 'max-age=31536000; includeSubDomains', 'cf-cache-status': 'DYNAMIC', 'Server': 'cloudflare', 'CF-RAY': '92dada6e5a7aed3f-SJC'}

    :return:
        Last HTTP reply headers from the JSON-RPC call.
    """
    return getattr(last_headers_storage, "headers", {})
