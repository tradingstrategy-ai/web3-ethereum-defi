"""Tests for GMX leverage data fetching and calculations."""

import pytest
from decimal import Decimal

from eth_defi.gmx.graphql.client import GMXSubsquidClient


def test_calculate_max_leverage():
    """Test leverage calculation from minCollateralFactor."""

    # Test 0.5% min collateral → 100x max leverage
    result = GMXSubsquidClient.calculate_max_leverage("5000000000000000000000000000")
    assert result == 100.0

    # Test 1% min collateral → 50x max leverage
    result = GMXSubsquidClient.calculate_max_leverage("10000000000000000000000000000")
    assert result == 50.0

    # Test 0.67% min collateral → ~75x max leverage
    result = GMXSubsquidClient.calculate_max_leverage("6666666666666666666666666666")
    assert abs(result - 75.0) < 0.1  # Allow small floating point difference

    # Test zero collateral → None
    result = GMXSubsquidClient.calculate_max_leverage("0")
    assert result is None


def test_calculate_leverage_tiers():
    """Test leverage tier calculation with mocked market data."""

    # Mock market info with OI-based scaling
    market_info = {
        "minCollateralFactor": "5000000000000000000000000000",  # 0.5%
        "minCollateralFactorForOpenInterestLong": "1000000000000000",  # Small multiplier
        "minCollateralFactorForOpenInterestShort": "1000000000000000",
        "longOpenInterestUsd": "50000000000000000000000000000000",  # $50M
        "shortOpenInterestUsd": "50000000000000000000000000000000",
        "maxOpenInterestLong": "100000000000000000000000000000000",  # $100M max
        "maxOpenInterestShort": "100000000000000000000000000000000",
    }

    # Calculate tiers for longs
    tiers = GMXSubsquidClient.calculate_leverage_tiers(market_info, is_long=True, num_tiers=5)

    assert len(tiers) == 5

    # Check tier structure
    for i, tier in enumerate(tiers, 1):
        assert tier["tier"] == i
        assert "minNotional" in tier
        assert "maxNotional" in tier
        assert "maxLeverage" in tier
        assert "minCollateralFactor" in tier

        # Leverage should be positive
        assert tier["maxLeverage"] > 0

        # Notional ranges should be valid
        assert tier["maxNotional"] > tier["minNotional"]


def test_get_market_infos_includes_leverage_fields(graphql_client):
    """Test that get_market_infos returns all leverage-related fields."""

    market_infos = graphql_client.get_market_infos(limit=5)

    assert len(market_infos) > 0

    # Check first market has required fields
    first_market = market_infos[0]

    assert "minCollateralFactor" in first_market
    assert "minCollateralFactorForOpenInterestLong" in first_market
    assert "minCollateralFactorForOpenInterestShort" in first_market
    assert "maxOpenInterestLong" in first_market
    assert "maxOpenInterestShort" in first_market


def test_ccxt_markets_include_leverage_limits(gmx_config):
    """Test that CCXT fetch_markets includes leverage limits."""
    from eth_defi.gmx.ccxt.exchange import GMX

    gmx = GMX(gmx_config)
    markets = gmx.fetch_markets()

    assert len(markets) > 0

    # Check that markets have leverage limits structure
    for market in markets[:5]:  # Check first 5 markets
        leverage_limits = market["limits"]["leverage"]
        assert "min" in leverage_limits
        assert "max" in leverage_limits
        assert leverage_limits["min"] == 1.1

        # Max leverage may be None if subsquid data unavailable
        if leverage_limits["max"] is not None:
            assert leverage_limits["max"] > 0
            assert leverage_limits["max"] >= leverage_limits["min"]


def test_fetch_market_leverage_tiers(gmx_config):
    """Test CCXT fetch_market_leverage_tiers method."""
    from eth_defi.gmx.ccxt.exchange import GMX

    gmx = GMX(gmx_config)

    # Fetch tiers for BTC/USDC
    tiers = gmx.fetch_market_leverage_tiers("BTC/USDC", {"side": "long"})

    # Should return tiers if data is available
    assert isinstance(tiers, list)

    if len(tiers) > 0:
        # Verify tier structure
        for tier in tiers:
            assert "tier" in tier
            assert "minNotional" in tier
            assert "maxNotional" in tier
            assert "maxLeverage" in tier
            assert tier["maxLeverage"] > 0


def test_fetch_leverage_tiers_bulk(gmx_config):
    """Test CCXT fetch_leverage_tiers for multiple markets."""
    from eth_defi.gmx.ccxt.exchange import GMX

    gmx = GMX(gmx_config)

    # Fetch tiers for multiple markets
    symbols = ["BTC/USDC", "ETH/USDC"]
    all_tiers = gmx.fetch_leverage_tiers(symbols, {"side": "long"})

    assert isinstance(all_tiers, dict)
    assert "BTC/USDC" in all_tiers
    assert "ETH/USDC" in all_tiers

    # Each symbol should have a list (may be empty)
    for symbol in symbols:
        assert isinstance(all_tiers[symbol], list)
