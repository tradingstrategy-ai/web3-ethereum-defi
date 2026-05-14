"""Async wrapper around :class:`eth_defi.gmx.core.market_catalog.MarketCatalog`.

The underlying GMX SDK (``Markets``, ``GetAvailableLiquidity``,
``GetOpenInterest``) is sync, and the catalog's hot path (memory cache hit)
returns in microseconds — so there is no benefit to re-implementing
enumeration / augmentation in async.  Instead this module exposes an
:class:`AsyncMarketCatalog` that delegates to a shared :class:`MarketCatalog`
instance via :func:`asyncio.to_thread`, keeping IO off the event loop on the
~5-minute boundary when the in-memory cache expires.

Sync + async callers share the same on-disk cache file
(``{cache_dir}/market_catalog_{chain_id}.json``) so the two adapters never
diverge: a refresh triggered from either side warms the cache for the other.

Per the project sync↔async lockstep memory rule: every change to the sync
catalog API must be mirrored here (and vice versa) in the same PR so
downstream consumers never see drift.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from eth_defi.gmx.core.market_catalog import (
    DEFAULT_DISK_TTL_SECONDS,
    DEFAULT_MEMORY_TTL_SECONDS,
    MarketCatalog,
    MarketEntry,
    MarketSelection,
    NoMarketFoundError,
)

if TYPE_CHECKING:
    from eth_defi.gmx.config import GMXConfig

logger = logging.getLogger(__name__)

# Re-export the value types so async-only callers don't have to import from
# both modules.  The dataclass + enum + exception are pure-Python and have no
# sync/async distinction — only the IO-bound class needs an async surface.
__all__ = [
    "AsyncMarketCatalog",
    "MarketEntry",
    "MarketSelection",
    "NoMarketFoundError",
]


class AsyncMarketCatalog:
    """Async-facing GMX V2 market catalog.

    Thin wrapper that delegates every IO-bound operation to a shared
    :class:`MarketCatalog` instance running on a worker thread.  Cache
    hits return without thread-hopping when possible (the underlying
    catalog short-circuits when the in-memory entry is fresh), so the
    overhead is bounded to ~5-minute boundaries (memory TTL) or
    ~24-hour boundaries (disk TTL).

    The disk cache file is identical to the sync catalog's, so sync and
    async callers see the same snapshot.  This avoids the failure mode
    where a sync caller refreshes the catalog but the async caller still
    serves the stale memory copy (or vice versa).

    :param config: GMX configuration with ``chain`` and ``web3``.
    :param chain_id: Numeric chain ID (42161 for Arbitrum, 43114 for
        Avalanche) — embedded in the cache filename for multi-chain
        deployments.
    :param cache_dir: Override the default ``~/.cache/eth_defi/gmx``.
    :param disk_ttl_seconds: Persisted snapshot lifetime.  Default 24 h.
    :param memory_ttl_seconds: In-memory snapshot lifetime.  Default 5 min.
    :param sync_catalog: Pre-built :class:`MarketCatalog`.  Pass this when
        sync and async callers must share the *exact* same instance
        (rare — file-sharing via the on-disk cache is usually enough).
    """

    def __init__(
        self,
        config: "GMXConfig" = None,
        chain_id: int | None = None,
        *,
        cache_dir: Path | str | None = None,
        disk_ttl_seconds: int = DEFAULT_DISK_TTL_SECONDS,
        memory_ttl_seconds: int = DEFAULT_MEMORY_TTL_SECONDS,
        sync_catalog: MarketCatalog | None = None,
    ) -> None:
        if sync_catalog is not None:
            self._sync = sync_catalog
        else:
            if config is None or chain_id is None:
                raise ValueError(
                    "AsyncMarketCatalog requires either sync_catalog or both config and chain_id"
                )
            self._sync = MarketCatalog(
                config=config,
                chain_id=chain_id,
                cache_dir=cache_dir,
                disk_ttl_seconds=disk_ttl_seconds,
                memory_ttl_seconds=memory_ttl_seconds,
            )

    @property
    def sync_catalog(self) -> MarketCatalog:
        """Expose the wrapped :class:`MarketCatalog` for sync-only call sites
        (e.g. inside synchronous CCXT helpers that share the same async
        adapter).  Use sparingly — most callers should go through the
        async surface.
        """
        return self._sync

    @property
    def chain_id(self) -> int:
        return self._sync.chain_id

    @property
    def cache_file(self) -> Path:
        return self._sync.cache_file

    async def get_entries(self) -> list[MarketEntry]:
        """Async equivalent of :meth:`MarketCatalog.get_entries`.

        Off-loads the (mostly-cached) call to a worker thread so the
        event loop isn't blocked when the in-memory cache expires and
        the catalog rebuilds via on-chain multicalls.
        """
        return await asyncio.to_thread(self._sync.get_entries)

    async def refresh(self) -> list[MarketEntry]:
        """Async equivalent of :meth:`MarketCatalog.refresh`."""
        return await asyncio.to_thread(self._sync.refresh)

    async def pick_market(
        self,
        base_symbol: str,
        selection: MarketSelection = MarketSelection.USDC_PAIRED,
        explicit_market_key: str | None = None,
    ) -> MarketEntry:
        """Async equivalent of :meth:`MarketCatalog.pick_market`.

        Same three selection strategies, same fallback chain, same
        ``NoMarketFoundError`` semantics as the sync version.  When the
        underlying catalog has a fresh in-memory snapshot, the worker
        thread returns immediately — no RPC traffic.
        """
        return await asyncio.to_thread(
            self._sync.pick_market,
            base_symbol,
            selection,
            explicit_market_key,
        )
