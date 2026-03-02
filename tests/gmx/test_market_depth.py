"""
Tests for GMX market depth and price impact analysis.

Unit tests (no network) validate the pure-Python price impact formula and
the binary search helper.  Network tests make real REST API calls.
On-chain tests read price impact parameters from the DataStore contract.
"""

import pytest
from flaky import flaky

from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.market_depth import (
    MarketDepthInfo,
    PriceImpactParams,
    estimate_position_price_impact,
    fetch_price_impact_params,
    find_max_position_size,
    parse_market_depth,
)
from tests.gmx.conftest import GMX_TEST_RETRY_CONFIG


# ---------------------------------------------------------------------------
# Shared fixture: realistic price impact params for ETH/USD on Arbitrum
# (approximate values -- exact values change with governance, but the order
#  of magnitude is stable for testing purposes)
# ---------------------------------------------------------------------------

_ETH_PARAMS = PriceImpactParams(
    # positionImpactFactor for ETH: ~2e-9 (in 30-decimal)
    positive_factor=2_000_000_000_000_000_000_000,  # 2e-9 * 10^30
    negative_factor=4_000_000_000_000_000_000_000,  # 4e-9 * 10^30
    positive_exponent=2_000_000_000_000_000_000_000_000_000_000,  # 2.0 * 10^30
    negative_exponent=2_000_000_000_000_000_000_000_000_000_000,  # 2.0 * 10^30
    max_positive_factor=4_000_000_000_000_000_000_000_000_000,  # 0.004 * 10^30
    max_negative_factor=0,
)

# Typical ETH OI on Arbitrum in USD
_LONG_OI = 300_000_000.0  # $300 M
_SHORT_OI = 250_000_000.0  # $250 M


# ===========================================================================
# Unit tests -- no network
# ===========================================================================


def test_estimate_price_impact_zero_size():
    """Zero position size must produce zero price impact."""
    impact = estimate_position_price_impact(
        long_open_interest_usd=_LONG_OI,
        short_open_interest_usd=_SHORT_OI,
        size_delta_usd=0.0,
        is_long=True,
        params=_ETH_PARAMS,
    )
    assert impact == 0.0


def test_estimate_price_impact_same_side_negative():
    """Opening a long when longs already dominate worsens imbalance -- negative impact."""
    # Longs already dominate: opening more longs worsens the imbalance
    impact = estimate_position_price_impact(
        long_open_interest_usd=300_000_000.0,
        short_open_interest_usd=250_000_000.0,
        size_delta_usd=10_000.0,
        is_long=True,
        params=_ETH_PARAMS,
    )
    assert impact < 0.0, "Opening a long into a long-skewed market should have negative impact"


def test_estimate_price_impact_balance_improving():
    """Opening a long when shorts dominate reduces imbalance -- positive impact (rebate)."""
    impact = estimate_position_price_impact(
        long_open_interest_usd=250_000_000.0,
        short_open_interest_usd=300_000_000.0,
        size_delta_usd=10_000.0,
        is_long=True,
        params=_ETH_PARAMS,
    )
    assert impact > 0.0, "Opening a long into a short-skewed market should give a rebate"


def test_estimate_price_impact_crossover():
    """A very large long that flips the OI from short-skewed to long-skewed.

    The crossover case splits the impact into two parts:
    positive (reducing the old imbalance) and negative (creating new imbalance).
    The net impact may be positive or negative depending on the magnitude.
    """
    # Start slightly short-skewed
    long_oi = 100_000_000.0
    short_oi = 110_000_000.0
    # Open a $50 M long -- this flips the imbalance to long-skewed
    size_delta = 50_000_000.0

    impact = estimate_position_price_impact(
        long_open_interest_usd=long_oi,
        short_open_interest_usd=short_oi,
        size_delta_usd=size_delta,
        is_long=True,
        params=_ETH_PARAMS,
    )
    # After the trade: long = 150 M, short = 110 M -> imbalance = 40 M (long side)
    # Before trade: imbalance = 10 M (short side)
    # Crossover occurred. Net impact should be negative (creating larger new imbalance).
    assert impact < 0.0, "Large crossover trade should produce negative net impact"


