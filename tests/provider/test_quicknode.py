"""Quicknode specific tests"""

import os

import pytest
from web3 import HTTPProvider, Web3

from eth_defi.chain import has_graphql_support

JSON_RPC_QUICKNODE = os.environ.get("JSON_RPC_QUICKNODE")
pytestmark = pytest.mark.skipif(not JSON_RPC_QUICKNODE, reason="This test needs QuickNode come node via JSON_RPC_QUICKNODE")


def test_quicknode_graphql_support():
    """Work around fake 404 response from quicknode"""
    provider = HTTPProvider(JSON_RPC_QUICKNODE)
    web3 = Web3(provider)
    assert not has_graphql_support(provider)
    assert web3.eth.block_number > 1
