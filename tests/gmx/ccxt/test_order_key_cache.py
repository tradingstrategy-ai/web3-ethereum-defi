"""Coverage for :mod:`eth_defi.gmx.ccxt.order_key_cache`.

Verifies the disk-persisted ``OrderKeyCache`` survives process restart,
prunes stale entries, atomically writes (no partial-file poisoning),
and degrades gracefully when disk operations fail.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest


def _rec(tx_hash="0xtx1", order_key="0xorder1", symbol="BTC/USDC:USDC", market_key="0xWBTCUSDC", side="long", amount=0.5, price=42000.0, created_at_unix=None):
    from eth_defi.gmx.ccxt.order_key_cache import OrderKeyRecord

    return OrderKeyRecord(
        order_key=order_key,
        tx_hash=tx_hash,
        symbol=symbol,
        market_key=market_key,
        side=side,
        amount=amount,
        price=price,
        created_at_unix=created_at_unix if created_at_unix is not None else int(time.time()),
    )


def _cache(tmp_path, **kwargs):
    from eth_defi.gmx.ccxt.order_key_cache import OrderKeyCache

    return OrderKeyCache(
        chain_id=42161,
        wallet="0xE3F16770C0A336103d7c24B34A4AfcBf6fb17583",
        cache_dir=tmp_path,
        **kwargs,
    )


class TestOrderKeyCacheBasics:
    def test_put_and_get_round_trip(self, tmp_path):
        cache = _cache(tmp_path)
        rec = _rec()
        cache.put(rec)
        got = cache.get("0xtx1")
        assert got is not None
        assert got.order_key == "0xorder1"
        assert got.market_key == "0xWBTCUSDC"

    def test_get_case_insensitive(self, tmp_path):
        # tx_hash casing varies between providers; cache must normalise.
        cache = _cache(tmp_path)
        cache.put(_rec(tx_hash="0xMixedCaseHash"))
        assert cache.get("0xmixedcasehash") is not None
        assert cache.get("0XMIXEDCASEHASH") is not None

    def test_get_returns_none_for_missing(self, tmp_path):
        assert _cache(tmp_path).get("0xnope") is None

    def test_remove_drops_entry(self, tmp_path):
        cache = _cache(tmp_path)
        cache.put(_rec(tx_hash="0xtx1"))
        cache.put(_rec(tx_hash="0xtx2", order_key="0xorder2"))
        cache.remove("0xtx1")
        assert cache.get("0xtx1") is None
        assert cache.get("0xtx2") is not None

    def test_contains(self, tmp_path):
        cache = _cache(tmp_path)
        cache.put(_rec(tx_hash="0xPresent"))
        assert "0xpresent" in cache
        assert "0xmissing" not in cache

    def test_len(self, tmp_path):
        cache = _cache(tmp_path)
        assert len(cache) == 0
        cache.put(_rec(tx_hash="0xa"))
        cache.put(_rec(tx_hash="0xb"))
        assert len(cache) == 2

    def test_values_returns_snapshot(self, tmp_path):
        cache = _cache(tmp_path)
        cache.put(_rec(tx_hash="0xa", order_key="0xKa"))
        cache.put(_rec(tx_hash="0xb", order_key="0xKb"))
        keys = sorted(r.order_key for r in cache.values())
        assert keys == ["0xKa", "0xKb"]


class TestOrderKeyCachePersistence:
    """The point of this whole module — survive process restart."""

    def test_new_instance_reads_existing_file(self, tmp_path):
        from eth_defi.gmx.ccxt.order_key_cache import OrderKeyCache

        c1 = _cache(tmp_path)
        c1.put(_rec(tx_hash="0xpersisted", order_key="0xkey_persisted"))

        # New instance == process restart.
        c2 = OrderKeyCache(
            chain_id=42161,
            wallet="0xE3F16770C0A336103d7c24B34A4AfcBf6fb17583",
            cache_dir=tmp_path,
        )
        got = c2.get("0xpersisted")
        assert got is not None
        assert got.order_key == "0xkey_persisted"

    def test_cache_file_path_includes_chain_and_wallet(self, tmp_path):
        from eth_defi.gmx.ccxt.order_key_cache import OrderKeyCache

        arb = OrderKeyCache(chain_id=42161, wallet="0xWALLET_A", cache_dir=tmp_path)
        avax = OrderKeyCache(chain_id=43114, wallet="0xWALLET_B", cache_dir=tmp_path)
        arb.put(_rec())
        avax.put(_rec(tx_hash="0xavax_tx"))
        # Files are distinct — wallet on different chains never collide.
        files = sorted(p.name for p in tmp_path.glob("order_keys_*.json"))
        assert "order_keys_42161_0xwallet_a.json" in files
        assert "order_keys_43114_0xwallet_b.json" in files

    def test_wallet_case_normalised_in_filename(self, tmp_path):
        from eth_defi.gmx.ccxt.order_key_cache import OrderKeyCache

        # Same wallet, different casing — must share a file.
        a = OrderKeyCache(chain_id=42161, wallet="0xABCdef", cache_dir=tmp_path)
        b = OrderKeyCache(chain_id=42161, wallet="0xabcDEF", cache_dir=tmp_path)
        a.put(_rec(tx_hash="0x1"))
        b.put(_rec(tx_hash="0x2"))
        # Lazy load means b reads what a wrote.
        assert b.get("0x1") is not None
        assert a.cache_file == b.cache_file


class TestOrderKeyCachePruning:
    def test_stale_entries_pruned_on_load(self, tmp_path):
        cache = _cache(tmp_path, max_entry_age_seconds=10)
        now = int(time.time())
        # 1h-old entry — way past the 10s TTL.
        cache.put(_rec(tx_hash="0xancient", created_at_unix=now - 3600))
        cache.put(_rec(tx_hash="0xfresh", created_at_unix=now))

        from eth_defi.gmx.ccxt.order_key_cache import OrderKeyCache

        # New instance loads file + prunes stale.
        c2 = OrderKeyCache(
            chain_id=42161,
            wallet="0xE3F16770C0A336103d7c24B34A4AfcBf6fb17583",
            cache_dir=tmp_path,
            max_entry_age_seconds=10,
        )
        assert c2.get("0xancient") is None
        assert c2.get("0xfresh") is not None

    def test_prune_persists_to_disk_too(self, tmp_path):
        """After prune-on-load, the file must reflect the prune.
        Otherwise the stale rows resurrect on the *next* reload.
        """
        cache = _cache(tmp_path, max_entry_age_seconds=10)
        now = int(time.time())
        cache.put(_rec(tx_hash="0xancient", created_at_unix=now - 3600))
        cache.put(_rec(tx_hash="0xfresh", created_at_unix=now))

        from eth_defi.gmx.ccxt.order_key_cache import OrderKeyCache

        c2 = OrderKeyCache(
            chain_id=42161,
            wallet="0xE3F16770C0A336103d7c24B34A4AfcBf6fb17583",
            cache_dir=tmp_path,
            max_entry_age_seconds=10,
        )
        c2.get("0xfresh")  # triggers load + prune + reflush

        # Read raw file: ancient row must no longer be there.
        raw = json.loads(c2.cache_file.read_text())
        tx_hashes = {r["tx_hash"] for r in raw["records"]}
        assert "0xancient" not in tx_hashes
        assert "0xfresh" in tx_hashes


class TestOrderKeyCacheResilience:
    def test_corrupt_file_starts_empty(self, tmp_path, caplog):
        cache_file = tmp_path / "order_keys_42161_0xe3f16770c0a336103d7c24b34a4afcbf6fb17583.json"
        cache_file.write_text("{not json")
        caplog.set_level(logging.WARNING, logger="eth_defi.gmx.ccxt.order_key_cache")
        cache = _cache(tmp_path)
        assert cache.get("0xanything") is None
        assert any("corrupt" in rec.message.lower() for rec in caplog.records)

    def test_disk_write_failure_keeps_memory_value(self, tmp_path, caplog):
        # Block cache_dir creation by putting a file at that path.
        blocker = tmp_path / "subdir"
        blocker.write_text("blocker")

        caplog.set_level(logging.WARNING, logger="eth_defi.gmx.ccxt.order_key_cache")
        from eth_defi.gmx.ccxt.order_key_cache import OrderKeyCache

        cache = OrderKeyCache(
            chain_id=42161,
            wallet="0xWALLET",
            cache_dir=blocker / "nested",
        )
        cache.put(_rec(tx_hash="0xmem_only"))
        # Memory has it.
        assert cache.get("0xmem_only") is not None
        # WARNING about persistence failure was emitted.
        assert any("persist" in rec.message.lower() or "could not" in rec.message.lower() for rec in caplog.records)

    def test_schema_mismatch_row_skipped(self, tmp_path, caplog):
        cache_file = tmp_path / "order_keys_42161_0xe3f16770c0a336103d7c24b34a4afcbf6fb17583.json"
        cache_file.write_text(
            json.dumps(
                {
                    "chain_id": 42161,
                    "wallet": "0xe3f16770c0a336103d7c24b34a4afcbf6fb17583",
                    "saved_at_unix": int(time.time()),
                    "records": [
                        {"order_key": "0xok", "tx_hash": "0xgood", "created_at_unix": int(time.time())},
                        {"order_key": "0xbroken"},  # missing tx_hash → schema mismatch
                    ],
                }
            )
        )
        caplog.set_level(logging.DEBUG, logger="eth_defi.gmx.ccxt.order_key_cache")
        cache = _cache(tmp_path)
        # Good row survived; bad row silently dropped.
        assert cache.get("0xgood") is not None
        assert cache.get("0xbroken") is None


class TestOrderKeyCacheAtomicWrites:
    def test_tmp_file_is_renamed_not_left_behind(self, tmp_path):
        cache = _cache(tmp_path)
        cache.put(_rec())
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []
        final = list(tmp_path.glob("order_keys_*.json"))
        assert len(final) == 1
