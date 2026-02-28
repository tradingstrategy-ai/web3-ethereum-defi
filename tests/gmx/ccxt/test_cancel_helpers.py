"""Unit tests for eth_defi.gmx.ccxt.cancel_helpers.

These tests exercise pure logic only — no blockchain RPC calls required.
"""

import pytest

from eth_defi.gmx.ccxt.cancel_helpers import build_cancel_order_response, resolve_order_id

# A valid 64-hex-char order key (32 bytes)
_VALID_KEY = "a" * 64
_VALID_KEY_0X = "0x" + _VALID_KEY


def test_resolve_order_id_bare_hex_passthrough():
    """A raw 64-char hex string with no cache entry is returned as-is."""
    resolved_id, order_key = resolve_order_id({}, _VALID_KEY)
    assert resolved_id == _VALID_KEY
    assert order_key == bytes.fromhex(_VALID_KEY)


def test_resolve_order_id_0x_prefixed_passthrough():
    """A 0x-prefixed key with no cache entry keeps its prefix in the returned string."""
    resolved_id, order_key = resolve_order_id({}, _VALID_KEY_0X)
    assert resolved_id == _VALID_KEY_0X
    assert order_key == bytes.fromhex(_VALID_KEY)


def test_resolve_order_id_tx_hash_resolves_via_cache():
    """A tx_hash stored in the cache resolves to the cached order_key."""
    tx_hash = "0xdeadbeef"
    cached_key = _VALID_KEY_0X
    cache = {tx_hash: {"info": {"order_key": cached_key}}}

    resolved_id, order_key = resolve_order_id(cache, tx_hash)
    assert resolved_id == cached_key
    assert order_key == bytes.fromhex(_VALID_KEY)


def test_resolve_order_id_bare_tx_hash_also_resolved_via_cache():
    """A tx_hash without 0x prefix is still found in cache via normalisation."""
    tx_hash_bare = "deadbeef"
    cached_key = _VALID_KEY_0X
    cache = {tx_hash_bare: {"info": {"order_key": cached_key}}}

    resolved_id, _ = resolve_order_id(cache, tx_hash_bare)
    assert resolved_id == cached_key


def test_resolve_order_id_cache_entry_without_order_key_is_ignored():
    """Cache hit where 'order_key' is absent falls through to raw id validation."""
    cache = {"0xdeadbeef": {"info": {}}}
    # _VALID_KEY is the raw_id — not a tx_hash, so it passes hex validation
    resolved_id, _ = resolve_order_id(cache, _VALID_KEY)
    assert resolved_id == _VALID_KEY


def test_resolve_order_id_invalid_key_too_short_raises():
    """A key shorter than 64 hex chars raises ValueError."""
    with pytest.raises(ValueError, match="expected 32-byte"):
        resolve_order_id({}, "0xdeadbeef")


def test_resolve_order_id_invalid_key_too_long_raises():
    """A key longer than 64 hex chars raises ValueError."""
    with pytest.raises(ValueError, match="expected 32-byte"):
        resolve_order_id({}, "a" * 65)


def _iso8601(ms: int) -> str:
    return f"2026-01-01T00:00:00.{ms:03d}Z"


def test_build_cancel_order_response_basic_structure():
    """Response contains all required CCXT fields."""
    resp = build_cancel_order_response(
        order_id=_VALID_KEY_0X,
        symbol="ETH/USDC:USDC",
        tx_hash="0xabc123",
        block_number=12345,
        timestamp_ms=1000,
        iso8601_fn=_iso8601,
    )
    assert resp["id"] == _VALID_KEY_0X
    assert resp["status"] == "cancelled"
    assert resp["symbol"] == "ETH/USDC:USDC"
    assert resp["filled"] == 0.0
    assert resp["info"]["order_key"] == _VALID_KEY_0X
    assert resp["info"]["tx_hash"] == "0xabc123"
    assert resp["info"]["block_number"] == 12345
    assert resp["timestamp"] == 1000
    assert resp["datetime"] == _iso8601(1000)


def test_build_cancel_order_response_none_symbol_allowed():
    """symbol=None and block_number=None are valid CCXT-compatible values."""
    resp = build_cancel_order_response(
        order_id=_VALID_KEY_0X,
        symbol=None,
        tx_hash="0xabc",
        block_number=None,
        timestamp_ms=0,
        iso8601_fn=_iso8601,
    )
    assert resp["symbol"] is None
    assert resp["info"]["block_number"] is None
