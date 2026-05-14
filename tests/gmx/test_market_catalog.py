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


class TestAugmentWithLiquidity:
    """``augment_with_liquidity`` populates ``liquidity_usd``, ``oi_long_usd``,
    ``oi_short_usd`` from on-chain / REST sources (GetAvailableLiquidity +
    GetOpenInterest — neither depends on Subsquid).  Graceful degradation:
    when augmentation fails, entries keep their zero-init values and a single
    WARNING is logged — never raises.
    """

    @staticmethod
    def _base_entries():
        from eth_defi.gmx.core.market_catalog import MarketEntry

        return [
            MarketEntry(
                market_key="0xWBTCUSDC",
                index_token_symbol="BTC",
                index_token_address="0xBtcIndex",
                long_token_symbol="WBTC",
                long_token_address="0xWbtc",
                short_token_symbol="USDC",
                short_token_address="0xUsdc",
                liquidity_usd=0.0,
                oi_long_usd=0.0,
                oi_short_usd=0.0,
                refreshed_at=1_700_000_000,
            ),
            MarketEntry(
                market_key="0xTBTC2",
                index_token_symbol="BTC",
                index_token_address="0xBtcIndex",
                long_token_symbol="tBTC",
                long_token_address="0xTbtc",
                short_token_symbol="tBTC",
                short_token_address="0xTbtc",
                liquidity_usd=0.0,
                oi_long_usd=0.0,
                oi_short_usd=0.0,
                refreshed_at=1_700_000_000,
            ),
            MarketEntry(
                market_key="0xBONKUSDC",
                index_token_symbol="BONK",
                index_token_address="0xBonkIndex",
                long_token_symbol="BONK",
                long_token_address="0xKbonk",
                short_token_symbol="USDC",
                short_token_address="0xUsdc",
                liquidity_usd=0.0,
                oi_long_usd=0.0,
                oi_short_usd=0.0,
                refreshed_at=1_700_000_000,
            ),
        ]

    def test_augment_populates_liquidity_and_oi_from_contract_calls(self, monkeypatch):
        from eth_defi.gmx.core import market_catalog as mc

        # GMX core returns dicts keyed by `market_symbol`, NOT by market_key —
        # the catalog augmenter must resolve market_symbol per entry.
        monkeypatch.setattr(
            mc,
            "_resolve_market_symbol",
            lambda config, key: {"0xWBTCUSDC": "BTC", "0xTBTC2": "BTC2", "0xBONKUSDC": "BONK"}[key],
        )
        monkeypatch.setattr(
            mc,
            "_fetch_available_liquidity",
            lambda config: {
                "long": {"BTC": 500_000_000.0, "BTC2": 5_000_000.0, "BONK": 1_000_000.0},
                "short": {"BTC": 500_000_000.0, "BTC2": 5_000_000.0, "BONK": 1_000_000.0},
                "parameter": "available_liquidity",
            },
        )
        monkeypatch.setattr(
            mc,
            "_fetch_open_interest",
            lambda config: {
                "long": {"BTC": 200_000_000.0, "BTC2": 1_000_000.0, "BONK": 250_000.0},
                "short": {"BTC": 180_000_000.0, "BTC2": 900_000.0, "BONK": 220_000.0},
                "parameter": "open_interest",
            },
        )

        entries = mc.augment_with_liquidity(self._base_entries(), config=object())
        by_key = {e.market_key: e for e in entries}

        # liquidity_usd is long + short available — total tradable depth.
        assert by_key["0xWBTCUSDC"].liquidity_usd == pytest.approx(1_000_000_000.0)
        assert by_key["0xTBTC2"].liquidity_usd == pytest.approx(10_000_000.0)
        assert by_key["0xBONKUSDC"].liquidity_usd == pytest.approx(2_000_000.0)

        # OI populated per side.
        assert by_key["0xWBTCUSDC"].oi_long_usd == pytest.approx(200_000_000.0)
        assert by_key["0xWBTCUSDC"].oi_short_usd == pytest.approx(180_000_000.0)
        assert by_key["0xBONKUSDC"].oi_long_usd == pytest.approx(250_000.0)

    def test_augment_returns_entries_unchanged_when_liquidity_fetch_fails(self, monkeypatch, caplog):
        import logging

        from eth_defi.gmx.core import market_catalog as mc

        monkeypatch.setattr(mc, "_resolve_market_symbol", lambda config, key: "BTC")
        monkeypatch.setattr(mc, "_fetch_available_liquidity", lambda config: None)
        monkeypatch.setattr(
            mc,
            "_fetch_open_interest",
            lambda config: {"long": {"BTC": 1.0}, "short": {"BTC": 2.0}, "parameter": "open_interest"},
        )

        caplog.set_level(logging.WARNING, logger="eth_defi.gmx.core.market_catalog")
        entries = mc.augment_with_liquidity(self._base_entries(), config=object())

        # When liquidity source fails, OI may still be populated (each source
        # degrades independently).  Liquidity stays at zero.
        for e in entries:
            assert e.liquidity_usd == 0.0
        assert any("liquidity" in rec.message.lower() for rec in caplog.records)

    def test_augment_handles_total_fetch_failure_gracefully(self, monkeypatch, caplog):
        import logging

        from eth_defi.gmx.core import market_catalog as mc

        monkeypatch.setattr(mc, "_resolve_market_symbol", lambda config, key: "BTC")
        monkeypatch.setattr(mc, "_fetch_available_liquidity", lambda config: None)
        monkeypatch.setattr(mc, "_fetch_open_interest", lambda config: None)

        caplog.set_level(logging.WARNING, logger="eth_defi.gmx.core.market_catalog")
        # Must not raise — caller can still use the catalog with zero
        # liquidity (selection will fall back to first-listed order).
        entries = mc.augment_with_liquidity(self._base_entries(), config=object())
        for e in entries:
            assert e.liquidity_usd == 0.0
            assert e.oi_long_usd == 0.0
            assert e.oi_short_usd == 0.0

    def test_augment_skips_unknown_market_symbols(self, monkeypatch):
        from eth_defi.gmx.core import market_catalog as mc

        monkeypatch.setattr(
            mc, "_resolve_market_symbol", lambda config, key: None  # symbol resolver returns None
        )
        monkeypatch.setattr(
            mc,
            "_fetch_available_liquidity",
            lambda config: {"long": {"BTC": 1.0}, "short": {"BTC": 1.0}, "parameter": "available_liquidity"},
        )
        monkeypatch.setattr(
            mc,
            "_fetch_open_interest",
            lambda config: {"long": {"BTC": 1.0}, "short": {"BTC": 1.0}, "parameter": "open_interest"},
        )

        entries = mc.augment_with_liquidity(self._base_entries(), config=object())
        # No market_symbol → no lookup → zero values preserved.
        for e in entries:
            assert e.liquidity_usd == 0.0

    def test_augment_partial_oi_data_uses_what_is_available(self, monkeypatch):
        """If OI only covers one side (e.g. short-only synthetic), the other
        side stays at 0.0 and no exception is raised.
        """
        from eth_defi.gmx.core import market_catalog as mc

        monkeypatch.setattr(
            mc,
            "_resolve_market_symbol",
            lambda config, key: {"0xWBTCUSDC": "BTC", "0xTBTC2": "BTC2", "0xBONKUSDC": "BONK"}[key],
        )
        monkeypatch.setattr(
            mc,
            "_fetch_available_liquidity",
            lambda config: {"long": {"BTC": 100.0}, "short": {}, "parameter": "available_liquidity"},
        )
        monkeypatch.setattr(
            mc,
            "_fetch_open_interest",
            lambda config: {"long": {"BTC": 50.0}, "short": {}, "parameter": "open_interest"},
        )

        entries = mc.augment_with_liquidity(self._base_entries(), config=object())
        btc = next(e for e in entries if e.market_key == "0xWBTCUSDC")
        # liquidity_usd = long-only since short side missing — degraded but defined.
        assert btc.liquidity_usd == pytest.approx(100.0)
        assert btc.oi_long_usd == pytest.approx(50.0)
        assert btc.oi_short_usd == 0.0


