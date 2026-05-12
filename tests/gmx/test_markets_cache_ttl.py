"""Bounded markets cache: TTL, invalidation, partial-build protection.

These tests pin the redesign of the class-level GMX markets cache
(``eth_defi.gmx.core.markets._CLASS_MARKETS_CACHE``):

* The cache must expire after a bounded TTL so a recently-listed token
  becomes visible within a single 1-hour candle without any process
  restart.
* The cache must support explicit ``Markets.invalidate_cache(chain)``
  invalidation for caller-driven force-refresh on lookup miss.
* A partial build (``len(processed_markets) < on_chain_count``) must
  never permanently shadow a previously-complete cache entry — partial
  results are either stored only when no better entry exists, and never
  served from the TTL fast path.

See ``tradingstrategy-ai/gmx-strategies#67`` deep-dive
(``2026-05-11-gmx-market-cache-permanent-fix.md``).
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from eth_defi.gmx.core.markets import _normalize_rest_market  # noqa: PLC2701

# Token addresses used as fixtures throughout the suite.  These are real
# Arbitrum addresses but the tests do not touch any RPC — every test
# mocks the raw-fetch and on-chain calls so the suite is fully offline.
_BTC_INDEX = "0x47904963fc8b2340414262125aF798B9655E58Cd"
_ETH_INDEX = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
_USDC_LONG = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
_USDC_SHORT = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
_BTC_MARKET_ADDR = "0x47c031236e19d024b42f8AE6780E44A573170703"
_ETH_MARKET_ADDR = "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336"


def _build_raw_market_tuple(
    market_address: str,
    index_token: str,
    long_token: str = _USDC_LONG,
    short_token: str = _USDC_SHORT,
) -> dict:
    """Return a dict shaped like one entry from ``_fetch_markets_from_rest``."""
    return {
        "market_address": market_address,
        "index_token_address": index_token,
        "long_token_address": long_token,
        "short_token_address": short_token,
        "is_listed": True,
    }


def _build_token_metadata(addresses: list[str]) -> dict[str, dict[str, Any]]:
    """Return a token-metadata dict covering the supplied addresses."""
    return {addr: {"symbol": "TKN", "decimals": 18} for addr in addresses}


def _build_config(chain: str = "arbitrum") -> MagicMock:
    """Build a minimal mock that mimics :class:`GMXConfig` for cache tests."""
    config = MagicMock()
    config.chain = chain
    config.web3 = MagicMock()
    return config


@pytest.fixture(autouse=True)
def _clean_cache():
    """Reset the class-level cache around every test."""
    from eth_defi.gmx.core.markets import Markets

    Markets.invalidate_cache()
    yield
    Markets.invalidate_cache()


def _install_mocks(
    monkeypatch,
    raw_markets: list[dict],
    token_metadata: dict[str, dict[str, Any]] | None = None,
    disabled_addresses: set[str] | None = None,
    on_chain_count: int | None = None,
):
    """Patch the network surfaces ``_process_markets`` depends on.

    :param monkeypatch: pytest monkeypatch fixture.
    :param raw_markets: List of normalized market dicts (from ``_build_raw_market_tuple``).
    :param token_metadata: Optional token-metadata mapping; built automatically from raw_markets if omitted.
    :param disabled_addresses: Set of market addresses that should be treated as disabled (unused — kept for
        API compatibility; disabled filtering is now REST-based and handled upstream of ``_process_markets``).
    :param on_chain_count: If provided, the REST mock returns exactly this many markets (to simulate a
        partial build where ``on_chain_count > len(raw_markets)``); otherwise defaults to ``len(raw_markets)``.
    """
    from eth_defi.gmx.core import markets as markets_mod

    # Build token metadata from all token addresses found in the market dicts.
    token_meta = token_metadata or _build_token_metadata(
        [m["index_token_address"] for m in raw_markets]
        + [m["long_token_address"] for m in raw_markets]
        + [m["short_token_address"] for m in raw_markets]
    )

    # When on_chain_count > len(raw_markets), pad the REST return value with
    # extra dummy entries so partial-build detection fires correctly.
    rest_list = list(raw_markets)
    if on_chain_count is not None and on_chain_count > len(raw_markets):
        # Add placeholder entries with unique addresses and zero-token index so
        # they are skipped during the metadata build but still count toward
        # rest_markets_count for partial-build detection.
        for i in range(on_chain_count - len(raw_markets)):
            rest_list.append({
                "market_address": f"0xDEAD{'0' * 36}{i:04d}",
                "index_token_address": f"0xBEEF{'0' * 36}{i:04d}",
                "long_token_address": _USDC_LONG,
                "short_token_address": _USDC_SHORT,
                "is_listed": True,
            })

    rest_call = MagicMock(return_value=rest_list)
    monkeypatch.setattr(markets_mod.Markets, "_fetch_markets_from_rest", rest_call)
    monkeypatch.setattr(markets_mod.Markets, "_get_token_metadata_dict", lambda self: dict(token_meta))

    return {"raw_call": rest_call}


def test_cache_entry_under_ttl_returns_cached(monkeypatch):
    """Within TTL, ``_process_markets`` must NOT rebuild — RPC calls happen once only."""
    from eth_defi.gmx.core.markets import Markets

    raw = [_build_raw_market_tuple(_ETH_MARKET_ADDR, _ETH_INDEX)]
    mocks = _install_mocks(monkeypatch, raw)

    config = _build_config()
    markets = Markets(config)

    first = markets._process_markets()
    second = markets._process_markets()

    assert first is second or first == second
    # Raw fetch should happen exactly once even though we called twice.
    assert mocks["raw_call"].call_count == 1


def test_cache_entry_over_ttl_refetches(monkeypatch):
    """After TTL elapses, next call rebuilds."""
    from eth_defi.gmx.core import markets as markets_mod
    from eth_defi.gmx.core.markets import Markets

    raw = [_build_raw_market_tuple(_ETH_MARKET_ADDR, _ETH_INDEX)]
    mocks = _install_mocks(monkeypatch, raw)

    config = _build_config()
    markets = Markets(config)

    # First call seeds the cache.
    markets._process_markets()
    assert mocks["raw_call"].call_count == 1

    # Reach into the cache and rewind ``fetched_at_ms`` past the TTL.
    entry = markets_mod._CLASS_MARKETS_CACHE["arbitrum"]
    entry.fetched_at_ms = entry.fetched_at_ms - markets_mod._CLASS_MARKETS_CACHE_TTL_MS - 1

    markets._process_markets()
    assert mocks["raw_call"].call_count == 2, "Stale entry must trigger a rebuild"


def test_invalidate_cache_clears_chain_entry():
    """Per-chain invalidation removes only that chain's entry."""
    from eth_defi.gmx.core.markets import (
        _CLASS_MARKETS_CACHE,
        Markets,
        _MarketsCacheEntry,
    )

    _CLASS_MARKETS_CACHE["arbitrum"] = _MarketsCacheEntry(
        markets={"0xabc": {"index_token_address": "0x123"}},
        fetched_at_ms=int(time.time() * 1000),
        partial=False,
    )
    _CLASS_MARKETS_CACHE["avalanche"] = _MarketsCacheEntry(
        markets={"0xdef": {"index_token_address": "0x456"}},
        fetched_at_ms=int(time.time() * 1000),
        partial=False,
    )

    Markets.invalidate_cache("arbitrum")
    assert "arbitrum" not in _CLASS_MARKETS_CACHE
    assert "avalanche" in _CLASS_MARKETS_CACHE


