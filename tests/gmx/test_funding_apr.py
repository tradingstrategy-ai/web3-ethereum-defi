"""
Tests for GMX Funding APR functionality (alias for GetFundingFee).
"""

from eth_defi.gmx.core.funding_apr import GetFundingFee


def test_initialization_and_basic_functionality(get_funding_fee, gmx_config):
    """Test GetFundingFee initialization and basic functionality."""
    # Test basic initialization
    assert get_funding_fee.config is not None

    # Test initialization with custom filter setting
    funding_fee_custom = GetFundingFee(gmx_config, filter_swap_markets=False)
    assert funding_fee_custom.filter_swap_markets is False

    # Test inheritance from GetData
    assert hasattr(get_funding_fee, "get_data")
    assert callable(get_funding_fee.get_data)

    # Test config dependency
    assert hasattr(get_funding_fee.config, "web3")
    assert hasattr(get_funding_fee.config, "chain")


def test_market_info_and_data_structures(get_funding_fee):
    """Test market info handling and data structure patterns."""
    results = get_funding_fee.get_data()

    assert isinstance(results, dict)
    assert "long" in results
    assert "short" in results
    assert results["parameter"] == "funding_apr"

    # Make sure the values exists
    assert results["long"]["BTC"]
    assert results["short"]["ARB"]

    assert isinstance(results["long"]["BTC"], float)
    assert isinstance(results["short"]["ARB"], float)
