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
) -> tuple[str, str, str, str]:
    """Return a tuple shaped like one row of ``MarketReader.getMarkets()``."""
    return (market_address, index_token, long_token, short_token)


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
    raw_markets: list[tuple[str, str, str, str]],
    token_metadata: dict[str, dict[str, Any]] | None = None,
    disabled_addresses: set[str] | None = None,
    on_chain_count: int | None = None,
):
    """Patch the network surfaces ``_process_markets`` depends on."""
    from eth_defi.gmx.core import markets as markets_mod

    token_meta = token_metadata or _build_token_metadata([t for row in raw_markets for t in row[1:]])
    disabled = disabled_addresses or set()
    count = on_chain_count if on_chain_count is not None else len(raw_markets)

    raw_call = MagicMock(return_value=list(raw_markets))
    monkeypatch.setattr(markets_mod.Markets, "_get_available_markets_raw", raw_call)
    monkeypatch.setattr(markets_mod.Markets, "_get_token_metadata_dict", lambda self: dict(token_meta))
    monkeypatch.setattr(markets_mod.Markets, "_get_oracle_prices", lambda self: {})

    disabled_check = MagicMock(return_value={addr: (addr in disabled) for addr in [row[0] for row in raw_markets]})
    monkeypatch.setattr(markets_mod.Markets, "_check_markets_disabled_onchain", disabled_check, raising=False)

    count_call = MagicMock(return_value=count)
    monkeypatch.setattr(markets_mod.Markets, "_get_on_chain_market_count", count_call, raising=False)

    return {"raw_call": raw_call, "disabled_check": disabled_check, "count_call": count_call}


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
    """A market flagged as disabled by ``IS_MARKET_DISABLED`` is excluded."""
    from eth_defi.gmx.core.markets import Markets

    raw = [
        _build_raw_market_tuple(_ETH_MARKET_ADDR, _ETH_INDEX),
        _build_raw_market_tuple(_BTC_MARKET_ADDR, _BTC_INDEX),
    ]
    _install_mocks(
        monkeypatch,
        raw,
        disabled_addresses={_BTC_MARKET_ADDR},
        on_chain_count=2,
    )

    config = _build_config()
    markets = Markets(config)
    result = markets._process_markets()

    assert _ETH_MARKET_ADDR in result
    assert _BTC_MARKET_ADDR not in result, "Disabled market must be filtered out"


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