def test_invalidate_cache_clears_all_when_chain_none():
    """``invalidate_cache()`` with no argument wipes every chain."""
    from eth_defi.gmx.core.markets import (
        _CLASS_MARKETS_CACHE,
        Markets,
        _MarketsCacheEntry,
    )

    _CLASS_MARKETS_CACHE["arbitrum"] = _MarketsCacheEntry(
        markets={"0xabc": {}}, fetched_at_ms=0, partial=False
    )
    _CLASS_MARKETS_CACHE["avalanche"] = _MarketsCacheEntry(
        markets={"0xdef": {}}, fetched_at_ms=0, partial=False
    )
    Markets.invalidate_cache()
    assert _CLASS_MARKETS_CACHE == {}


def test_invalidate_cache_unknown_chain_is_noop():
    """Calling invalidate for a chain that was never cached must not raise."""
    from eth_defi.gmx.core.markets import Markets

    # Should be a no-op rather than KeyError.
    Markets.invalidate_cache("base_sepolia")


def test_partial_build_marked_partial(monkeypatch):
    """When processed_count < on_chain_count, the entry has partial=True."""
    from eth_defi.gmx.core import markets as markets_mod
    from eth_defi.gmx.core.markets import Markets

    raw = [_build_raw_market_tuple(_ETH_MARKET_ADDR, _ETH_INDEX)]
    # Tell the partial-detection helper there are *2* on-chain markets but the
    # raw fetch only returned 1 — this simulates a transient gap (e.g. a
    # mid-flight RPC error inside _process_markets that drops a market).
    _install_mocks(monkeypatch, raw, on_chain_count=2)

    config = _build_config()
    markets = Markets(config)
    result = markets._process_markets()

    entry = markets_mod._CLASS_MARKETS_CACHE["arbitrum"]
    assert entry.partial is True
    assert result  # The (partial) result is still returned for this call.


