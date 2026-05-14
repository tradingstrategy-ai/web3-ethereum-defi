"""Tests for :mod:`eth_defi.gmx.core.market_catalog`.

Task 1.1 of the GMX market-catalog rewrite covers :class:`MarketEntry` only —
construction invariants plus the ``is_synthetic`` and ``accepts_collateral``
helpers.  Subsequent tasks layer enumeration, augmentation, caching, and
selection on top.
"""

from __future__ import annotations

import pytest


def _entry(**overrides):
    """Build a fully populated :class:`MarketEntry` with sensible defaults.

    Tests override only the fields they care about — keeps the call sites short
    and self-documenting.
    """
    from eth_defi.gmx.core.market_catalog import MarketEntry

    defaults = {
        "market_key": "0xMarketKey",
        "index_token_symbol": "BTC",
        "index_token_address": "0xIndex",
        "long_token_symbol": "WBTC",
        "long_token_address": "0xLong",
        "short_token_symbol": "USDC",
        "short_token_address": "0xShort",
        "liquidity_usd": 0.0,
        "oi_long_usd": 0.0,
        "oi_short_usd": 0.0,
        "refreshed_at": 0,
    }
    defaults.update(overrides)
    return MarketEntry(**defaults)


class TestMarketEntry:
    """:class:`MarketEntry` invariants — frozen, slotted, deterministic helpers."""

    def test_construction_round_trips(self):
        e = _entry(market_key="0xMARKETKEY", liquidity_usd=1_234_567.89)
        assert e.market_key == "0xMARKETKEY"
        assert e.liquidity_usd == pytest.approx(1_234_567.89)

    def test_is_synthetic_true_when_long_eq_short(self):
        e = _entry(
            long_token_symbol="tBTC",
            long_token_address="0xtbtc",
            short_token_symbol="tBTC",
            short_token_address="0xtbtc",
        )
        assert e.is_synthetic is True

    def test_is_synthetic_false_for_two_sided_pool(self):
        e = _entry(
            long_token_symbol="WBTC",
            long_token_address="0xwbtc",
            short_token_symbol="USDC",
            short_token_address="0xusdc",
        )
        assert e.is_synthetic is False

    def test_is_synthetic_case_insensitive(self):
        # On-chain returns mixed-case checksum addresses; comparison must not
        # be tripped by casing.
        e = _entry(
            long_token_address="0xAbCdEf",
            short_token_address="0xabcdef",
        )
        assert e.is_synthetic is True

    def test_accepts_collateral_matches_long(self):
        e = _entry(long_token_symbol="WBTC", short_token_symbol="USDC")
        assert e.accepts_collateral("WBTC") is True

    def test_accepts_collateral_matches_short(self):
        e = _entry(long_token_symbol="WBTC", short_token_symbol="USDC")
        assert e.accepts_collateral("USDC") is True

    def test_accepts_collateral_rejects_unsupported(self):
        e = _entry(long_token_symbol="tBTC", short_token_symbol="tBTC")
        assert e.accepts_collateral("USDC") is False

    def test_accepts_collateral_case_insensitive(self):
        e = _entry(long_token_symbol="WBTC", short_token_symbol="USDC")
        assert e.accepts_collateral("usdc") is True
        assert e.accepts_collateral("Wbtc") is True

    def test_frozen_dataclass_blocks_mutation(self):
        e = _entry()
        with pytest.raises(Exception):
            e.liquidity_usd = 999.0  # type: ignore[misc]
