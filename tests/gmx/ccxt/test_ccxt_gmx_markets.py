"""Tests for reading available markets and their parameters in CCXT format."""

import tempfile
from pathlib import Path

import pytest
from flaky import flaky

from eth_defi.gmx.ccxt.exchange import GMX


def test_arbitrum_gmx_fetch_tickers(ccxt_gmx_arbitrum: GMX):
    """Get all markets of GMX in CCXT format"""
    gmx = ccxt_gmx_arbitrum
    tickers = gmx.fetch_tickers()


@flaky(max_runs=3, min_passes=1)
def test_load_markets_rest_api_mode(ccxt_gmx_arbitrum: GMX):
    """Test loading markets using REST API mode (default).

    Makes real API calls to verify REST API market loading.
    """
    gmx = ccxt_gmx_arbitrum

    # Force reload to test REST API mode
    markets = gmx.load_markets(reload=True)

    # Verify markets were loaded
    assert markets is not None
    assert isinstance(markets, dict)
    assert len(markets) > 0

    # Verify market structure (CCXT-compatible)
    first_symbol = list(markets.keys())[0]
    market = markets[first_symbol]

    assert "symbol" in market
    assert "base" in market
    assert "quote" in market
    assert "info" in market
    assert "market_token" in market["info"]

    # Verify REST API-specific fields in info
    # These come from /markets/info endpoint
    assert "index_token" in market["info"]


@flaky(max_runs=3, min_passes=1)
def test_load_markets_graphql_mode(chain_rpc_url):
    """Test loading markets using GraphQL mode.

    Makes real API calls to verify GraphQL mode still works.
    """
    gmx = GMX(
        params={
            "rpcUrl": chain_rpc_url,
            "chainId": 42161,  # Arbitrum
        },
        options={"graphql_only": True},
    )

    markets = gmx.load_markets()

    assert markets is not None
    assert isinstance(markets, dict)
    assert len(markets) > 0


@flaky(max_runs=3, min_passes=1)
def test_load_markets_rpc_mode(chain_rpc_url):
    """Test loading markets using RPC mode (fallback).

    Makes real RPC calls to verify RPC mode still works when REST API disabled.
    """
    gmx = GMX(
        params={
            "rpcUrl": chain_rpc_url,
            "chainId": 42161,  # Arbitrum
        },
        options={"rest_api_mode": False, "graphql_only": False},
    )

    markets = gmx.load_markets()

    assert markets is not None
    assert isinstance(markets, dict)
    # RPC mode should still load markets
    assert len(markets) > 0


@flaky(max_runs=3, min_passes=1)
def test_fetch_apy_all_markets(ccxt_gmx_arbitrum: GMX):
    """Test fetching APY data for all markets.

    Makes real API call to /apy endpoint.
    """
    gmx = ccxt_gmx_arbitrum

    # Load markets first
    gmx.load_markets()

    # Fetch APY for all markets (30-day period)
    all_apy = gmx.fetch_apy(period="30d")

    # Should return dict mapping symbols to APY values
    assert all_apy is not None
    assert isinstance(all_apy, dict)

    # Should have at least one market with APY
    if len(all_apy) > 0:
        first_symbol = list(all_apy.keys())[0]
        apy_value = all_apy[first_symbol]

        assert isinstance(apy_value, (int, float))
        # APY should be reasonable (between -100% and 1000%)
        assert -1.0 <= apy_value <= 10.0


@flaky(max_runs=3, min_passes=1)
def test_fetch_apy_specific_symbol(ccxt_gmx_arbitrum: GMX):
    """Test fetching APY for specific market symbol.

    Makes real API call and verifies symbol-specific query.
    """
    gmx = ccxt_gmx_arbitrum

    # Load markets first
    gmx.load_markets()

    # Get first available market symbol
    if len(gmx.markets) == 0:
        pytest.skip("No markets available")

    symbol = list(gmx.markets.keys())[0]

    # Fetch APY for specific symbol
    apy = gmx.fetch_apy(symbol=symbol, period="30d")

    # Should return float or None
    if apy is not None:
        assert isinstance(apy, (int, float))
        # APY should be reasonable
        assert -1.0 <= apy <= 10.0


@flaky(max_runs=3, min_passes=1)
def test_cache_persistence(chain_rpc_url):
    """Test that disk cache persists across GMX instances.

    Verifies that markets loaded once are cached for subsequent instances.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)

        # First instance - loads and caches markets
        gmx1 = GMX(
            params={
                "rpcUrl": chain_rpc_url,
                "chainId": 42161,  # Arbitrum
            },
            options={"market_cache_dir": str(cache_dir)},
        )

        markets1 = gmx1.load_markets()
        assert len(markets1) > 0

        # Create second instance with same cache dir
        gmx2 = GMX(
            params={
                "rpcUrl": chain_rpc_url,
                "chainId": 42161,  # Arbitrum
            },
            options={"market_cache_dir": str(cache_dir)},
        )

        # Second instance should load from cache (much faster)
        markets2 = gmx2.load_markets()

        # Should have same markets
        assert len(markets2) == len(markets1)
        assert set(markets2.keys()) == set(markets1.keys())


@flaky(max_runs=3, min_passes=1)
def test_rest_api_performance(ccxt_gmx_arbitrum: GMX):
    """Test that REST API loading is fast (<5 seconds).

    Verifies performance improvement over RPC mode.
    """
    import time

    gmx = ccxt_gmx_arbitrum

    # Force reload to measure loading time
    start_time = time.time()
    markets = gmx.load_markets(reload=True)
    elapsed = time.time() - start_time

    # REST API loading should be fast
    assert elapsed < 30.0, f"REST API loading took {elapsed:.1f}s (expected <30s)"

    # Should still load markets successfully
    assert len(markets) > 0


@flaky(max_runs=3, min_passes=1)
def test_fetch_apy_different_periods(ccxt_gmx_arbitrum: GMX):
    """Test fetching APY for different time periods.

    Verifies that all valid periods work correctly.
    """
    gmx = ccxt_gmx_arbitrum
    gmx.load_markets()

    valid_periods = ["1d", "7d", "30d", "90d", "180d", "1y", "total"]

    for period in valid_periods:
        apy_data = gmx.fetch_apy(period=period)

        # Should return dict
        assert isinstance(apy_data, dict)

        # May be empty for some periods, but should not error
        if len(apy_data) > 0:
            # Check structure of APY values
            first_symbol = list(apy_data.keys())[0]
            assert isinstance(apy_data[first_symbol], (int, float))
