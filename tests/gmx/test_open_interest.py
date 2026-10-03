"""
Tests for GMX Open Interest Data Retrieval Module.
"""

from eth_defi.gmx.config import GMXConfig

from eth_defi.gmx.core.open_interest import GetOpenInterest, OpenInterestInfo


def test_initialization_and_basic_functionality(get_open_interest, gmx_config):
    """Test GetOpenInterest initialization and basic functionality."""
    # Test basic initialization
    assert get_open_interest.config is not None
    assert get_open_interest.log is not None
    assert get_open_interest.filter_swap_markets is True

    # Test initialization with custom filter setting
    open_interest_custom = GetOpenInterest(gmx_config, filter_swap_markets=False)
    assert open_interest_custom.filter_swap_markets is False

    # Test inheritance from GetData
    assert hasattr(get_open_interest, "get_data")
    assert callable(get_open_interest.get_data)

    # Test config dependency
    assert hasattr(get_open_interest.config, "web3")
    assert hasattr(get_open_interest.config, "chain")

    # Test that markets are properly initialized
    assert get_open_interest.markets is not None
    assert hasattr(get_open_interest.markets, "get_available_markets")


def test_open_interest_info_dataclass():
    """Test OpenInterestInfo dataclass structure and initialization."""
    market_address = "0x47904963fc8b2340414262125aF906B738AD9BDF"
    long_token_address = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    short_token_address = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

    open_interest_info = OpenInterestInfo(market_address=market_address, market_symbol="ETH", long_open_interest=1000000.0, short_open_interest=800000.0, total_open_interest=1800000.0, long_token_address=long_token_address, short_token_address=short_token_address)

    # Verify all fields are set correctly
    assert open_interest_info.market_address == market_address
    assert open_interest_info.market_symbol == "ETH"
    assert open_interest_info.long_open_interest == 1000000.0
    assert open_interest_info.short_open_interest == 800000.0
    assert open_interest_info.total_open_interest == 1800000.0
    assert open_interest_info.long_token_address == long_token_address
    assert open_interest_info.short_token_address == short_token_address


def test_live_response_and_filtering(gmx_config: GMXConfig, chain_name: str) -> None:
    """Validate both live filtering modes with one fetch per mode.

    Preserve response schema, numeric values and representative market coverage
    without downloading again to test short-term market-price stability.
    """
    filtered = GetOpenInterest(gmx_config, filter_swap_markets=True).get_data()
    unfiltered = GetOpenInterest(gmx_config, filter_swap_markets=False).get_data()
    expected = {"ETH", "BTC", "ARB"} if chain_name == "arbitrum" else {"AVAX", "ETH", "BTC"}
    for result in (filtered, unfiltered):
        assert result["parameter"] == "open_interest"
        assert result["long"], "A successful provider response must contain markets"
        assert set(result["long"]) == set(result["short"])
        assert expected.intersection(result["long"]), "Missing representative markets"
        for side in ("long", "short"):
            assert all(isinstance(value, float) for value in result[side].values())
            assert all(value >= 0 for value in result[side].values())
            for symbol in expected.intersection(result[side]):
                assert result[side][symbol] > 0, f"Expected active open interest for {symbol}"

    assert set(filtered["long"]).issubset(unfiltered["long"])
    assert not any(symbol.startswith("SWAP") for symbol in filtered["long"])
