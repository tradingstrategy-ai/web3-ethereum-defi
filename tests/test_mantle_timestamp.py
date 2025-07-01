"""Mantle brokeness test"""

import datetime
import os

import pytest

from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.timestamp import get_block_timestamp
from eth_defi.token import TokenDiskCache, fetch_erc20_details

JSON_RPC_MANTLE = os.environ.get("JSON_RPC_MANTLE")

pytestmark = pytest.mark.skipif(JSON_RPC_MANTLE is None, reason="JSON_RPC_MANTLE needed to run these tests")


def test_mantle_timestamp():
    """Mantle RPC is broken with Web3.py  middleware"""
    web3 = create_multi_provider_web3(JSON_RPC_MANTLE)
    timestamp = get_block_timestamp(web3, 1)
    assert timestamp == datetime.datetime(2023, 7, 2, 16, 21, 26)

    timestamp = get_block_timestamp(web3, "latest")
    assert timestamp > datetime.datetime(2023, 7, 2, 16, 21, 26)