def test_partial_build_never_returned_from_ttl_fast_path(monkeypatch):
    """A partial entry forces a rebuild on every subsequent call."""
    from eth_defi.gmx.core import markets as markets_mod
    from eth_defi.gmx.core.markets import Markets

    raw = [_build_raw_market_tuple(_ETH_MARKET_ADDR, _ETH_INDEX)]
    mocks = _install_mocks(monkeypatch, raw, on_chain_count=2)

    config = _build_config()
    markets = Markets(config)

    markets._process_markets()
    assert mocks["raw_call"].call_count == 1

    # Even within TTL, the partial entry must be re-evaluated.
    markets._process_markets()
    assert mocks["raw_call"].call_count == 2

    # And confirm the entry on disk is still partial.
    entry = markets_mod._CLASS_MARKETS_CACHE["arbitrum"]
    assert entry.partial is True


def test_partial_build_does_not_overwrite_complete_prior(monkeypatch):
    """A complete prior entry beats a partial new build (no oracle-race poisoning)."""
    from eth_defi.gmx.core import markets as markets_mod
    from eth_defi.gmx.core.markets import Markets, _MarketsCacheEntry

    # Seed cache with a *complete* 2-market entry from a prior call.
    complete_markets = {
        _ETH_MARKET_ADDR: {"index_token_address": _ETH_INDEX, "market_symbol": "ETH"},
        _BTC_MARKET_ADDR: {"index_token_address": _BTC_INDEX, "market_symbol": "BTC"},
    }
    markets_mod._CLASS_MARKETS_CACHE["arbitrum"] = _MarketsCacheEntry(
        markets=complete_markets,
        fetched_at_ms=int(time.time() * 1000)
        - markets_mod._CLASS_MARKETS_CACHE_TTL_MS
        - 1,  # past TTL → would normally rebuild
        partial=False,
    )

    # The rebuild only sees ONE market but on-chain still reports 2 — partial.
    raw = [_build_raw_market_tuple(_ETH_MARKET_ADDR, _ETH_INDEX)]
    _install_mocks(monkeypatch, raw, on_chain_count=2)

    config = _build_config()
    markets = Markets(config)
    result = markets._process_markets()

    # The complete prior cache MUST be preserved when the new build is partial.
    assert _BTC_MARKET_ADDR in result, "Complete prior entry must shadow a partial rebuild"
    assert _ETH_MARKET_ADDR in result
    # And the stored entry should still be the original complete one, not partial.
    entry = markets_mod._CLASS_MARKETS_CACHE["arbitrum"]
    assert entry.partial is False
    assert _BTC_MARKET_ADDR in entry.markets


