"""
Tests for GetAvailableLiquidity with parametrised chain testing.

This test suite validates the GetAvailableLiquidity class functionality
using efficient multicall batching across different chains.
"""

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.available_liquidity import GetAvailableLiquidity


def test_get_available_liquidity_initialisation(gmx_config: GMXConfig) -> None:
    """Test that GetAvailableLiquidity initialises correctly with chain-specific config.

    Check both explicit filter settings and the configured reader bindings
    without adding another live data download.

    :param gmx_config:
        Chain-specific GMX configuration supplied by the integration fixtures.

    :return:
        None; assertions validate the behaviour.
    """
    get_available_liquidity = GetAvailableLiquidity(gmx_config, filter_swap_markets=True)

    assert get_available_liquidity.config == gmx_config
    assert get_available_liquidity.filter_swap_markets is True
    assert get_available_liquidity.datastore_address is not None
    assert get_available_liquidity.log is not None

    # Test with filter_swap_markets=False
    get_available_liquidity_unfiltered = GetAvailableLiquidity(gmx_config, filter_swap_markets=False)
    assert get_available_liquidity_unfiltered.filter_swap_markets is False


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
    filtered = GetAvailableLiquidity(gmx_config, filter_swap_markets=True).get_data()
    unfiltered = GetAvailableLiquidity(gmx_config, filter_swap_markets=False).get_data()
    expected = {"ETH", "BTC", "ARB"} if chain_name == "arbitrum" else {"AVAX", "ETH", "BTC"}
    for result in (filtered, unfiltered):
        assert result["parameter"] == "available_liquidity"
        assert result["long"], "A successful provider response must contain markets"
        assert set(result["long"]) == set(result["short"])
        assert expected.intersection(result["long"]), "Missing representative markets"
        for side in ("long", "short"):
            assert all(isinstance(value, (int, float)) for value in result[side].values())

    assert set(filtered["long"]).issubset(unfiltered["long"])
    assert not any(symbol.startswith("SWAP") for symbol in filtered["long"])
