"""
Tests for GMX Funding APR functionality (alias for GetFundingFee).
"""

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.funding_fee import GetFundingFee


def test_initialisation_and_basic_functionality(get_funding_fee: GetFundingFee, gmx_config: GMXConfig) -> None:
    """Test GetFundingFee initialisation and basic functionality.

    Check configuration and both explicit filter settings without downloading
    another live response.

    :param get_funding_fee:
        Funding reader constructed from the chain configuration.

    :param gmx_config:
        Chain-specific GMX configuration supplied by the integration fixtures.

    :return:
        None; assertions validate the behaviour.
    """
    # Test basic initialisation
    assert get_funding_fee.config is not None
    assert get_funding_fee.log is not None
    assert get_funding_fee.filter_swap_markets is True

    # Test initialisation with custom filter setting
    funding_fee_custom = GetFundingFee(gmx_config, filter_swap_markets=False)
    assert funding_fee_custom.filter_swap_markets is False

    # Test inheritance from GetData
    assert hasattr(get_funding_fee, "get_data")
    assert callable(get_funding_fee.get_data)

    # Test config dependency
    assert hasattr(get_funding_fee.config, "web3")
    assert hasattr(get_funding_fee.config, "chain")

    # Test that markets are properly initialized
    assert get_funding_fee.markets is not None
    assert hasattr(get_funding_fee.markets, "get_available_markets")


def test_live_response_and_filtering(gmx_config: GMXConfig, chain_name: str) -> None:
    """Validate both live filtering modes with one fetch per mode.

    Preserve response schema, numeric values and representative market coverage
    without downloading again to test short-term market-price stability.

    :param gmx_config:
        Chain-specific GMX configuration supplied by the integration fixtures.

    :param chain_name:
        Configured chain used to select representative markets.

    :return:
        None; assertions validate the behaviour.
    """
    filtered = GetFundingFee(gmx_config, filter_swap_markets=True).get_data()
    unfiltered = GetFundingFee(gmx_config, filter_swap_markets=False).get_data()
    expected = {"ETH", "BTC", "ARB"} if chain_name == "arbitrum" else {"AVAX", "ETH", "BTC"}
    for result in (filtered, unfiltered):
        assert result["parameter"] == "funding_apr"
        assert result["long"], "A successful provider response must contain markets"
        assert set(result["long"]) == set(result["short"])
        assert expected.intersection(result["long"]), "Missing representative markets"
        for side in ("long", "short"):
            assert all(isinstance(value, float) for value in result[side].values())

    assert set(filtered["long"]).issubset(unfiltered["long"])
    assert not any(symbol.startswith("SWAP") for symbol in filtered["long"])
