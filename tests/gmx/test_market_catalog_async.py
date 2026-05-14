"""Coverage for :mod:`eth_defi.gmx.core.market_catalog_async`.

The async wrapper is intentionally thin — it delegates every IO-bound
operation to the sync :class:`MarketCatalog` via :func:`asyncio.to_thread`.
These tests verify the contract: same shapes, same selection strategies,
same error semantics, sync + async sharing the same on-disk cache file.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def fake_entries():
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
            liquidity_usd=1_000_000_000.0,
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
            liquidity_usd=5_000_000.0,
            oi_long_usd=0.0,
            oi_short_usd=0.0,
            refreshed_at=1_700_000_000,
        ),
    ]


@pytest.fixture
def async_catalog(tmp_path, monkeypatch, fake_entries):
    from eth_defi.gmx.core import market_catalog as mc
    from eth_defi.gmx.core.market_catalog_async import AsyncMarketCatalog

    monkeypatch.setattr(mc, "enumerate_markets", lambda config, now_ts=None: fake_entries)
    monkeypatch.setattr(mc, "augment_with_liquidity", lambda items, config: items)

    return AsyncMarketCatalog(
        config=object(),
        chain_id=42161,
        cache_dir=tmp_path,
        disk_ttl_seconds=86400,
        memory_ttl_seconds=300,
    )


class TestAsyncMarketCatalogConstruction:
    def test_constructs_from_config_and_chain_id(self, tmp_path, monkeypatch):
        from eth_defi.gmx.core.market_catalog_async import AsyncMarketCatalog

        # No sync_catalog argument — must build one internally.
        cat = AsyncMarketCatalog(
            config=object(),
            chain_id=42161,
            cache_dir=tmp_path,
        )
        assert cat.chain_id == 42161
        # cache_file matches the sync naming convention so sync + async
        # callers share the on-disk snapshot.
        assert cat.cache_file == tmp_path / "market_catalog_42161.json"

    def test_constructs_from_existing_sync_catalog(self, tmp_path, monkeypatch):
        from eth_defi.gmx.core.market_catalog import MarketCatalog
        from eth_defi.gmx.core.market_catalog_async import AsyncMarketCatalog

        sync = MarketCatalog(config=object(), chain_id=43114, cache_dir=tmp_path)
        cat = AsyncMarketCatalog(sync_catalog=sync)
        # Wrapping an existing sync catalog must reuse its state, not build
        # a fresh one.  This is the path for sharing one catalog between
        # sync helpers and the async adapter.
        assert cat.sync_catalog is sync
        assert cat.chain_id == 43114

    def test_missing_required_args_raises(self):
        from eth_defi.gmx.core.market_catalog_async import AsyncMarketCatalog

        with pytest.raises(ValueError, match="sync_catalog or both"):
            AsyncMarketCatalog()


class TestAsyncMarketCatalogAPI:
    @pytest.mark.asyncio
    async def test_get_entries_returns_same_shape_as_sync(self, async_catalog, fake_entries):
        entries = await async_catalog.get_entries()
        assert len(entries) == len(fake_entries)
        assert {e.market_key for e in entries} == {"0xWBTCUSDC", "0xTBTC2"}

    @pytest.mark.asyncio
    async def test_refresh_rebuilds(self, async_catalog, monkeypatch, fake_entries):
        from eth_defi.gmx.core import market_catalog as mc

        # Counter that increments each time the underlying pipeline runs.
        counter = {"n": 0}

        def counting_enumerate(config, now_ts=None):
            counter["n"] += 1
            return fake_entries

        monkeypatch.setattr(mc, "enumerate_markets", counting_enumerate)

        await async_catalog.get_entries()
        await async_catalog.refresh()  # second build via refresh
        assert counter["n"] == 2

    @pytest.mark.asyncio
    async def test_pick_market_default_usdc_paired(self, async_catalog):
        from eth_defi.gmx.core.market_catalog import MarketSelection

        # Even though tBTC-tBTC has a position in the list, the USDC_PAIRED
        # default must pick WBTC-USDC.
        chosen = await async_catalog.pick_market("BTC")
        assert chosen.market_key == "0xWBTCUSDC"

        # And explicit USDC_PAIRED yields the same answer.
        chosen = await async_catalog.pick_market("BTC", selection=MarketSelection.USDC_PAIRED)
        assert chosen.market_key == "0xWBTCUSDC"

    @pytest.mark.asyncio
    async def test_pick_market_highest_liquidity(self, async_catalog):
        from eth_defi.gmx.core.market_catalog import MarketSelection

        # WBTC-USDC has higher liquidity in the fixture — HIGHEST_LIQUIDITY
        # picks it for the same reason USDC_PAIRED does, but for a different
        # path through the selector.
        chosen = await async_catalog.pick_market(
            "BTC",
            selection=MarketSelection.HIGHEST_LIQUIDITY,
        )
        assert chosen.market_key == "0xWBTCUSDC"

    @pytest.mark.asyncio
    async def test_pick_market_explicit_override(self, async_catalog):
        chosen = await async_catalog.pick_market(
            "BTC",
            explicit_market_key="0xTBTC2",
        )
        # Explicit always wins, regardless of strategy default.
        assert chosen.market_key == "0xTBTC2"

    @pytest.mark.asyncio
    async def test_pick_market_unknown_base_raises(self, async_catalog):
        from eth_defi.gmx.core.market_catalog_async import NoMarketFoundError

        with pytest.raises(NoMarketFoundError):
            await async_catalog.pick_market("UNKNOWN")

    @pytest.mark.asyncio
    async def test_pick_market_unknown_explicit_key_raises(self, async_catalog):
        from eth_defi.gmx.core.market_catalog_async import NoMarketFoundError

        with pytest.raises(NoMarketFoundError):
            await async_catalog.pick_market("BTC", explicit_market_key="0xMISSING")


class TestAsyncSyncSharedCache:
    """Sync and async catalog instances pointing at the same ``cache_dir``
    must share the on-disk snapshot — a refresh from one side warms the
    other and they never diverge.
    """

    def test_async_reads_disk_written_by_sync(self, tmp_path, monkeypatch, fake_entries):
        # Build via sync, read back via async.
        from eth_defi.gmx.core import market_catalog as mc
        from eth_defi.gmx.core.market_catalog_async import AsyncMarketCatalog

        monkeypatch.setattr(mc, "enumerate_markets", lambda config, now_ts=None: fake_entries)
        monkeypatch.setattr(mc, "augment_with_liquidity", lambda items, config: items)

        sync = mc.MarketCatalog(config=object(), chain_id=42161, cache_dir=tmp_path)
        sync.get_entries()  # build + persist

        # Fresh async catalog at the same path.  Should hit disk, not rebuild.
        counter = {"n": 0}

        def counting_enumerate(config, now_ts=None):
            counter["n"] += 1
            return fake_entries

        monkeypatch.setattr(mc, "enumerate_markets", counting_enumerate)

        async_cat = AsyncMarketCatalog(config=object(), chain_id=42161, cache_dir=tmp_path)

        entries = asyncio.run(async_cat.get_entries())
        assert len(entries) == 2
        # Sync already wrote the file; async loaded it without calling the
        # pipeline a second time.
        assert counter["n"] == 0
