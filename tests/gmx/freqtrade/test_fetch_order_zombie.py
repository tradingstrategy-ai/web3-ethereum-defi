"""Regression tests for the wrapper-level zombie heuristic.

See tradingstrategy-ai/gmx-strategies#67.

The wrapper-level :meth:`Gmx.fetch_order` keeps a 10-minute "zombie" cutoff
for market orders only. Limit / stopLoss / take_profit orders are designed
to sit ``open`` until their trigger fires and must never be force-cancelled
by age. A 1-year sanity ceiling additionally protects against synthetic
timestamps from cache-miss paths (companion bug, fixed in PR #1001).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

# Freqtrade is an optional sibling install (lives in the parent
# ``gmx-strategies-livebt`` venv, not the web3-ethereum-defi venv). Skip
# the whole module gracefully when it is not fully importable so the suite
# remains runnable in either environment.
#
# Probe ``freqtrade.enums`` specifically: ``eth_defi.gmx.freqtrade.gmx_exchange``
# does ``from freqtrade.enums import MarginMode, TradingMode`` at module top, so
# checking only the top-level ``freqtrade`` package is insufficient when CI has
# a partial / namespace install that satisfies ``import freqtrade`` but not the
# submodule import the wrapper needs (observed on the upstream GMX Tests
# workflow which installs the package without the ``freqtrade`` extra).
pytest.importorskip("freqtrade.enums", reason="freqtrade.enums required (install with --extras freqtrade)")


def _apply_zombie_inplace(order: dict, pair: str = "BTC/USDC:USDC") -> dict:
    """Invoke the wrapper's fetch_order in isolation.

    Bypasses Gmx.__init__ and stubs the parent Exchange.fetch_order to return
    the supplied order dict verbatim, so the wrapper's zombie logic is the
    only thing exercised.
    """
    from eth_defi.gmx.freqtrade.gmx_exchange import Gmx

    fake = Gmx.__new__(Gmx)
    with patch(
        "eth_defi.gmx.freqtrade.gmx_exchange.Exchange.fetch_order",
        return_value=order,
    ):
        return fake.fetch_order(order["id"], pair)


def test_market_order_older_than_10min_is_zombied(fake_order_factory):
    eleven_min_ago = int((datetime.now(timezone.utc) - timedelta(minutes=11)).timestamp() * 1000)
    order = fake_order_factory(order_type="market", timestamp_ms=eleven_min_ago)
    result = _apply_zombie_inplace(order)
    assert result["status"] == "cancelled"
    assert result["info"]["gmx_status"] == "zombie_cancelled"


def test_limit_order_older_than_10min_is_NOT_zombied(fake_order_factory):
    """PR #1000 fix: limit orders sit open until trigger; they must not be killed at 10 min."""
    eleven_min_ago = int((datetime.now(timezone.utc) - timedelta(minutes=11)).timestamp() * 1000)
    order = fake_order_factory(order_type="limit", timestamp_ms=eleven_min_ago)
    result = _apply_zombie_inplace(order)
    assert result["status"] == "open"
    assert "gmx_status" not in result.get("info", {})


def test_stoploss_order_is_NOT_zombied(fake_order_factory):
    eleven_min_ago = int((datetime.now(timezone.utc) - timedelta(minutes=11)).timestamp() * 1000)
    order = fake_order_factory(order_type="stopLoss", timestamp_ms=eleven_min_ago)
    result = _apply_zombie_inplace(order)
    assert result["status"] == "open"


def test_market_order_with_implausible_age_is_NOT_zombied(fake_order_factory):
    """Sanity ceiling: implausible ages (>1 year) come from synthetic data, not real time.

    Belt for Bug B (companion PR #1001) — even after that PR fixes the
    timestamp source, this guards against any future regression that
    re-introduces an oversized timestamp.
    """
    ten_years_ago = int((datetime.now(timezone.utc) - timedelta(days=365 * 10)).timestamp() * 1000)
    order = fake_order_factory(order_type="market", timestamp_ms=ten_years_ago)
    result = _apply_zombie_inplace(order)
    assert result["status"] == "open"   # left untouched, NOT cancelled
