import pytest
import aiohttp
from eth_defi.gmx.ccxt.async_support.async_http import async_make_gmx_api_request


@pytest.mark.asyncio
async def test_async_make_gmx_api_request_success():
    """Test successful API request with retry logic"""
    result = await async_make_gmx_api_request(
        chain="arbitrum",
        endpoint="/prices/tickers",
        timeout=10.0,
    )
    # /prices/tickers returns a list of ticker dicts
    assert isinstance(result, (dict, list))
    assert len(result) > 0


@pytest.mark.asyncio
async def test_async_make_gmx_api_request_with_session():
    """Test request with provided session for connection pooling"""
    async with aiohttp.ClientSession() as session:
        result = await async_make_gmx_api_request(
            chain="arbitrum",
            endpoint="/prices/tickers",
            session=session,
            timeout=10.0,
        )
        # /prices/tickers returns a list of ticker dicts
        assert isinstance(result, (dict, list))
        assert len(result) > 0
