"""Unit tests for the ``_patch_cached_order_key`` cache helper.

When ``Gmx.fetch_order`` recovers a missing ``order_key`` from a live
DataStore pending order or a historical event, it must back-patch the
CCXT-layer order cache so the next poll resolves through the normal
order-key path instead of re-triggering the no-key reconciler.

The helper is intentionally simple: in-place update of ``info["order_key"]``
on every alias under which the cache may have stored the order
(``"0x{hash}"``, ``"{hash}"``).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _fake_sync_exchange() -> "object":
    """Build a sync ``GMX`` instance with the cache helper attached, bypassing __init__."""
    from eth_defi.gmx.ccxt.exchange import GMX

    fake = GMX.__new__(GMX)
    fake._orders = {}
    return fake


def _fake_async_exchange() -> "object":
    """Async counterpart — kept in lockstep per the sync/async invariant."""
    from eth_defi.gmx.ccxt.async_support.exchange import GMX as AsyncGMX

    fake = AsyncGMX.__new__(AsyncGMX)
    fake._orders = {}
    return fake


class TestPatchCachedOrderKeySync:
    def test_patches_both_prefixed_and_bare_aliases(self):
        gmx = _fake_sync_exchange()
        order = {"id": "0xcreationtx", "info": {}}
        # Real callers cache under both forms (see exchange.py:8542
        # ``_cache_key = id if id in self._orders else id.removeprefix("0x")``).
        gmx._orders["0xcreationtx"] = order
        gmx._orders["creationtx"] = order

        gmx._patch_cached_order_key("0xcreationtx", "0xorderkey")

        assert gmx._orders["0xcreationtx"]["info"]["order_key"] == "0xorderkey"
        assert gmx._orders["creationtx"]["info"]["order_key"] == "0xorderkey"

    def test_patches_when_only_bare_alias_is_cached(self):
        gmx = _fake_sync_exchange()
        order = {"id": "creationtx", "info": {}}
        gmx._orders["creationtx"] = order

        gmx._patch_cached_order_key("0xcreationtx", "0xorderkey")

        assert gmx._orders["creationtx"]["info"]["order_key"] == "0xorderkey"

    def test_patches_when_only_prefixed_alias_is_cached(self):
        gmx = _fake_sync_exchange()
        order = {"id": "0xcreationtx", "info": {}}
        gmx._orders["0xcreationtx"] = order

        gmx._patch_cached_order_key("creationtx", "0xorderkey")

        assert gmx._orders["0xcreationtx"]["info"]["order_key"] == "0xorderkey"

    def test_missing_info_dict_is_created(self):
        # Defensive: cached order shape that never had ``info`` set must
        # not raise; we want a no-op-or-create semantics, not a KeyError.
        gmx = _fake_sync_exchange()
        gmx._orders["0xcreationtx"] = {"id": "0xcreationtx"}

        gmx._patch_cached_order_key("0xcreationtx", "0xorderkey")

        assert gmx._orders["0xcreationtx"]["info"]["order_key"] == "0xorderkey"

    def test_no_op_when_id_not_in_cache(self):
        # No matching alias: silently does nothing. The historical resolver
        # may run before the order is actually cached (e.g. recovery path),
        # so raising would break the soft-fallback contract.
        gmx = _fake_sync_exchange()

        gmx._patch_cached_order_key("0xnothing", "0xorderkey")

        assert gmx._orders == {}


class TestPatchCachedOrderKeyAsync:
    """Mirror tests on the async adapter — sync/async lockstep invariant."""

    def test_patches_both_aliases_async(self):
        gmx = _fake_async_exchange()
        order = {"id": "0xcreationtx", "info": {}}
        gmx._orders["0xcreationtx"] = order
        gmx._orders["creationtx"] = order

        gmx._patch_cached_order_key("0xcreationtx", "0xorderkey")

        assert gmx._orders["0xcreationtx"]["info"]["order_key"] == "0xorderkey"
        assert gmx._orders["creationtx"]["info"]["order_key"] == "0xorderkey"
