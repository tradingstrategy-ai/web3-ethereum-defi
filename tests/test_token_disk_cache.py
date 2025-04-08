
import os

import pytest

from eth_defi.provider.multi_provider import MultiProviderWeb3Factory
from eth_defi.token import TokenDiskCache


JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests")


@pytest.mark.parametrize("max_workers", [1, 8])
def test_token_disk_cache(tmp_path, max_workers):
    """Prepopulate token cache on disk"""

    addresses = [
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC
        "0x4200000000000000000000000000000000000006",  # WETH
        "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",  # DAI
        "0x554a1283cecca5a46bc31c2b82d6702785fc72d9",  # UNI
    ]

    cache = TokenDiskCache(tmp_path / "disk_cache.sqlite")

    web3factory = MultiProviderWeb3Factory(JSON_RPC_BASE)

    # Dry cache
    result = cache.load_token_details_with_multicall(
        chain_id=8543,
        web3factory=web3factory,
        addresses=addresses,
        max_workers=max_workers,
        display_progress=False,
    )
    assert result["tokens_read"] == 4
    assert "8543-0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913".lower() in cache
    assert "8543-0x4200000000000000000000000000000000000006".lower() in cache

    cache_data = cache["8543-0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913".lower()]
    assert cache_data["name"] == "USD Coin"
    assert cache_data["symbol"] == "USDC"
    assert cache_data["decimals"] == 6
    assert cache_data["supply"] > 1_000_000

    # Warmed up cache
    result = cache.load_token_details_with_multicall(
        chain_id=8543,
        web3factory=web3factory,
        addresses=addresses,
        max_workers=max_workers,
        display_progress=False,
    )
    assert result["tokens_read"] == 0