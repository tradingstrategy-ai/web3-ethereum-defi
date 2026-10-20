"""Tests for resolving the GMX token symbol of a CCXT market.

Synthetic markets are exposed with a ``2`` suffix (``HYPE2``), which is a
market id, not a token symbol: ``/prices/candles`` and ``/prices/tickers``
only accept the underlying token (``HYPE``). Using the market id makes every
request for those markets fail with HTTP 400 on all four API hosts.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from eth_defi.gmx.ccxt.token_symbols import (
    resolve_market_token_symbol,
    token_symbols_by_address,
)
from eth_defi.gmx.retry import GMXAPIUnavailable

HYPE_ADDRESS = "0xfDFA0A749dA3bCcee20aE0B4AD50E39B26F58f7C"
XRP_ADDRESS = "0xc14e065b0067dE91534e032868f5Ac6ecf2c6868"
TOKENS_PAYLOAD = {
    "tokens": [
        {"symbol": "HYPE", "address": HYPE_ADDRESS, "decimals": 8},
        {"symbol": "XRP", "address": XRP_ADDRESS, "decimals": 6},
        {"symbol": "NO_ADDRESS", "decimals": 18},
    ],
}


def _market(market_id: str, index_token: str | None) -> dict:
    return {
        "id": market_id,
        "symbol": f"{market_id}/USDC:USDC",
        "info": {"index_token": index_token},
    }


def test_token_symbols_by_address_lowercases_and_skips_incomplete():
    symbols = token_symbols_by_address(TOKENS_PAYLOAD)

    assert symbols[HYPE_ADDRESS.lower()] == "HYPE"
    assert symbols[XRP_ADDRESS.lower()] == "XRP"
    assert len(symbols) == 2


def test_resolve_synthetic_market_uses_index_token():
    symbols = token_symbols_by_address(TOKENS_PAYLOAD)

    assert resolve_market_token_symbol(_market("HYPE2", HYPE_ADDRESS), symbols) == "HYPE"
    assert resolve_market_token_symbol(_market("XRP2", XRP_ADDRESS), symbols) == "XRP"


def test_resolve_plain_market_uses_id():
    symbols = token_symbols_by_address(TOKENS_PAYLOAD)

    assert resolve_market_token_symbol(_market("BTC", HYPE_ADDRESS), symbols) == "HYPE"
    assert resolve_market_token_symbol(_market("BTC", None), symbols) == "BTC"


def test_resolve_unknown_index_token_falls_back_to_id():
    symbols = token_symbols_by_address(TOKENS_PAYLOAD)

    assert resolve_market_token_symbol(_market("HYPE2", "0xdeadbeef"), symbols) == "HYPE2"


class TestSyncResolution:
    def _fake_exchange(self):
        from eth_defi.gmx.ccxt.exchange import GMX

        gmx = GMX.__new__(GMX)
        gmx.api = MagicMock()
        return gmx

    def test_symbol_map_is_fetched_once_and_cached(self):
        gmx = self._fake_exchange()
        gmx.api.get_tokens.return_value = TOKENS_PAYLOAD

        assert gmx._resolve_market_token_symbol(_market("HYPE2", HYPE_ADDRESS)) == "HYPE"
        assert gmx._resolve_market_token_symbol(_market("XRP2", XRP_ADDRESS)) == "XRP"
        gmx.api.get_tokens.assert_called_once()

    def test_api_unavailable_falls_back_without_caching(self):
        gmx = self._fake_exchange()
        gmx.api.get_tokens.side_effect = GMXAPIUnavailable("arbitrum", "/tokens", ["down"])

        assert gmx._resolve_market_token_symbol(_market("HYPE2", HYPE_ADDRESS)) == "HYPE2"
        assert not hasattr(gmx, "_market_token_symbols")

    def test_fetch_ohlcv_requests_the_index_token_symbol(self, monkeypatch):
        from eth_defi.gmx.ccxt import exchange as exchange_module

        gmx = self._fake_exchange()
        gmx.timeframes = {"1h": "1h"}
        gmx.load_markets = lambda: None
        gmx.market = lambda symbol: _market("HYPE2", HYPE_ADDRESS)
        gmx.parse_ohlcvs = lambda *args, **kwargs: []
        gmx.api.get_tokens.return_value = TOKENS_PAYLOAD
        gmx.api.get_candlesticks.return_value = {"candles": []}
        monkeypatch.setattr(exchange_module, "_validate_ohlcv_data_sufficiency", lambda **kwargs: None)

        assert gmx.fetch_ohlcv("HYPE2/USDC:USDC", "1h") == []
        gmx.api.get_candlesticks.assert_called_once_with("HYPE", "1h")


class TestAsyncResolution:
    def _fake_exchange(self):
        from eth_defi.gmx.ccxt.async_support.exchange import GMX

        gmx = GMX.__new__(GMX)
        gmx.chain = "arbitrum"
        gmx.session = object()
        return gmx

    @pytest.mark.asyncio
    async def test_symbol_map_is_fetched_once_and_cached(self, monkeypatch):
        from eth_defi.gmx.ccxt.async_support import exchange as async_module

        gmx = self._fake_exchange()
        request = AsyncMock(return_value=TOKENS_PAYLOAD)
        monkeypatch.setattr(async_module, "async_make_gmx_api_request", request)

        assert await gmx._resolve_market_token_symbol(_market("HYPE2", HYPE_ADDRESS)) == "HYPE"
        assert await gmx._resolve_market_token_symbol(_market("XRP2", XRP_ADDRESS)) == "XRP"
        request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_api_unavailable_falls_back_without_caching(self, monkeypatch):
        from eth_defi.gmx.ccxt.async_support import exchange as async_module

        gmx = self._fake_exchange()
        monkeypatch.setattr(
            async_module,
            "async_make_gmx_api_request",
            AsyncMock(side_effect=GMXAPIUnavailable("arbitrum", "/tokens", ["down"])),
        )

        assert await gmx._resolve_market_token_symbol(_market("HYPE2", HYPE_ADDRESS)) == "HYPE2"
        assert not hasattr(gmx, "_market_token_symbols")

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_requests_the_index_token_symbol(self, monkeypatch):
        from eth_defi.gmx.ccxt.async_support import exchange as async_module

        gmx = self._fake_exchange()
        gmx.timeframes = {"1h": "1h"}
        gmx.market = lambda symbol: _market("HYPE2", HYPE_ADDRESS)

        async def _noop(*args, **kwargs):
            return None

        gmx._ensure_session = _noop
        gmx.load_markets = _noop

        calls: dict = {}

        async def fake_request(**kwargs):
            if kwargs["endpoint"] == "/tokens":
                return TOKENS_PAYLOAD
            calls.update(kwargs)
            return {"candles": []}

        monkeypatch.setattr(async_module, "async_make_gmx_api_request", fake_request)
        monkeypatch.setattr(async_module, "_validate_ohlcv_data_sufficiency", lambda **kwargs: None)

        assert await gmx.fetch_ohlcv("HYPE2/USDC:USDC", "1h") == []
        assert calls["endpoint"] == "/prices/candles"
        assert calls["params"]["tokenSymbol"] == "HYPE"
