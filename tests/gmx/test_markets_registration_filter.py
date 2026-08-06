"""The REST market list must be intersected with on-chain registered markets.

GMX's REST ``/markets`` endpoint can advertise markets that do not exist on the
chain it claims to describe. On Arbitrum Sepolia it returns 16 markets while the
DataStore holds 10. Ordering against one of the six phantom entries reverts with
GMX's ``EmptyMarket`` custom error — and it does so *after* a Lagoon vault guard
has already validated and approved the call, so the failure surfaces as a burnt
transaction rather than a preflight rejection.

``IS_MARKET_DISABLED`` cannot catch these: the flag was never written for a market
that was never registered, so it reads ``false`` — "enabled". Only membership of
the on-chain registered set distinguishes a phantom from a live market.

These tests exercise the filter's drop path directly. The mocks in
``test_markets_cache_ttl.py`` supply a ``MagicMock`` web3, under which
``_fetch_onchain_market_addresses()`` always fails open, so that suite only ever
covers the skip-filtering branch.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

#: Real Arbitrum addresses, but nothing here touches an RPC.
_ETH_INDEX = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
_USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
_REAL_MARKET = "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336"

#: Advertised by REST but absent from the DataStore — the phantom.
_PHANTOM_MARKET = "0x482Df3D320C964808579b585a8AC7Dd5D144eFaF"


@pytest.fixture(autouse=True)
def _clean_cache():
    """Reset the class-level markets cache around every test."""
    from eth_defi.gmx.core.markets import Markets

    Markets.invalidate_cache()
    yield
    Markets.invalidate_cache()


def _rest_entry(market_address: str) -> dict:
    return {
        "market_address": market_address,
        "index_token_address": _ETH_INDEX,
        "long_token_address": _USDC,
        "short_token_address": _USDC,
        "is_listed": True,
    }


def _install_mocks(monkeypatch, registered: set[str] | None) -> None:
    """Patch every network surface ``_process_markets`` touches.

    :param registered: Value ``_fetch_onchain_market_addresses`` should return.
        ``None`` simulates the lookup failing, which must disable filtering.
    """
    from eth_defi.gmx.core import markets as markets_mod

    rest = [_rest_entry(_REAL_MARKET), _rest_entry(_PHANTOM_MARKET)]
    token_meta: dict[str, dict[str, Any]] = {addr: {"symbol": "TKN", "decimals": 18} for addr in (_ETH_INDEX, _USDC)}

    monkeypatch.setattr(markets_mod.Markets, "_fetch_markets_from_rest", lambda self: list(rest))
    monkeypatch.setattr(markets_mod.Markets, "_get_token_metadata_dict", lambda self: dict(token_meta))
    monkeypatch.setattr(markets_mod.Markets, "_check_markets_disabled_onchain", lambda self, addrs: dict.fromkeys(addrs, False))
    monkeypatch.setattr(markets_mod.Markets, "_fetch_onchain_market_addresses", lambda self: registered)


def _build_markets(monkeypatch, registered: set[str] | None):
    from eth_defi.gmx.core.markets import Markets

    _install_mocks(monkeypatch, registered)
    config = MagicMock()
    config.chain = "arbitrum"
    config.web3 = MagicMock()
    return Markets(config).get_available_markets()


def test_phantom_market_is_dropped(monkeypatch):
    """A REST market absent from the DataStore must not reach the market map.

    This is the regression: ordering against it reverts with ``EmptyMarket``
    after the guard has already approved the transaction.
    """
    markets = _build_markets(monkeypatch, {_REAL_MARKET})

    assert _REAL_MARKET in markets
    assert _PHANTOM_MARKET not in markets


def test_registered_markets_are_kept(monkeypatch):
    """Markets present on-chain must survive the filter untouched."""
    markets = _build_markets(monkeypatch, {_REAL_MARKET, _PHANTOM_MARKET})

    assert _REAL_MARKET in markets
    assert _PHANTOM_MARKET in markets


def test_filter_fails_open_when_lookup_fails(monkeypatch):
    """An RPC failure must disable filtering, never empty the market list.

    Failing closed here would take out trading on every chain the moment the
    DataStore read had a bad minute, which is far worse than the phantom
    markets the filter exists to remove.
    """
    markets = _build_markets(monkeypatch, None)

    assert _REAL_MARKET in markets
    assert _PHANTOM_MARKET in markets


def test_lookup_failure_is_swallowed(monkeypatch):
    """``_fetch_onchain_market_addresses`` returns ``None`` rather than raising."""
    from eth_defi.gmx.core import markets as markets_mod

    def _boom(web3, chain):
        raise ConnectionError("RPC down")

    monkeypatch.setattr(markets_mod, "get_reader_contract", _boom)

    config = MagicMock()
    config.chain = "arbitrum"
    config.web3 = MagicMock()

    assert markets_mod.Markets(config)._fetch_onchain_market_addresses() is None
