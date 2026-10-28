"""Test retained ForgeYields offchain metadata.

ForgeYields is a cross-chain yield aggregator. Most TVL sits on Starknet,
but the Ethereum TokenGateway only shows a small residual. The canonical
TVL comes from the proprietary API at api.forgeyields.com/strategies.

The API is disabled because it is no longer working. Verify retained metadata
is readable without expiry, unchanged on disk, and never replaced on a cold
cache; backfills must abort explicitly before modifying historical prices.
"""

import datetime
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

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
        "tvl": "1069435.712178",
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
        "tvl": "268.828125652177874350",
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
        "tvl": "2.48726603",
        "token_gateway_per_domain": [
            {"domain": "ethereum", "token_gateway": "0xeDca8230366B9eaFf06becdD1D261577836AA507"},
        ],
        "integrationInfo": {
            "overallUsdPrice": "183247.03",
            "overallApy": "10.92",
        },
    },
]


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
                    "tvl": raw.get("tvl", "0"),
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


def test_fetch_vault_metadata_by_address(monkeypatch: pytest.MonkeyPatch):
    """Verify per-vault lookup using mock data.

    1. Supply a cached strategy snapshot with mock data
    2. Look up fyUSDC by its gateway address
    3. Verify name, symbol, and TVL
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = _write_mock_cache(tmpdir)
        # Supply a cached strategy snapshot
        strategies = fetch_forgeyields_strategies(
            cache_path=cache_path,
            max_cache_duration=datetime.timedelta(days=999),
        )

    monkeypatch.setattr(forgeyields_offchain, "fetch_forgeyields_strategies", lambda: strategies)

    # 2. Look up
    meta = fetch_forgeyields_vault_metadata(FYUSDC_ADDRESS)

    # 3. Verify
    assert meta is not None
    assert meta["name"] == "ForgeYields USDC"
    assert meta["tvl_usd"] == Decimal("1085984.11")


def test_unknown_address_returns_none(monkeypatch: pytest.MonkeyPatch):
    """Verify that an unknown address returns None.

    1. Populate cache with mock data
    2. Look up an address not in the mock
    3. Assert None
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = _write_mock_cache(tmpdir)
        strategies = fetch_forgeyields_strategies(
            cache_path=cache_path,
            max_cache_duration=datetime.timedelta(days=999),
        )

    monkeypatch.setattr(forgeyields_offchain, "fetch_forgeyields_strategies", lambda: strategies)
    assert fetch_forgeyields_vault_metadata("0x0000000000000000000000000000000000000001") is None


@pytest.mark.parametrize("warm_cache", [False, True])
def test_disabled_fetch_preserves_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warm_cache: bool) -> None:
    """Disabled fetching preserves old metadata and never writes an empty copy.

    Even an explicitly expired cache must retain its original bytes and age.
    Cold installations remain unavailable instead of contacting the broken API.
    """
    request = Mock(side_effect=AssertionError("Disabled ForgeYields API must not be contacted"))
    monkeypatch.setattr(requests, "get", request)
    file = tmp_path / "forgeyields_strategies.json"
    if warm_cache:
        _write_mock_cache(str(tmp_path))
        old_time = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC).timestamp()
        os.utime(file, (old_time, old_time))
        original = file.read_bytes(), file.stat().st_mtime
    for _ in range(2):
        result = fetch_forgeyields_strategies(cache_path=tmp_path, max_cache_duration=datetime.timedelta(0))
        if warm_cache:
            assert result[FYUSDC_ADDRESS.lower()]["tvl"] == Decimal("1069435.712178")
            assert (file.read_bytes(), file.stat().st_mtime) == original
        else:
            assert result == {}
            assert not file.exists()
    request.assert_not_called()
    assert not file.with_suffix(".retry-after").exists()


def test_disabled_history_aborts_before_backfill(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing offchain history source must not masquerade as an empty backfill."""
    request = Mock(side_effect=AssertionError("Disabled ForgeYields API must not be contacted"))
    monkeypatch.setattr(requests, "get", request)
    with pytest.raises(RuntimeError, match="no longer working"):
        forgeyields_offchain.fetch_forgeyields_history()
    request.assert_not_called()
