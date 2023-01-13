"""Middleware tests.

Note that request retry middleware is currently a bit hard to test,
so we contain only partial coverage.
"""

import os

import pytest
import requests
from requests import Response, HTTPError
from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware, install_retry_middleware
from eth_defi.middleware import is_retryable_http_exception


JSON_RPC_POLYGON = os.environ.get("JSON_RPC_POLYGON", "https://polygon-rpc.com")


@pytest.fixture()
def web3():
    """Live Polygon web3 instance."""
    # HTTP 1.1 keep-alive
    session = requests.Session()
    web3 = Web3(HTTPProvider(JSON_RPC_POLYGON, session=session))
    web3.middleware_onion.clear()
    install_chain_middleware(web3)
    install_retry_middleware(web3)
    return web3


def test_too_many_requests_is_retryable():
    """Check if detect too many requests as retryable exception."""

    resp = Response()
    resp.status_code = 429

    exc = HTTPError(response=resp)
    assert is_retryable_http_exception(exc)


def test_connection_error_is_retryable():
    """Check if detect too many requests as retryable exception."""
    exc = requests.exceptions.ConnectionError()
    assert is_retryable_http_exception(exc)


def test_with_retry(web3):
    """Normal API request with retry middleware."""
    assert web3.eth.block_number > 0


def test_pokt_network_broken():
    """Test for Internal server error from Pokt relay."""
    exc = ValueError({"message": "Internal JSON-RPC error.", "code": -32603})
    assert is_retryable_http_exception(exc)
