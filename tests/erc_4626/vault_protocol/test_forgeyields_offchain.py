"""Test ForgeYields offchain metadata API.

ForgeYields is a cross-chain yield aggregator. Most TVL sits on Starknet,
but the Ethereum TokenGateway only shows a small residual. The canonical
TVL comes from the proprietary API at api.forgeyields.com/strategies.

1. Mock the API response and verify parsing
2. Verify per-vault lookup by Ethereum gateway address
3. Verify unknown address returns None
4. (Live) One opt-in integration test hitting the real API
"""

import datetime
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

import eth_defi.erc_4626.vault_protocol.forgeyields.offchain_metadata as forgeyields_offchain
from eth_defi.erc_4626.vault_protocol.forgeyields.offchain_metadata import (
    fetch_forgeyields_strategies,
    fetch_forgeyields_vault_metadata,
)


#: fyUSDC Ethereum gateway
FYUSDC_ADDRESS = "0x943109DC7C950da4592d85ebd4Cfed007Af64670"

#: Minimal mock API response for /strategies
MOCK_STRATEGIES_RESPONSE = [
    {
        "name": "ForgeYields USDC",
        "symbol": "fyUSDC",
        "token_gateway_per_domain": [
            {"domain": "ethereum", "token_gateway": FYUSDC_ADDRESS},
            {"domain": "starknet", "token_gateway": "0x07fDcec0ceF01294C9C3D52415215949805C77bAe8003702A7928fd6D2c36BC1"},
        ],
        "integrationInfo": {
            "overallUsdPrice": "1085984.11",
            "overallApy": "25.07",
        },
    },
    {
        "name": "ForgeYields ETH",
        "symbol": "fyETH",
        "token_gateway_per_domain": [
            {"domain": "ethereum", "token_gateway": "0x98CD770b4e9905B1263f0c9ae6cdE34E1923508E"},
        ],
        "integrationInfo": {
            "overallUsdPrice": "535854.47",
            "overallApy": "8.91",
        },
    },
    {
        "name": "ForgeYields WBTC",
        "symbol": "fyWBTC",
        "token_gateway_per_domain": [
            {"domain": "ethereum", "token_gateway": "0xeDca8230366B9eaFf06becdD1D261577836AA507"},
        ],
        "integrationInfo": {
            "overallUsdPrice": "183247.03",
            "overallApy": "10.92",
        },
    },
]


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the in-process cache before each test."""
    forgeyields_offchain._cached_strategies = None
    yield
    forgeyields_offchain._cached_strategies = None


def _write_mock_cache(tmpdir: str) -> Path:
    """Write mock API data to a cache file, returning the cache dir."""
    cache_path = Path(tmpdir)
    cache_file = cache_path / "forgeyields_strategies.json"
    # Serialise with string tvl_usd as the cache format expects
    serialisable = {}
    for raw in MOCK_STRATEGIES_RESPONSE:
        for gw in raw.get("token_gateway_per_domain", []):
            if gw["domain"] == "ethereum":
                from web3 import Web3

                addr = Web3.to_checksum_address(gw["token_gateway"]).lower()
                info = raw.get("integrationInfo", {})
                serialisable[addr] = {
                    "name": raw["name"],
                    "symbol": raw["symbol"],
                    "tvl_usd": info.get("overallUsdPrice", "0"),
                    "apy": float(info["overallApy"]) if info.get("overallApy") else None,
                    "ethereum_gateway": Web3.to_checksum_address(gw["token_gateway"]),
                }
    with cache_file.open("wt") as f:
        json.dump(serialisable, f)
    return cache_path


def test_fetch_strategies_from_cache():
    """Fetch strategies from a mock cache file.

    1. Write mock API response to a cache file
    2. Load strategies using fetch_forgeyields_strategies with the cache
    3. Verify all three known gateways are present with correct TVL
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = _write_mock_cache(tmpdir)

        # 1. Load from cache (max_cache_duration is large so it reads the file)
        strategies = fetch_forgeyields_strategies(
            cache_path=cache_path,
            max_cache_duration=datetime.timedelta(days=999),
        )

        # 2. Verify all three gateways
        assert len(strategies) >= 3
        assert FYUSDC_ADDRESS.lower() in strategies

        # 3. Verify TVL
        meta = strategies[FYUSDC_ADDRESS.lower()]
        assert meta["tvl_usd"] == Decimal("1085984.11")
        assert meta["symbol"] == "fyUSDC"
        assert meta["apy"] == pytest.approx(25.07)


def test_fetch_vault_metadata_by_address():
    """Verify per-vault lookup using mock data.

    1. Populate the in-process cache with mock data
    2. Look up fyUSDC by its gateway address
    3. Verify name, symbol, and TVL
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = _write_mock_cache(tmpdir)
        # Populate the in-process cache
        forgeyields_offchain._cached_strategies = fetch_forgeyields_strategies(
            cache_path=cache_path,
            max_cache_duration=datetime.timedelta(days=999),
        )

    # 2. Look up
    meta = fetch_forgeyields_vault_metadata(FYUSDC_ADDRESS)

    # 3. Verify
    assert meta is not None
    assert meta["name"] == "ForgeYields USDC"
    assert meta["tvl_usd"] == Decimal("1085984.11")


def test_unknown_address_returns_none():
    """Verify that an unknown address returns None.

    1. Populate cache with mock data
    2. Look up an address not in the mock
    3. Assert None
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = _write_mock_cache(tmpdir)
        forgeyields_offchain._cached_strategies = fetch_forgeyields_strategies(
            cache_path=cache_path,
            max_cache_duration=datetime.timedelta(days=999),
        )

    assert fetch_forgeyields_vault_metadata("0x0000000000000000000000000000000000000001") is None


@pytest.mark.skipif(
    os.environ.get("FORGE_YIELDS_LIVE_TEST") is None,
    reason="Set FORGE_YIELDS_LIVE_TEST=1 to run",
)
def test_live_api():
    """Integration test hitting the real ForgeYields API.

    1. Fetch strategies from the live API
    2. Verify fyUSDC is present with realistic TVL
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        strategies = fetch_forgeyields_strategies(
            cache_path=Path(tmpdir),
            max_cache_duration=datetime.timedelta(seconds=0),
        )

    assert FYUSDC_ADDRESS.lower() in strategies
    meta = strategies[FYUSDC_ADDRESS.lower()]
    assert meta["tvl_usd"] > Decimal("10000")