def test_disabled_market_filtered_via_onchain_check(monkeypatch):
    """Disabled filtering is now done upstream by the REST /markets endpoint (isListed:false).

    _process_markets no longer calls _check_markets_disabled_onchain — all markets
    returned by _fetch_markets_from_rest are considered listed and processed.
    This test verifies that all REST-returned markets are present in the result
    (the ``disabled_addresses`` arg is now a no-op in _install_mocks).
    """
    from eth_defi.gmx.core.markets import Markets

    raw = [
        _build_raw_market_tuple(_ETH_MARKET_ADDR, _ETH_INDEX),
        _build_raw_market_tuple(_BTC_MARKET_ADDR, _BTC_INDEX),
    ]
    _install_mocks(
        monkeypatch,
        raw,
        disabled_addresses={_BTC_MARKET_ADDR},  # no longer has any effect
        on_chain_count=2,
    )

    config = _build_config()
    markets = Markets(config)
    result = markets._process_markets()

    # Both markets are present — disabled filtering happens in _fetch_markets_from_rest,
    # not inside _process_markets.
    assert _ETH_MARKET_ADDR in result
    assert _BTC_MARKET_ADDR in result, "REST-listed markets must all be included"


def test_oracle_snapshot_does_not_exclude_markets(monkeypatch):
    """Markets missing from the oracle snapshot must STILL be included.

    This is the structural fix for issue #67 — the previous code skipped any
    market whose ``index_token_address`` was absent from the oracle REST
    snapshot, which crashed live bots whenever a newly-listed token (or one
    whose Pyth feed was momentarily unavailable) was on the whitelist.
    """
    from eth_defi.gmx.core import markets as markets_mod
    from eth_defi.gmx.core.markets import Markets

    raw = [_build_raw_market_tuple(_ETH_MARKET_ADDR, _ETH_INDEX)]
    _install_mocks(monkeypatch, raw)
    # Override the oracle stub to be empty *and* still expect the market to
    # come through.
    monkeypatch.setattr(markets_mod.Markets, "_get_oracle_prices", lambda self: {})

    config = _build_config()
    markets = Markets(config)
    result = markets._process_markets()

    assert _ETH_MARKET_ADDR in result, "Oracle-missing markets must NOT be filtered out"


def test_empty_processed_markets_still_raises(monkeypatch):
    """Preserve the existing PR-#722 guard: an empty result must raise."""
    from eth_defi.gmx.core.markets import Markets

    # All markets have zero-address index tokens, so all get skipped.
    raw = [_build_raw_market_tuple(_ETH_MARKET_ADDR, "0x" + "0" * 40)]
    _install_mocks(monkeypatch, raw, on_chain_count=1)

    config = _build_config()
    markets = Markets(config)
    with pytest.raises(ValueError, match="empty dict"):
        markets._process_markets()


# ---------------------------------------------------------------------------
# _normalize_rest_market tests
# ---------------------------------------------------------------------------
_NRM_MARKET_TOKEN = "0x47c031236e19d024b42f8AE6780E44A573170703"
_NRM_INDEX_TOKEN = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
_NRM_LONG_TOKEN = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
_NRM_SHORT_TOKEN = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"


def test_normalize_rest_market_gmxinfra_shape():
    raw = {
        "marketToken": _NRM_MARKET_TOKEN,
        "indexToken": _NRM_INDEX_TOKEN,
        "longToken": _NRM_LONG_TOKEN,
        "shortToken": _NRM_SHORT_TOKEN,
        "isListed": True,
    }
    result = _normalize_rest_market(raw)
    assert result is not None
    assert result["market_address"] == _NRM_MARKET_TOKEN
    assert result["index_token_address"] == _NRM_INDEX_TOKEN
    assert result["long_token_address"] == _NRM_LONG_TOKEN
    assert result["short_token_address"] == _NRM_SHORT_TOKEN
    assert result["is_listed"] is True


