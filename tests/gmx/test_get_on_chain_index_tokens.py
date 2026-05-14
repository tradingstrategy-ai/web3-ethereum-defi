"""Whitelist-decoupled enumeration of GMX index tokens.

These tests pin the new ``GMX.get_on_chain_index_tokens()`` enumerator —
the decoupling point that lets startup whitelist validation answer the
*structural* question ("is this token a GMX market right now?") instead
of relying on :meth:`Markets.get_available_markets`, whose result can
shrink whenever the oracle REST snapshot is momentarily stale.

The decoupling pin (``test_does_not_apply_oracle_filter``) is the most
important assertion: a market that exists on-chain but is missing from
``OraclePrices.get_recent_prices()`` MUST still appear in the returned
set, otherwise a transient oracle hiccup would silently shrink the
production whitelist again — exactly the failure mode that produced
issue #67.

See ``tradingstrategy-ai/gmx-strategies#67`` deep-dive
(``2026-05-11-gmx-market-cache-permanent-fix.md``).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from web3 import Web3


_ETH_INDEX = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
_BTC_INDEX = "0x47904963fc8b2340414262125aF798B9655E58Cd"
_CHZ_INDEX = "0x5dB4692926C8ceebF6Da0995358Bbc438F3fd80C"
_USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
_ETH_MARKET_ADDR = "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336"
_BTC_MARKET_ADDR = "0x47c031236e19d024b42f8AE6780E44A573170703"
_CHZ_MARKET_ADDR = "0x4fDd333FF9cA409df583f306B6F5a7fFdC9573d1"


def _raw_market(market: str, index: str) -> tuple[str, str, str, str]:
    return (market, index, _USDC, _USDC)


def _build_gmx_stub(raw_markets: list[tuple[str, str, str, str]]):
    """Build a barely-instantiated ``GMX`` whose ``get_on_chain_index_tokens``
    can be called against a mocked ``Markets`` instance."""
    from eth_defi.gmx.ccxt.exchange import GMX
    from eth_defi.gmx.core import markets as markets_mod

    gmx = GMX.__new__(GMX)  # bypass __init__ to avoid RPC
    gmx.config = MagicMock()
    gmx.config.get_chain.return_value = "arbitrum"
    gmx.config.chain = "arbitrum"
    gmx.config.web3 = MagicMock()

    # Patch the Markets._get_available_markets_raw used inside the method.
    def _fake_raw(_self):
        return list(raw_markets)

    markets_mod.Markets._get_available_markets_raw = _fake_raw  # type: ignore[assignment]
    return gmx


@pytest.fixture(autouse=True)
def _clean_cache():
    from eth_defi.gmx.core.markets import Markets

    Markets.invalidate_cache()
    yield
    Markets.invalidate_cache()


def test_method_exists_on_gmx():
    """The new method must be on the ``GMX`` class itself, not a helper."""
    from eth_defi.gmx.ccxt.exchange import GMX

    assert hasattr(GMX, "get_on_chain_index_tokens"), "GMX.get_on_chain_index_tokens() is missing — issue #67 fix not applied"


def test_returns_set_of_checksum_addresses(monkeypatch):
    """The returned set must contain only EIP-55 checksum addresses."""
    raw = [
        _raw_market(_ETH_MARKET_ADDR, _ETH_INDEX),
        _raw_market(_BTC_MARKET_ADDR, _BTC_INDEX),
    ]
    gmx = _build_gmx_stub(raw)

    result = gmx.get_on_chain_index_tokens()

    assert isinstance(result, set)
    assert Web3.to_checksum_address(_ETH_INDEX) in result
    assert Web3.to_checksum_address(_BTC_INDEX) in result
    # Every address must round-trip through to_checksum_address unchanged.
    for addr in result:
        assert addr == Web3.to_checksum_address(addr)


def test_does_not_apply_oracle_filter(monkeypatch):
    """Decoupling pin — markets missing from oracle prices are STILL returned.

    This is the exact failure mode that produced issue #67: the oracle
    snapshot for a newly-listed (or momentarily-stale) token is empty
    for a few seconds while Pyth updates, and the old
    :meth:`Markets.get_available_markets` dropped the market entirely.
    The enumerator must NOT consult ``OraclePrices`` at all.
    """
    from eth_defi.gmx.core import markets as markets_mod

    raw = [
        _raw_market(_ETH_MARKET_ADDR, _ETH_INDEX),
        _raw_market(_CHZ_MARKET_ADDR, _CHZ_INDEX),
    ]
    gmx = _build_gmx_stub(raw)

    # Mock oracle to return empty — the issue #67 scenario.
    with patch.object(markets_mod.OraclePrices, "get_recent_prices", return_value={}):
        result = gmx.get_on_chain_index_tokens()

    assert Web3.to_checksum_address(_CHZ_INDEX) in result, "Oracle-missing token must still appear in the structural enumeration"
    assert Web3.to_checksum_address(_ETH_INDEX) in result


def test_skips_zero_address_index_tokens(monkeypatch):
    """A market with a zero index token (e.g. wstETH special-case) is filtered out.

    The enumerator's job is to return *real* index tokens — the zero address
    is a wstETH-special-case sentinel from the reader contract.  Returning it
    would corrupt whitelist validation by suggesting that ``0x0`` is a valid
    GMX market.
    """
    zero = "0x" + "0" * 40
    raw = [
        _raw_market(_ETH_MARKET_ADDR, _ETH_INDEX),
        _raw_market("0xfeed" + "0" * 36, zero),  # a swap-only market
    ]
    gmx = _build_gmx_stub(raw)

    result = gmx.get_on_chain_index_tokens()
    assert Web3.to_checksum_address(_ETH_INDEX) in result
    assert zero not in result
    assert Web3.to_checksum_address(zero) not in result