def test_estimate_price_impact_symmetric():
    """Impact magnitude should increase monotonically with position size."""
    impacts = [
        abs(
            estimate_position_price_impact(
                long_open_interest_usd=_LONG_OI,
                short_open_interest_usd=_SHORT_OI,
                size_delta_usd=float(size),
                is_long=True,
                params=_ETH_PARAMS,
            )
        )
        for size in [1_000, 10_000, 100_000, 1_000_000]
    ]
    # Each step should increase the absolute impact
    assert all(impacts[i] < impacts[i + 1] for i in range(len(impacts) - 1)), "Absolute price impact should increase monotonically with position size"


def test_find_max_position_size_basic():
    """Binary search returns a size where impact is within the threshold."""
    max_size = find_max_position_size(
        long_open_interest_usd=_LONG_OI,
        short_open_interest_usd=_SHORT_OI,
        is_long=True,
        max_price_impact_bps=10.0,
        params=_ETH_PARAMS,
        max_oi_available_usd=50_000_000.0,
    )
    assert max_size >= 0.0

    # Verify the returned size stays within threshold
    if max_size > 0:
        impact = estimate_position_price_impact(
            long_open_interest_usd=_LONG_OI,
            short_open_interest_usd=_SHORT_OI,
            size_delta_usd=max_size,
            is_long=True,
            params=_ETH_PARAMS,
        )
        impact_bps = abs(impact) / max_size * 10_000
        assert impact_bps <= 10.0 + 0.1, f"Returned size ${max_size:,.0f} gives {impact_bps:.2f} bps, expected ≤10 bps"


def test_find_max_position_size_tight_threshold():
    """A very tight threshold should return a small (non-zero) size."""
    max_size = find_max_position_size(
        long_open_interest_usd=_LONG_OI,
        short_open_interest_usd=_SHORT_OI,
        is_long=True,
        max_price_impact_bps=0.1,  # 0.1 bps -- very tight
        params=_ETH_PARAMS,
        max_oi_available_usd=10_000_000.0,
    )
    # Should find a small but positive position size
    assert max_size >= 0.0


def test_parse_market_depth_listed():
    """parse_market_depth converts a mock API dict into a correct MarketDepthInfo."""
    PRECISION = 10**30
    mock_market = {
        "name": "ETH/USD [WETH-USDC]",
        "marketToken": "0xAbc",
        "indexToken": "0xDef",
        "longToken": "0xGhi",
        "shortToken": "0xJkl",
        "isListed": True,
        "openInterestLong": str(100_000 * PRECISION),  # $100 000
        "openInterestShort": str(80_000 * PRECISION),  # $80 000
        "availableLiquidityLong": str(500_000 * PRECISION),  # $500 000 cap remaining
        "availableLiquidityShort": str(600_000 * PRECISION),
        "poolAmountLong": "12345678",
        "poolAmountShort": "9876543",
        "fundingRateLong": "0",
        "fundingRateShort": "0",
        "borrowingRateLong": "0",
        "borrowingRateShort": "0",
    }
    info = parse_market_depth(mock_market)

    assert isinstance(info, MarketDepthInfo)
    assert info.market_symbol == "ETH/USD [WETH-USDC]"
    assert info.is_listed is True
    assert info.long_open_interest_usd == pytest.approx(100_000.0)
    assert info.short_open_interest_usd == pytest.approx(80_000.0)
    assert info.available_long_oi_usd == pytest.approx(500_000.0)
    assert info.available_short_oi_usd == pytest.approx(600_000.0)
    assert info.max_long_open_interest_usd == pytest.approx(600_000.0)
    assert info.max_short_open_interest_usd == pytest.approx(680_000.0)
    assert info.long_pool_amount == pytest.approx(12_345_678.0)