class TestMarketCatalogCache:
    """``MarketCatalog`` glues enumeration + augmentation + TTL caching.

    Memory cache: 5-minute TTL for hot-path latency.
    Disk cache: 24-hour TTL so a process restart re-uses the snapshot
    instead of refetching every market on cold start.
    """

    @staticmethod
    def _fake_entries():
        from eth_defi.gmx.core.market_catalog import MarketEntry

        return [
            MarketEntry(
                market_key="0xKEY1",
                index_token_symbol="ETH",
                index_token_address="0xEth",
                long_token_symbol="WETH",
                long_token_address="0xWeth",
                short_token_symbol="USDC",
                short_token_address="0xUsdc",
                liquidity_usd=100_000_000.0,
                oi_long_usd=10_000_000.0,
                oi_short_usd=8_000_000.0,
                refreshed_at=1_700_000_000,
            )
        ]

    def _make_catalog(self, tmp_path, monkeypatch, build_count_ref):
        """Build a MarketCatalog instance with mocked enumerate+augment.

        The shared build counter lets tests assert how many times the
        underlying pipeline was triggered — used to verify TTL / cache hits.
        """
        from eth_defi.gmx.core import market_catalog as mc

        def fake_enumerate(config, now_ts=None):
            build_count_ref["calls"] += 1
            return self._fake_entries()

        monkeypatch.setattr(mc, "enumerate_markets", fake_enumerate)
        monkeypatch.setattr(mc, "augment_with_liquidity", lambda entries, config: entries)

        return mc.MarketCatalog(
            config=object(),
            chain_id=42161,
            cache_dir=tmp_path,
            disk_ttl_seconds=86400,
            memory_ttl_seconds=300,
        )

    def test_first_call_builds_and_writes_disk(self, tmp_path, monkeypatch):
        from eth_defi.gmx.core import market_catalog as mc

        counter = {"calls": 0}
        catalog = self._make_catalog(tmp_path, monkeypatch, counter)
        entries = catalog.get_entries()
        assert len(entries) == 1
        assert counter["calls"] == 1
        # File exists at expected path with chain_id encoded.
        cache_file = tmp_path / "market_catalog_42161.json"
        assert cache_file.exists()

    def test_second_call_within_memory_ttl_skips_rebuild(self, tmp_path, monkeypatch):
        counter = {"calls": 0}
        catalog = self._make_catalog(tmp_path, monkeypatch, counter)
        catalog.get_entries()
        catalog.get_entries()
        # Memory hit on the second call.
        assert counter["calls"] == 1

    def test_force_refresh_rebuilds(self, tmp_path, monkeypatch):
        counter = {"calls": 0}
        catalog = self._make_catalog(tmp_path, monkeypatch, counter)
        catalog.get_entries()
        catalog.refresh()
        assert counter["calls"] == 2

    def test_new_instance_reads_disk_within_disk_ttl(self, tmp_path, monkeypatch):
        from eth_defi.gmx.core import market_catalog as mc

        # First instance builds + persists.
        counter1 = {"calls": 0}
        c1 = self._make_catalog(tmp_path, monkeypatch, counter1)
        c1.get_entries()
        assert counter1["calls"] == 1

        # Second instance (fresh process equivalent) should hit disk, not rebuild.
        counter2 = {"calls": 0}
        c2 = self._make_catalog(tmp_path, monkeypatch, counter2)
        entries = c2.get_entries()
        assert counter2["calls"] == 0
        assert len(entries) == 1
        # Same content as written.
        assert entries[0].market_key == "0xKEY1"

    def test_expired_disk_entry_triggers_rebuild(self, tmp_path, monkeypatch):
        from eth_defi.gmx.core import market_catalog as mc

        counter = {"calls": 0}
        catalog = self._make_catalog(tmp_path, monkeypatch, counter)
        catalog.get_entries()
        assert counter["calls"] == 1

        # Tamper with cache file: backdate the saved-at timestamp past TTL.
        cache_file = tmp_path / "market_catalog_42161.json"
        import json

        blob = json.loads(cache_file.read_text())
        blob["saved_at_unix"] = 0  # 1970 — way past 24h
        cache_file.write_text(json.dumps(blob))

        # New instance now sees a stale file → rebuilds.
        counter2 = {"calls": 0}
        catalog2 = self._make_catalog(tmp_path, monkeypatch, counter2)
        catalog2.get_entries()
        assert counter2["calls"] == 1

    def test_corrupt_disk_cache_falls_back_to_rebuild(self, tmp_path, monkeypatch, caplog):
        import logging

        counter = {"calls": 0}
        # Write a corrupt cache file BEFORE any catalog instance.
        cache_file = tmp_path / "market_catalog_42161.json"
        cache_file.write_text("{not valid json")
        caplog.set_level(logging.WARNING, logger="eth_defi.gmx.core.market_catalog")

        catalog = self._make_catalog(tmp_path, monkeypatch, counter)
        catalog.get_entries()
        # Should have rebuilt despite the bogus file.
        assert counter["calls"] == 1
        assert any("corrupt" in rec.message.lower() or "decode" in rec.message.lower() for rec in caplog.records)

    def test_disk_write_failure_still_serves_from_memory(self, tmp_path, monkeypatch, caplog):
        import logging

        counter = {"calls": 0}
        # Point cache_dir at a non-existent, non-creatable path.
        cache_file = tmp_path / "subdir" / "market_catalog_42161.json"
        # Pre-create the parent as a file so subdir creation fails.
        (tmp_path / "subdir").write_text("blocker")

        from eth_defi.gmx.core import market_catalog as mc

        def fake_enumerate(config, now_ts=None):
            counter["calls"] += 1
            return self._fake_entries()

        monkeypatch.setattr(mc, "enumerate_markets", fake_enumerate)
        monkeypatch.setattr(mc, "augment_with_liquidity", lambda entries, config: entries)

        caplog.set_level(logging.WARNING, logger="eth_defi.gmx.core.market_catalog")
        catalog = mc.MarketCatalog(
            config=object(),
            chain_id=42161,
            cache_dir=tmp_path / "subdir",
            disk_ttl_seconds=86400,
            memory_ttl_seconds=300,
        )
        entries = catalog.get_entries()
        # Build succeeded; disk write failed silently.
        assert len(entries) == 1
        assert counter["calls"] == 1
        assert any("write" in rec.message.lower() or "persist" in rec.message.lower() for rec in caplog.records)

    def test_cache_file_path_includes_chain_id(self, tmp_path, monkeypatch):
        from eth_defi.gmx.core import market_catalog as mc

        monkeypatch.setattr(mc, "enumerate_markets", lambda config, now_ts=None: self._fake_entries())
        monkeypatch.setattr(mc, "augment_with_liquidity", lambda entries, config: entries)

        c1 = mc.MarketCatalog(
            config=object(), chain_id=42161, cache_dir=tmp_path,
            disk_ttl_seconds=1, memory_ttl_seconds=1,
        )
        c2 = mc.MarketCatalog(
            config=object(), chain_id=43114, cache_dir=tmp_path,
            disk_ttl_seconds=1, memory_ttl_seconds=1,
        )
        c1.get_entries()
        c2.get_entries()
        assert (tmp_path / "market_catalog_42161.json").exists()
        assert (tmp_path / "market_catalog_43114.json").exists()
