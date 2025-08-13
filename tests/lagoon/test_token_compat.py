"""Lagoon token checker test"""

import os

import pytest

from eth_typing import HexAddress

from eth_defi.lagoon.lagoon_compatibility import check_lagoon_compatibility_with_database
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import addr

JSON_RPC_BINANCE = os.environ.get("JSON_RPC_BINANCE", None)
pytestmark = pytest.mark.skipif(not JSON_RPC_BINANCE, reason="JSON_RPC_BINANCE not set, skipping BNB smart chain tests")


@pytest.fixture()
def token_list(web3) -> list[HexAddress]:
    """List of different tokens to test token compatibility."""



def test_token_compat(token_list, tmp_path):
    database_file = tmp_path / "test_lagoon_compat.pickle"
    web3 = create_multi_provider_web3(JSON_RPC_BINANCE)

    # For the beta deployment
    compat_db = check_lagoon_compatibility_with_database(
        web3=web3,
        paths=token_list,
        vault_address=addr("0x21DA913BA04D67af88E9F709022416834AaD8F54"),
        trading_strategy_module_address=addr("0xe922ECC2596A97C4daB573e2057051022f35023f"),
        asset_manager_address=addr("0xc9EDbb9F5b3f55B7Cc87a8Af6A695f18200E47Af"),
        database_file=database_file,
    )