def test_normalize_rest_market_gmxapi_shape():
    raw = {
        "marketTokenAddress": _NRM_MARKET_TOKEN,
        "indexTokenAddress": _NRM_INDEX_TOKEN,
        "longTokenAddress": _NRM_LONG_TOKEN,
        "shortTokenAddress": _NRM_SHORT_TOKEN,
        "isListed": True,
        "isSpotOnly": False,
    }
    result = _normalize_rest_market(raw)
    assert result is not None
    assert result["market_address"] == _NRM_MARKET_TOKEN
    assert result["index_token_address"] == _NRM_INDEX_TOKEN


def test_normalize_rest_market_unlisted_returns_none():
    raw = {
        "marketToken": _NRM_MARKET_TOKEN,
        "indexToken": _NRM_INDEX_TOKEN,
        "longToken": _NRM_LONG_TOKEN,
        "shortToken": _NRM_SHORT_TOKEN,
        "isListed": False,
    }
    assert _normalize_rest_market(raw) is None


def test_normalize_rest_market_zero_index_token_returns_none():
    zero = "0x0000000000000000000000000000000000000000"
    raw = {
        "marketToken": _NRM_MARKET_TOKEN,
        "indexToken": zero,
        "longToken": _NRM_LONG_TOKEN,
        "shortToken": _NRM_SHORT_TOKEN,
        "isListed": True,
    }
    assert _normalize_rest_market(raw) is None


# ---------------------------------------------------------------------------
# _fetch_markets_from_rest tests
# ---------------------------------------------------------------------------
def test_fetch_markets_from_rest_returns_normalized_list():
    """_fetch_markets_from_rest should strip unlisted entries and normalise keys."""
    from eth_defi.gmx.core.markets import Markets

    config = _build_config()
    m = Markets(config)

    fake_api_response = {
        "markets": [
            {
                "marketToken": _BTC_MARKET_ADDR,
                "indexToken": _BTC_INDEX,
                "longToken": _USDC_LONG,
                "shortToken": _USDC_SHORT,
                "isListed": True,
            },
            {
                "marketToken": _ETH_MARKET_ADDR,
                "indexToken": _ETH_INDEX,
                "longToken": _USDC_LONG,
                "shortToken": _USDC_SHORT,
                "isListed": False,
            },
        ]
    }

    with patch("eth_defi.gmx.core.markets.GMXAPI") as mock_gmx_api:
        mock_gmx_api.return_value.get_markets.return_value = fake_api_response
        result = m._fetch_markets_from_rest()

    assert len(result) == 1
    assert result[0]["market_address"] == _BTC_MARKET_ADDR
    assert result[0]["index_token_address"] == _BTC_INDEX
    assert result[0]["is_listed"] is True


def test_process_markets_falls_back_to_onchain_when_rest_fails():
    """When REST /markets raises, _process_markets must call _fetch_markets_from_onchain."""

    from eth_defi.gmx.core.markets import _CLASS_MARKETS_CACHE, Markets

    _CLASS_MARKETS_CACHE.clear()
    config = _build_config()
    m = Markets(config)

    onchain_markets = [
        {
            "market_address": _BTC_MARKET_ADDR,
            "index_token_address": _BTC_INDEX,
            "long_token_address": _USDC_LONG,
            "short_token_address": _USDC_SHORT,
            "is_listed": True,
        }
    ]
    token_meta = _build_token_metadata([_BTC_INDEX, _USDC_LONG, _USDC_SHORT])

    with (
        patch.object(m, "_fetch_markets_from_rest", side_effect=RuntimeError("all REST endpoints down")),
        patch.object(m, "_fetch_markets_from_onchain", return_value=onchain_markets),
        patch.object(m, "_get_token_metadata_dict", return_value=token_meta),
    ):
        result = m._process_markets()

    assert _BTC_MARKET_ADDR in result
    assert result[_BTC_MARKET_ADDR]["index_token_address"] == _BTC_INDEX
