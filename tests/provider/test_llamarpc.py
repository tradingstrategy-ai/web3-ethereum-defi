"""LlamaNodes specific tests"""

import os

import pytest
from web3 import Web3, HTTPProvider

from eth_defi.chain import has_graphql_support

JSON_RPC_LLAMA = os.environ.get("JSON_RPC_LLAMA")
pytestmark = pytest.mark.skipif(not JSON_RPC_LLAMA, reason="This test needs LlamaNodes come node via JSON_RPC_LLAMA")


def test_llama_is_bad():
    """Work around fake 404 response from llamarpc.com"""
    provider = HTTPProvider(JSON_RPC_LLAMA)
    web3 = Web3(provider)
    assert not has_graphql_support(provider)
    assert web3.eth.block_number > 1
