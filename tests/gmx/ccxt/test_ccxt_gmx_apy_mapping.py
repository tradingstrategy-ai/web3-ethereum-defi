"""Controlled APY period and symbol coverage without repeated live downloads."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.cache import GMXMarketCache
from eth_defi.gmx.ccxt.async_support.exchange import GMX as AsyncGMX
from eth_defi.gmx.ccxt.exchange import GMX


@pytest.mark.parametrize("period", ["1d", "7d", "30d", "90d", "180d", "1y", "total"])
def test_apy_period_and_symbol_mapping(period: str) -> None:
    """Exercise actual period validation and case-insensitive symbol mapping.

    One controlled endpoint response replaces seven live downloads. Keep the
    separate real APY test for the external provider contract.

    :param period:
        Supported APY lookback period under test.

    :return:
        None; assertions validate the behaviour.
    """
    exchange = GMX(options={"disable_market_cache": True})
    exchange.api = GMXAPI(chain="arbitrum")
    symbol = "ETH/USDC:USDC"
    exchange.markets = {symbol: {"symbol": symbol, "info": {"market_token": "0xAbC"}}}
    response = {"markets": {"0xabc": {"apy": 0.25}, "0xunmapped": {"apy": 0.99}}}
    with patch.object(exchange, "load_markets", return_value=exchange.markets), patch.object(exchange, "market", return_value=exchange.markets[symbol]), patch("eth_defi.gmx.api.make_gmx_api_request", return_value=response) as fetch:
        # Bypass the process-global API cache so every period exercises routing.
        with patch("eth_defi.gmx.api._APY_CACHE", {}):
            assert exchange.fetch_apy(period=period) == {symbol: 0.25}
            assert exchange.fetch_apy(symbol=symbol, period=period) == 0.25
        fetch.assert_called_once()
        assert fetch.call_args.kwargs["params"] == {"period": period}
        assert fetch.call_args.kwargs["endpoint"] == "/apy"


def test_apy_missing_symbol_and_provider_failure() -> None:
    """Keep defined missing-data and provider-failure outcomes offline.

    Exercise the documented empty-result outcomes with controlled missing data
    and a deliberately raised provider failure.

    :return:
        None; assertions validate the behaviour.
    """
    exchange = GMX(options={"disable_market_cache": True})
    exchange.api = GMXAPI(chain="arbitrum")
    market = {"info": {"market_token": "0xabc"}}
    with patch.object(exchange, "load_markets", return_value={}), patch.object(exchange, "market", return_value=market), patch.object(exchange.api, "get_apy", return_value={"markets": {}}):
        assert exchange.fetch_apy(symbol="ETH/USDC:USDC") is None
    with patch.object(exchange, "load_markets", return_value={}), patch.object(exchange.api, "get_apy", side_effect=RuntimeError("Provider unavailable")):
        assert exchange.fetch_apy() == {}
        assert exchange.fetch_apy(symbol="ETH/USDC:USDC") is None


def test_apy_invalid_period() -> None:
    """Reject invalid periods before contacting the provider.

    The actual API validator must reject an unsupported period without issuing
    a network request.

    :return:
        None; assertions validate the behaviour.
    """
    with patch("eth_defi.gmx.api.make_gmx_api_request") as fetch:
        with pytest.raises(ValueError, match="Invalid period"):
            GMXAPI(chain="arbitrum").get_apy(period="invalid")
        fetch.assert_not_called()


def test_empty_apy_cache_is_populated_by_both_adapters(tmp_path: Path) -> None:
    """First responses populate empty SQLite caches and prevent repeat downloads.

    Empty persistent stores evaluate as false; that must not disable the initial
    write. Exercise both adapters with controlled responses and real cache files.

    :param tmp_path:
        Isolated directory for persistent cache files.

    :return:
        None; assertions validate the behaviour.
    """
    symbol = "ETH/USDC:USDC"
    markets = {symbol: {"symbol": symbol, "info": {"market_token": "0xabc"}}}
    response = {"markets": {"0xabc": {"apy": 0.25}}}
    expected = {symbol: 0.25}
    cache = GMXMarketCache(tmp_path / "sync.sqlite")
    try:
        assert len(cache) == 0
        exchange = GMX(options={"disable_market_cache": True})
        exchange._market_cache = cache
        exchange.markets = markets
        exchange.api = Mock()
        exchange.api.get_apy.return_value = response
        with patch.object(exchange, "load_markets", return_value=markets):
            assert exchange.fetch_apy() == expected
            assert cache.get_apy("30d") == response["markets"]
            assert exchange.fetch_apy() == expected
        exchange.api.get_apy.assert_called_once()
    finally:
        cache.close()

    cache = GMXMarketCache(tmp_path / "async.sqlite")
    try:
        assert len(cache) == 0
        exchange = AsyncGMX({})
        exchange._market_cache = cache
        exchange.markets = markets
        with patch.object(exchange, "_ensure_session", new_callable=AsyncMock), patch("eth_defi.gmx.ccxt.async_support.exchange.async_make_gmx_api_request", new_callable=AsyncMock, return_value=response) as fetch:
            assert asyncio.run(exchange.fetch_apy()) == expected
            assert cache.get_apy("30d") == response["markets"]
            assert asyncio.run(exchange.fetch_apy()) == expected
            fetch.assert_awaited_once()
    finally:
        cache.close()


@pytest.mark.parametrize("existing_empty_entry", [False, True])
def test_empty_rest_discovery_is_not_cached(tmp_path: Path, existing_empty_entry: bool) -> None:
    """Retry discovery rather than retaining an empty response for an hour.

    Exercise both an initially empty SQLite store and an empty entry written by
    older code. The second discovery call must still reach the provider.

    :param tmp_path:
        Isolated directory for the persistent cache.
    :param existing_empty_entry:
        Seed an empty entry to exercise recovery from an older cache.
    :return:
        None; assertions validate provider calls and cache contents.
    """
    cache = GMXMarketCache(tmp_path / "markets.sqlite")
    try:
        if existing_empty_entry:
            cache.set_markets({}, "rest_api", ttl=3600)
        exchange = GMX(options={"disable_market_cache": True})
        exchange._market_cache = cache
        exchange.api = Mock()
        exchange.api.get_markets_info.return_value = {"markets": []}
        exchange.api.get_tokens.return_value = {"tokens": []}
        with patch.object(exchange, "_filter_datastore_disabled_markets", side_effect=lambda markets: markets):
            assert exchange._load_markets_from_rest_api() == {}
            assert exchange._load_markets_from_rest_api() == {}
        assert exchange.api.get_markets_info.call_count == 2
        if not existing_empty_entry:
            assert cache.get_markets("rest_api") is None
    finally:
        cache.close()
