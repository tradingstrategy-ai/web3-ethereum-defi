"""Collateral-aware market resolution on the OPEN path (issue #1178, B2).

``_resolve_market_info``'s default branch is the ONLY market-selection logic
that executes on the live ccxt order path — it feeds ``market_key`` straight
into ``OrderArgumentParser``, bypassing the parser's own USDC-preference
disambiguation (``_handle_missing_market_key``, which never runs because
``market_key`` arrives pre-injected). Pre-B2 it blindly returned
``self.markets[symbol]["info"]``, so a poisoned or synthetic-only mapping
handed ``create_order()`` a pool that rejects USDC (the incident).

B2 applies the same selection strategy ``_handle_missing_market_key`` already
documents: keep the mapped pool if it accepts the order's collateral; else
scan sibling pools (same index token) and pick one that accepts it, defaulting
to USDC (deepest liquidity) when the caller names no collateral. Selection
failures (RPC/scan errors, no candidate) fall back to the mapped pool — the
open never crashes here; B3 fails loudly downstream if the pool is unusable.

Offline: ``fetch_pools_for_symbol`` / ``self.markets`` are stubbed, no RPC.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

import eth_defi.gmx.ccxt.exchange as exchange_module
from eth_defi.gmx.ccxt.exchange import GMX

_BTC_INDEX = "0x47904963fc8b2340414262125aF798B9655E58Cd"
_USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
_WBTC = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
_TBTC = "0x6c84a8f1c29108F47a79964b5Fe888D4f4D0dE40"
_REAL_BTC_MARKET = "0x47c031236e19d024b42f8AE6780E44A573170703"
_SYNTH_BTC_MARKET = "0xd62068697bCc92AF253225676D618B0C9f17C663"


@pytest.fixture(autouse=True)
def _stub_token_address_resolution(monkeypatch):
    """Keep collateral-resolution tests independent of the live GMX token API."""
    addresses = {
        "USDC": _USDC,
        "BTC": _WBTC,
        "TBTC": _TBTC,
    }
    monkeypatch.setattr(
        exchange_module,
        "get_token_address_normalized",
        lambda _chain, symbol: addresses.get(symbol.upper()),
    )


def _make_exchange() -> GMX:
    """A GMX instance with only the attributes ``_resolve_market_info`` touches."""
    return GMX.__new__(GMX)


def _synthetic_info() -> dict:
    """info dict for the poisoned/synthetic pool (rejects USDC)."""
    return {
        "market_token": _SYNTH_BTC_MARKET,
        "index_token": _BTC_INDEX,
        "long_token": _TBTC,
        "short_token": _TBTC,
    }


def _usdc_info() -> dict:
    return {
        "market_token": _REAL_BTC_MARKET,
        "index_token": _BTC_INDEX,
        "long_token": _WBTC,
        "short_token": _USDC,
    }


def _pools_both() -> list[dict]:
    """fetch_pools_for_symbol output: synthetic listed FIRST, then real USDC pool."""
    return [
        {
            "market_address": _SYNTH_BTC_MARKET,
            "index_token": _BTC_INDEX,
            "long_token": _TBTC,
            "long_token_symbol": "tBTC",
            "short_token": _TBTC,
            "short_token_symbol": "tBTC",
        },
        {
            "market_address": _REAL_BTC_MARKET,
            "index_token": _BTC_INDEX,
            "long_token": _WBTC,
            "long_token_symbol": "WBTC",
            "short_token": _USDC,
            "short_token_symbol": "USDC",
        },
    ]


def _install(gmx: GMX, mapped_info: dict, pools: list[dict]) -> None:
    gmx.markets = {"BTC/USDC:USDC": {"info": mapped_info}}
    gmx._normalize_symbol = lambda s: "BTC/USDC:USDC"
    gmx.fetch_pools_for_symbol = lambda s: list(pools)


def test_default_overrides_synthetic_mapping_to_usdc_pool(caplog):
    """No collateral named → default USDC. Mapped synthetic pool → override + WARN."""
    gmx = _make_exchange()
    _install(gmx, _synthetic_info(), _pools_both())

    with caplog.at_level(logging.WARNING):
        info = gmx._resolve_market_info("BTC/USDC:USDC", {})

    assert info["market_token"] == _REAL_BTC_MARKET
    assert any("overriding" in r.message.lower() for r in caplog.records), [r.message for r in caplog.records]


def test_mapped_usdc_pool_is_kept_no_override(caplog):
    """Mapped pool already accepts USDC → keep it, no override warning."""
    gmx = _make_exchange()
    _install(gmx, _usdc_info(), _pools_both())

    with caplog.at_level(logging.WARNING):
        info = gmx._resolve_market_info("BTC/USDC:USDC", {})

    assert info["market_token"] == _REAL_BTC_MARKET
    assert not any("overriding" in r.message.lower() for r in caplog.records)


def test_case_insensitive_collateral_match_avoids_spurious_override():
    """Lowercase-stored tokens (REST/GraphQL shape) still recognised as accepting.

    A blind equality check would miss the match and wrongly override a good pool.
    """
    gmx = _make_exchange()
    lower_pools = [
        {
            "market_address": _REAL_BTC_MARKET.lower(),
            "index_token": _BTC_INDEX.lower(),
            "long_token": _WBTC.lower(),
            "long_token_symbol": "usdc".upper() and "WBTC",
            "short_token": _USDC.lower(),
            "short_token_symbol": "usdc",  # lowercase symbol
        }
    ]
    lower_info = {
        "market_token": _REAL_BTC_MARKET.lower(),
        "index_token": _BTC_INDEX.lower(),
        "long_token": _WBTC.lower(),
        "short_token": _USDC.lower(),
    }
    _install(gmx, lower_info, lower_pools)

    info = gmx._resolve_market_info("BTC/USDC:USDC", {})
    assert (info["market_token"] or "").lower() == _REAL_BTC_MARKET.lower()


def test_empty_mapping_selects_usdc_pool():
    """Symbol not in self.markets (empty mapped info) → still picks the USDC pool."""
    gmx = _make_exchange()
    _install(gmx, {}, _pools_both())

    info = gmx._resolve_market_info("BTC/USDC:USDC", {})
    assert info["market_token"] == _REAL_BTC_MARKET


def test_symbol_fallback_excludes_longer_unrelated_symbols(monkeypatch):
    """An absent ETH mapping must not include an unrelated ETH-prefixed market."""
    gmx = _make_exchange()
    gmx.markets = {}
    gmx._normalize_symbol = lambda _symbol: "ETH/USDC:USDC"
    gmx.config = object()

    eth2_address = "0x0000000000000000000000000000000000000002"
    ethfi_address = "0x0000000000000000000000000000000000000003"
    catalogue = {
        eth2_address: {
            "market_symbol": "ETH2",
            "index_token_address": "0x0000000000000000000000000000000000000012",
            "long_token_address": "0x0000000000000000000000000000000000000022",
            "long_token_metadata": {"symbol": "WETH"},
            "short_token_address": "0x0000000000000000000000000000000000000022",
            "short_token_metadata": {"symbol": "WETH"},
        },
        ethfi_address: {
            "market_symbol": "ETHFI",
            "index_token_address": "0x0000000000000000000000000000000000000013",
            "long_token_address": "0x0000000000000000000000000000000000000023",
            "long_token_metadata": {"symbol": "ETHFI"},
            "short_token_address": _USDC,
            "short_token_metadata": {"symbol": "USDC"},
        },
    }
    markets = MagicMock()
    markets.get_available_markets.return_value = catalogue
    monkeypatch.setattr(exchange_module, "Markets", MagicMock(return_value=markets))

    pools = gmx.fetch_pools_for_symbol("ETH/USDC:USDC")

    assert [pool["market_address"] for pool in pools] == [eth2_address]


def test_explicit_collateral_symbol_selects_matching_pool():
    """Explicit non-USDC collateral wins: tBTC order keeps the synthetic pool."""
    gmx = _make_exchange()
    _install(gmx, _usdc_info(), _pools_both())  # mapped USDC, but user wants tBTC

    info = gmx._resolve_market_info("BTC/USDC:USDC", {"collateral_symbol": "tBTC"})
    assert info["market_token"] == _SYNTH_BTC_MARKET


def test_btc_collateral_resolves_by_address_not_literal_symbol():
    """BTC collateral must match the WBTC.b pool by token address.

    Symbol-only matching misses the live shape where the pool advertises
    ``WBTC.b`` while the caller requests ``BTC``.
    """
    gmx = _make_exchange()
    _install(
        gmx,
        _synthetic_info(),
        [
            {
                "market_address": _SYNTH_BTC_MARKET,
                "index_token": _BTC_INDEX,
                "long_token": _TBTC,
                "long_token_symbol": "tBTC",
                "short_token": _TBTC,
                "short_token_symbol": "tBTC",
            },
            {
                "market_address": _REAL_BTC_MARKET,
                "index_token": _BTC_INDEX,
                "long_token": _WBTC,
                "long_token_symbol": "WBTC.b",
                "short_token": _USDC,
                "short_token_symbol": "USDC",
            },
        ],
    )

    info = gmx._resolve_market_info("BTC/USDC:USDC", {"collateral_symbol": "BTC"})
    assert info["market_token"] == _REAL_BTC_MARKET


def test_symbol_fallback_still_works_when_address_resolution_returns_none(monkeypatch):
    """Address resolution failure must fall back to the existing symbol match."""
    import eth_defi.gmx.ccxt.exchange as exch_mod

    gmx = _make_exchange()
    _install(gmx, _synthetic_info(), _pools_both())
    monkeypatch.setattr(exch_mod, "get_token_address_normalized", lambda chain, symbol: None)

    info = gmx._resolve_market_info("BTC/USDC:USDC", {"collateral_symbol": "tBTC"})
    assert info["market_token"] == _SYNTH_BTC_MARKET


def test_no_accepting_pool_returns_mapped_info(caplog):
    """No sibling accepts USDC → return mapped info unchanged (B3 fails it later)."""
    gmx = _make_exchange()
    only_synth = [_pools_both()[0]]  # tBTC-tBTC only
    _install(gmx, _synthetic_info(), only_synth)

    with caplog.at_level(logging.WARNING):
        info = gmx._resolve_market_info("BTC/USDC:USDC", {})

    assert info["market_token"] == _SYNTH_BTC_MARKET


def test_scan_failure_falls_back_to_mapped_info():
    """fetch_pools_for_symbol raising must not crash the open — fall back."""
    gmx = _make_exchange()
    gmx.markets = {"BTC/USDC:USDC": {"info": _usdc_info()}}
    gmx._normalize_symbol = lambda s: "BTC/USDC:USDC"

    def _boom(_s):
        raise RuntimeError("RPC down")

    gmx.fetch_pools_for_symbol = _boom

    info = gmx._resolve_market_info("BTC/USDC:USDC", {})
    assert info["market_token"] == _REAL_BTC_MARKET  # mapped info, no crash


def test_reduce_only_close_bypasses_collateral_scan_entirely(caplog):
    """reduceOnly (close) orders must NEVER run B2's collateral-aware scan.

    Adversarial-review finding: closes are resolved authoritatively downstream
    from the on-chain position (_execute_close_with_position), independent of
    whatever _resolve_market_info picks here. Running the scan on closes is
    both wasted RPC-backed work and a residual exposure — if the authoritative
    on-chain 'market' field is ever falsy, code falls back to THIS value,
    which was computed with zero knowledge of which position is being closed.
    Bypass the scan entirely for reduceOnly; return the mapped info unchanged,
    exactly like the pre-B2 code.
    """
    gmx = _make_exchange()
    scanned = {"called": False}

    def _fetch(_s):
        scanned["called"] = True
        return _pools_both()

    gmx.markets = {"BTC/USDC:USDC": {"info": _synthetic_info()}}
    gmx._normalize_symbol = lambda s: "BTC/USDC:USDC"
    gmx.fetch_pools_for_symbol = _fetch

    with caplog.at_level(logging.WARNING):
        info = gmx._resolve_market_info("BTC/USDC:USDC", {"reduceOnly": True})

    assert info["market_token"] == _SYNTH_BTC_MARKET  # mapped info, unchanged
    assert scanned["called"] is False, "reduceOnly must skip the collateral scan"
    assert not caplog.records


def test_sibling_tie_break_is_deterministic_regardless_of_scan_order():
    """Multiple sibling pools accepting the collateral must pick the SAME one
    regardless of ``fetch_pools_for_symbol``'s return order.

    Adversarial-review finding: ``accepting[0]`` depended on dict iteration
    order from the RPC catalogue — not a correctness signal. Two orderings of
    the same two USDC-accepting pools must resolve to the identical pool.
    """
    gmx = _make_exchange()
    pool_a = {
        "market_address": "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "index_token": _BTC_INDEX,
        "long_token": _WBTC,
        "long_token_symbol": "WBTC",
        "short_token": _USDC,
        "short_token_symbol": "USDC",
    }
    pool_b = {
        "market_address": "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        "index_token": _BTC_INDEX,
        "long_token": _WBTC,
        "long_token_symbol": "WBTC",
        "short_token": _USDC,
        "short_token_symbol": "USDC",
    }
    mapped = _synthetic_info()  # mapped pool rejects USDC -> must override

    gmx.markets = {"BTC/USDC:USDC": {"info": mapped}}
    gmx._normalize_symbol = lambda s: "BTC/USDC:USDC"

    gmx.fetch_pools_for_symbol = lambda s: [pool_a, pool_b]
    info_ab = gmx._resolve_market_info("BTC/USDC:USDC", {})

    gmx.fetch_pools_for_symbol = lambda s: [pool_b, pool_a]
    info_ba = gmx._resolve_market_info("BTC/USDC:USDC", {})

    assert info_ab["market_token"] == info_ba["market_token"], (
        info_ab["market_token"],
        info_ba["market_token"],
    )


def test_explicit_market_address_bypasses_collateral_scan():
    """An explicit ``market_address`` param must win — B2 never overrides it."""
    gmx = _make_exchange()
    called = {"scan": False}

    class _M:
        def __init__(self, *a, **k):
            pass

        def get_available_markets(self):
            return {
                _SYNTH_BTC_MARKET: {
                    "gmx_market_address": _SYNTH_BTC_MARKET,
                    "index_token_address": _BTC_INDEX,
                    "long_token_address": _TBTC,
                    "short_token_address": _TBTC,
                }
            }

    import eth_defi.gmx.ccxt.exchange as exch_mod

    orig = exch_mod.Markets
    exch_mod.Markets = _M
    try:
        gmx.config = object()
        gmx.fetch_pools_for_symbol = lambda s: called.__setitem__("scan", True) or []
        info = gmx._resolve_market_info("BTC/USDC:USDC", {"market_address": _SYNTH_BTC_MARKET})
    finally:
        exch_mod.Markets = orig

    assert info["market_token"] == _SYNTH_BTC_MARKET
    assert called["scan"] is False
