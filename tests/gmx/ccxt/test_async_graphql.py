import pytest
from eth_defi.gmx.ccxt.async_support.async_graphql import AsyncGMXSubsquidClient


@pytest.mark.asyncio
async def test_async_get_market_infos():
    """Test fetching market info via async GraphQL"""
    client = AsyncGMXSubsquidClient(chain="arbitrum")

    async with client:
        market_infos = await client.get_market_infos(limit=10)

        assert isinstance(market_infos, list)
        assert len(market_infos) > 0

        # Check structure of first market info
        first = market_infos[0]
        assert "marketTokenAddress" in first
        assert "minCollateralFactor" in first


@pytest.mark.asyncio
async def test_async_calculate_max_leverage():
    """Test leverage calculation (static method)"""
    from eth_defi.gmx.graphql.client import GMXSubsquidClient

    # Should match sync version
    leverage = GMXSubsquidClient.calculate_max_leverage("100000000000000000000000000000")
    assert leverage > 1
