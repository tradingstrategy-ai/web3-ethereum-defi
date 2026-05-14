"""Disk-persisted cache of GMX order_key by tx_hash.

**Root cause this module fixes** (issue #67, 2026-05-14):

``CcxtExchange._orders`` is an in-memory ``dict[tx_hash, order]`` populated
by ``create_order`` and consulted by ``fetch_order`` to extract the on-chain
``order_key`` needed to check execution status.  The dict is initialised
empty on every process restart::

    self._orders = {}  # eth_defi/gmx/ccxt/exchange.py:1554

When the freqtrade bot restarts between order creation and fill (a routine
event — config reload, dyno cycle, deploy), every live limit order loses
its ``order_key``.  ``fetch_order`` then logs ``"no order_key stored,
cannot check execution status"`` and returns the order unchanged — the
order appears perpetually ``open`` even after the GMX keeper fills it
on-chain.  This produced ~3,500 stuck-trade events for BONK + SHIB across
two days of post-fix logs.

:class:`OrderKeyCache` survives restarts by persisting the ``order_key``
to a JSON file in the user's cache directory.  Atomic writes
(``.tmp + rename``) prevent partial-file corruption.  Best-effort: disk
failures (read-only FS, missing parent we cannot create, permission
errors) downgrade to memory-only operation with a WARNING log — the bot
must keep trading even when local persistence is broken.

**Stale entry pruning:** entries older than ``DEFAULT_MAX_ENTRY_AGE_SECONDS``
(30 days) are dropped on load.  Settled / cancelled orders accumulate
forever otherwise; the prune keeps the file bounded without needing
explicit reconciliation against the chain.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


#: Default max age for cached entries.  Anything older than this when the
#: cache is loaded is silently dropped — older orders are either settled
#: long ago (in which case the on-chain state is authoritative) or stale
#: enough that the cached key is unlikely to still resolve.
DEFAULT_MAX_ENTRY_AGE_SECONDS = 30 * 24 * 60 * 60


@dataclass(slots=True)
class OrderKeyRecord:
    """A single (tx_hash → order_key) mapping with enough context to
    aid wallet-scoped recovery if the entry is later evicted.

    :param order_key: The 0x-prefixed bytes32 GMX order key.
    :param tx_hash: Transaction hash that submitted the order (cache key).
    :param symbol: ccxt unified symbol (e.g. ``BTC/USDC:USDC``) — used by
        wallet-scoped recovery to disambiguate when multiple orders are
        returned for the wallet (Task 5.2).
    :param market_key: GMX market_key (0x-prefixed address) the order was
        submitted against.  Same recovery use case as ``symbol``.
    :param side: ``"long"`` or ``"short"`` for derivatives, or ``"buy"``
        / ``"sell"`` for swaps.  Free-form; the cache does not validate.
    :param amount: Position size in the base currency the strategy uses
        (not in the market's native token).  Used by wallet recovery
        with a tolerance match.
    :param price: Submitted price (limit price for limit orders, market
        price snapshot for market orders).  Used by wallet recovery.
    :param created_at_unix: When the record was added.  Drives stale
        entry pruning on load.
    """

    order_key: str
    tx_hash: str
    symbol: str = ""
    market_key: str = ""
    side: str = ""
    amount: float = 0.0
    price: float = 0.0
    created_at_unix: int = field(default_factory=lambda: int(time.time()))


def _default_cache_dir() -> Path:
    """Same XDG-honouring location as the market catalog cache."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "eth_defi" / "gmx"


class OrderKeyCache:
    """Thread-safe disk-persisted ``tx_hash → OrderKeyRecord`` cache.

    Survives process restart so :meth:`Gmx.fetch_order` can still resolve
    a limit order's on-chain state after the bot is recycled mid-flight.

    Cache file path::

        {cache_dir}/order_keys_{chain_id}_{wallet_lower}.json

    Each wallet on each chain gets its own file — no cross-account
    contamination and operators can wipe a single wallet's state by
    deleting one file.

    :param chain_id: Numeric chain ID (42161 for Arbitrum, 43114 for
        Avalanche) — embedded in the filename.
    :param wallet: 0x-prefixed wallet address.  Lower-cased in the
        filename so the same wallet from different checksum sources hits
        the same file.
    :param cache_dir: Override the default ``~/.cache/eth_defi/gmx``.
    :param max_entry_age_seconds: Drop entries older than this on load.
        Defaults to 30 days.
    """

    def __init__(
        self,
        chain_id: int,
        wallet: str,
        *,
        cache_dir: Path | str | None = None,
        max_entry_age_seconds: int = DEFAULT_MAX_ENTRY_AGE_SECONDS,
    ) -> None:
        self.chain_id = chain_id
        self.wallet = wallet.lower()
        self.cache_dir = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
        self.max_entry_age_seconds = max_entry_age_seconds

        self._lock = threading.RLock()
        self._records: dict[str, OrderKeyRecord] = {}
        self._loaded = False

    @property
    def cache_file(self) -> Path:
        """Absolute path to this wallet's persisted cache file."""
        return self.cache_dir / f"order_keys_{self.chain_id}_{self.wallet}.json"

    def put(self, record: OrderKeyRecord) -> None:
        """Add or replace a record, then atomically persist to disk.

        Persistence is best-effort: a failure downgrades to memory-only
        and logs a WARNING.  Callers never see an exception.
        """
        with self._lock:
            self._ensure_loaded()
            self._records[record.tx_hash.lower()] = record
            self._flush_to_disk()

    def get(self, tx_hash: str) -> OrderKeyRecord | None:
        """Look up by tx_hash.  Returns ``None`` if not cached.

        Case-insensitive: tx_hashes vary between checksum / lowercase
        across providers.
        """
        with self._lock:
            self._ensure_loaded()
            return self._records.get(tx_hash.lower())

    def remove(self, tx_hash: str) -> None:
        """Drop a record and persist the deletion.

        Use when the upstream resolver confirms the order is settled or
        cancelled and the cached entry is no longer load-bearing.  Safe
        to call for a tx_hash that isn't cached.
        """
        with self._lock:
            self._ensure_loaded()
            if self._records.pop(tx_hash.lower(), None) is not None:
                self._flush_to_disk()

    def values(self) -> list[OrderKeyRecord]:
        """Snapshot of all cached records (for wallet-scoped scans)."""
        with self._lock:
            self._ensure_loaded()
            return list(self._records.values())

    def __len__(self) -> int:
        with self._lock:
            self._ensure_loaded()
            return len(self._records)

    def __contains__(self, tx_hash: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            return tx_hash.lower() in self._records

    def _ensure_loaded(self) -> None:
        """Lazy load from disk on first access.

        Lazy because cache files don't exist on first run, and we don't
        want construction-time disk IO blocking the hot path.  Must be
        called with the lock held.
        """
        if self._loaded:
            return
        self._loaded = True
        cache_file = self.cache_file
        if not cache_file.exists():
            return
        try:
            raw = json.loads(cache_file.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "order_key_cache: corrupt or unreadable cache file %s — starting empty (%s)",
                cache_file,
                exc,
            )
            return

        now = int(time.time())
        cutoff = now - self.max_entry_age_seconds
        loaded = 0
        pruned = 0
        for row in raw.get("records", []):
            try:
                rec = OrderKeyRecord(**row)
            except TypeError as exc:
                logger.debug("order_key_cache: schema mismatch on row %s — skipped (%s)", row, exc)
                continue
            if rec.created_at_unix < cutoff:
                pruned += 1
                continue
            self._records[rec.tx_hash.lower()] = rec
            loaded += 1
        if pruned:
            logger.info(
                "order_key_cache: loaded %d records from %s, pruned %d stale (older than %ds)",
                loaded,
                cache_file,
                pruned,
                self.max_entry_age_seconds,
            )
        # If we pruned anything, flush so the file no longer contains
        # rows we just dropped.  Free to fail — memory copy already correct.
        if pruned and loaded > 0:
            self._flush_to_disk()

    def _flush_to_disk(self) -> None:
        """Best-effort atomic write.  Caller holds the lock."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {
                "chain_id": self.chain_id,
                "wallet": self.wallet,
                "saved_at_unix": int(time.time()),
                "records": [asdict(r) for r in self._records.values()],
            }
            tmp = self.cache_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload))
            tmp.replace(self.cache_file)
        except OSError as exc:
            logger.warning(
                "order_key_cache: could not persist to %s — running memory-only (%s)",
                self.cache_file,
                exc,
            )