def test_parse_market_depth_unlisted():
    """Unlisted markets have is_listed=False."""
    mock_market = {
        "name": "OLD/USD",
        "marketToken": "0x000",
        "indexToken": "0x001",
        "longToken": "0x002",
        "shortToken": "0x003",
        "isListed": False,
        "openInterestLong": "0",
        "openInterestShort": "0",
        "availableLiquidityLong": "0",
        "availableLiquidityShort": "0",
        "poolAmountLong": "0",
        "poolAmountShort": "0",
        "fundingRateLong": "0",
        "fundingRateShort": "0",
        "borrowingRateLong": "0",
        "borrowingRateShort": "0",
    }
    info = parse_market_depth(mock_market)
    assert info.is_listed is False


# ===========================================================================
# Network tests -- REST API
# ===========================================================================


@flaky(max_runs=3, min_passes=1)
def test_get_market_depth_returns_data(api):
    """get_market_depth returns a non-empty list of listed MarketDepthInfo."""
    markets = api.get_market_depth(use_cache=False)

    assert isinstance(markets, list)
    assert len(markets) > 0, "Expected at least one listed market"

    first = markets[0]
    assert isinstance(first, MarketDepthInfo)
    assert first.is_listed is True
    assert len(first.market_token_address) > 0
    assert first.long_open_interest_usd >= 0.0
    assert first.short_open_interest_usd >= 0.0
    assert first.available_long_oi_usd >= 0.0
    assert first.available_short_oi_usd >= 0.0
    assert first.max_long_open_interest_usd >= first.long_open_interest_usd
    assert first.max_short_open_interest_usd >= first.short_open_interest_usd


@flaky(max_runs=3, min_passes=1)
def test_get_market_depth_filter_by_symbol(api):
    """Filtering by symbol returns only matching markets."""
    eth_markets = api.get_market_depth(market_symbol="ETH", use_cache=False)

    assert len(eth_markets) > 0, "Expected at least one ETH market"
    for m in eth_markets:
        assert "eth" in m.market_symbol.lower(), f"Market {m.market_symbol!r} does not contain 'ETH'"


@flaky(max_runs=3, min_passes=1)
def test_get_market_depth_all_listed(api):
    """All returned markets have is_listed=True."""
    markets = api.get_market_depth(use_cache=False)
    for m in markets:
        assert m.is_listed is True, f"Unlisted market leaked through: {m.market_symbol}"


@flaky(max_runs=3, min_passes=1)
def test_get_market_depth_filter_no_match(api):
    """Filtering with a non-existent symbol returns an empty list."""
    markets = api.get_market_depth(market_symbol="NONEXISTENT_TOKEN_XYZ123", use_cache=False)
    assert markets == []


# ===========================================================================
# On-chain test -- DataStore reads
# ===========================================================================


@flaky(max_runs=3, min_passes=1)
def test_fetch_price_impact_params(gmx_config, api):
    """Reads price impact params from the DataStore for the first ETH market."""
    eth_markets = api.get_market_depth(market_symbol="ETH", use_cache=False)
    assert len(eth_markets) > 0, "Need at least one ETH market to test"

    # Use the first available ETH market
    eth_market = eth_markets[0]
    params = fetch_price_impact_params(gmx_config, eth_market.market_token_address)

    assert isinstance(params, PriceImpactParams)
    # All factors must be non-negative integers (positive_factor may be 0 on some markets)
    assert params.positive_factor >= 0
    assert params.negative_factor >= 0
    assert params.positive_exponent >= 0
    assert params.negative_exponent >= 0
    assert params.max_positive_factor >= 0
    assert params.max_negative_factor >= 0
    # For an active market the factors must be set
    assert params.negative_factor > 0, "Active ETH market should have a non-zero negative impact factor"
    assert params.positive_exponent > 0, "Active ETH market should have a non-zero positive exponent"
