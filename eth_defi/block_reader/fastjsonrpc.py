"""JSON-RPC decoding optimised for web3.py.

Monkey-patches JSON decoder to use ujson.
"""

import logging
from json import JSONDecodeError

from typing import cast

import ujson

from web3 import Web3
from web3.providers import JSONBaseProvider
from web3.types import RPCResponse


logger = logging.getLogger(__name__)


class IPCFlaky(JSONDecodeError):
    """IPCProvider expects JSONDecodeErrors, not value errors."""


def _fast_decode_rpc_response(raw_response: bytes) -> RPCResponse:
    """Uses ujson for speeded up JSON decoding instead of web3.py default JSON."""
    try:
        decoded = ujson.loads(raw_response)
    except ValueError as e:
        # We received partial JSON-RPC response over IPC.
        # Signal the underlying stack to keep reading
        # See IPCProvider.make_request()
        raise IPCFlaky("Partial IPC?", "", 0) from e
    return cast(RPCResponse, decoded)


def patch_provider(provider: JSONBaseProvider):
    """Monkey-patch web3.py provider for faster JSON decoding.

    This greatly improves JSON-RPC API access speeds, when fetching
    multiple and large responses.
    """
    provider.decode_rpc_response = _fast_decode_rpc_response


def patch_web3(web3: Web3):
    """Monkey-patch web3.py provider for faster JSON decoding.

    This greatly improves JSON-RPC API access speeds, when fetching
    multiple and large responses.
    """
    patch_provider(web3.provider)
