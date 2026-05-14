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


class TestEnumerateMarkets:
    """``enumerate_markets`` converts ``Markets.get_available_markets`` output
    into a list of :class:`MarketEntry` rows with ``SYMBOL_NORMALISE`` applied
    defensively to every token symbol.

    Liquidity / OI augmentation is layered in Task 1.3 — at this stage every
    row reports ``0.0`` for liquidity_usd, oi_long_usd, oi_short_usd.
    """

    @staticmethod
    def _markets_blob():
        """Synthetic two-pool BTC + one-pool BONK markets dict matching the
        shape returned by :meth:`Markets.get_available_markets`.
        """
        return {
            "0xWBTCUSDC": {
                "gmx_market_address": "0xWBTCUSDC",
                "market_symbol": "BTC",  # Already normalised upstream.
                "index_token_address": "0xBtcIndex",
                "market_metadata": {"symbol": "BTC", "decimals": 8, "synthetic": False},
                "long_token_metadata": {"symbol": "WBTC", "decimals": 8},
                "long_token_address": "0xWbtc",
                "short_token_metadata": {"symbol": "USDC", "decimals": 6},
                "short_token_address": "0xUsdc",
            },
            "0xTBTC2": {
                "gmx_market_address": "0xTBTC2",
                "market_symbol": "BTC2",  # GMX shorthand for synthetic.
                "index_token_address": "0xBtcIndex",
                "market_metadata": {"symbol": "BTC", "decimals": 8, "synthetic": True},
                "long_token_metadata": {"symbol": "tBTC", "decimals": 18},
                "long_token_address": "0xTbtc",
                "short_token_metadata": {"symbol": "tBTC", "decimals": 18},
                "short_token_address": "0xTbtc",
            },
            "0xBONKUSDC": {
                "gmx_market_address": "0xBONKUSDC",
                # Upstream sometimes leaves k-prefix in raw metadata even when
                # market_symbol is already normalised — verify both paths apply
                # SYMBOL_NORMALISE so the catalog reports ``BONK``.
                "market_symbol": "kBONK",
                "index_token_address": "0xBonkIndex",
                "market_metadata": {"symbol": "kBONK", "decimals": 5, "synthetic": False},
                "long_token_metadata": {"symbol": "kBONK", "decimals": 5},
                "long_token_address": "0xKbonk",
                "short_token_metadata": {"symbol": "USDC", "decimals": 6},
                "short_token_address": "0xUsdc",
            },
        }

    def test_enumerate_returns_one_entry_per_market(self, monkeypatch):
        from eth_defi.gmx.core import market_catalog as mc

        monkeypatch.setattr(
            mc, "_load_raw_markets", lambda config: self._markets_blob()
        )
        entries = mc.enumerate_markets(config=object(), now_ts=1_700_000_000)
        assert {e.market_key for e in entries} == {"0xWBTCUSDC", "0xTBTC2", "0xBONKUSDC"}

    def test_enumerate_normalises_index_symbol_for_k_prefix(self, monkeypatch):
        from eth_defi.gmx.core import market_catalog as mc

        monkeypatch.setattr(
            mc, "_load_raw_markets", lambda config: self._markets_blob()
        )
        entries = mc.enumerate_markets(config=object(), now_ts=1_700_000_000)
        bonk = next(e for e in entries if e.market_key == "0xBONKUSDC")
        # Both index and long-token symbols must lose the k-prefix.
        assert bonk.index_token_symbol == "BONK"
        assert bonk.long_token_symbol == "BONK"
        assert bonk.short_token_symbol == "USDC"

    def test_enumerate_detects_synthetic_via_dataclass_property(self, monkeypatch):
        from eth_defi.gmx.core import market_catalog as mc

        monkeypatch.setattr(
            mc, "_load_raw_markets", lambda config: self._markets_blob()
        )
        entries = {e.market_key: e for e in mc.enumerate_markets(config=object(), now_ts=1_700_000_000)}
        assert entries["0xTBTC2"].is_synthetic is True
        assert entries["0xWBTCUSDC"].is_synthetic is False

    def test_enumerate_sets_refreshed_at_and_zero_liquidity(self, monkeypatch):
        from eth_defi.gmx.core import market_catalog as mc

        monkeypatch.setattr(
            mc, "_load_raw_markets", lambda config: self._markets_blob()
        )
        entries = mc.enumerate_markets(config=object(), now_ts=1_700_000_000)
        for e in entries:
            # Liquidity / OI augmentation happens in Task 1.3 — for now every
            # row reports 0.0 and exposes ``refreshed_at`` so the catalog cache
            # can compute TTLs.
            assert e.refreshed_at == 1_700_000_000
            assert e.liquidity_usd == 0.0
            assert e.oi_long_usd == 0.0
            assert e.oi_short_usd == 0.0

    def test_enumerate_empty_input_returns_empty_list(self, monkeypatch):
        from eth_defi.gmx.core import market_catalog as mc

        monkeypatch.setattr(mc, "_load_raw_markets", lambda config: {})
        assert mc.enumerate_markets(config=object(), now_ts=1_700_000_000) == []

    def test_enumerate_skips_entries_with_missing_required_fields(self, monkeypatch):
        from eth_defi.gmx.core import market_catalog as mc

        # Malformed entry: missing market_metadata.symbol -- pipeline should
        # skip it with a debug log rather than crash the whole enumeration.
        bad = {
            "0xBROKEN": {
                "gmx_market_address": "0xBROKEN",
                "market_symbol": "",
                "index_token_address": "0xIndex",
                "market_metadata": {},
                "long_token_metadata": {"symbol": "USDC"},
                "long_token_address": "0xUsdc",
                "short_token_metadata": {"symbol": "USDC"},
                "short_token_address": "0xUsdc",
            },
            "0xOK": self._markets_blob()["0xWBTCUSDC"],
        }
        monkeypatch.setattr(mc, "_load_raw_markets", lambda config: bad)
        entries = mc.enumerate_markets(config=object(), now_ts=1_700_000_000)
        assert {e.market_key for e in entries} == {"0xOK"}
