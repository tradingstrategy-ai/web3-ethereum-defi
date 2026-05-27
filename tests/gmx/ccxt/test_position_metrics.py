"""Regression tests for :pymod:`eth_defi.gmx.ccxt._position_metrics`.

Covers two crash-loop fixes that landed on ``fix/issue-67-no-key-resolver``:

* W1 — async/sync liquidation_price parity.  Both ``parse_position``
  (sync) and ``fetch_positions`` (async) must call
  :pyfunc:`safe_liquidation_price`.  These tests pin the helper's
  contract so neither path can silently re-introduce a wrong-side or
  zero value into the freqtrade ``Trade.liquidation_price`` column.
* W2 — explicit ``GMX_MIN_COST_USD`` guard in
  ``_convert_ccxt_to_gmx_params`` (sync) and
  ``_convert_ccxt_to_gmx_params_async`` (async).  Verifies the guard
  raises :pyexc:`ccxt.base.errors.InvalidOrder` for sub-$2 opens and
  is exempt for reduce-only closes.

Unit-level only — no network, no fork, no GMX instance construction.
The helper is a pure function; the ``_convert_ccxt_to_gmx_params``
checks bind to instance method only for ``self.markets`` lookup, so the
W2 cases mock the minimum surface (``self.markets``, ``load_markets``
no-op) rather than spinning up an anvil fork.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from ccxt.base.errors import InvalidOrder

from eth_defi.gmx.ccxt._position_metrics import safe_liquidation_price
from eth_defi.gmx.constants import GMX_MIN_COST_USD


# ---------------------------------------------------------------------------
# W1 — safe_liquidation_price helper
# ---------------------------------------------------------------------------


class TestSafeLiquidationPriceSmallStake:
    """Production crash-trigger inputs must return ``None``."""

    def test_icp_long_5usd_returns_none(self):
        """The exact production failure case (ICP/USDC 1x long, $5.05 stake).

        Pre-fix this returned ~$2.527 (0.89 % below entry $2.5522), which
        Freqtrade then padded with a 5 % buffer to ~$2.65 — exceeding
        current rate every loop, firing ``ExitType.LIQUIDATION``, and
        crashing the bot on the missing ``skip_custom_exit_price`` kwarg.
        Post-fix: ``None``.
        """
        result = safe_liquidation_price(
            entry_price=2.5522,
            collateral_usd=5.05,
            position_size_usd=5.05,
            is_long=True,
        )
        assert result is None

    def test_short_5usd_returns_none(self):
        result = safe_liquidation_price(
            entry_price=100.0,
            collateral_usd=5.05,
            position_size_usd=5.05,
            is_long=False,
        )
        assert result is None


class TestSafeLiquidationPriceRealistic:
    """Comfortable position sizes return a reliable float on the
    correct side of entry."""

    def test_long_returns_value_below_entry(self):
        result = safe_liquidation_price(
            entry_price=100.0,
            collateral_usd=1000.0,
            position_size_usd=1000.0,
            is_long=True,
        )
        assert result is not None
        assert 0 < result < 100.0

    def test_short_returns_value_above_entry(self):
        result = safe_liquidation_price(
            entry_price=100.0,
            collateral_usd=1000.0,
            position_size_usd=1000.0,
            is_long=False,
        )
        assert result is not None
        assert result > 100.0


class TestSafeLiquidationPriceDegenerateInputs:
    """Missing / zero / negative inputs must be rejected without exception."""

    @pytest.mark.parametrize(
        "entry,collateral,size",
        [
            (None, 100.0, 100.0),
            (100.0, None, 100.0),
            (100.0, 100.0, None),
            (0.0, 100.0, 100.0),
            (100.0, 0.0, 100.0),
            (100.0, 100.0, 0.0),
            (100.0, 100.0, -5.0),
        ],
    )
    def test_missing_or_zero_inputs_yield_none(self, entry, collateral, size):
        assert (
            safe_liquidation_price(
                entry_price=entry,
                collateral_usd=collateral,
                position_size_usd=size,
                is_long=True,
            )
            is None
        )


class TestSafeLiquidationPriceDirectionInvariant:
    """Belt-and-suspenders: even if the upstream helper one day returns
    a wrong-side value, ``safe_liquidation_price`` must reject it."""

    @patch("eth_defi.gmx.ccxt._position_metrics.calculate_estimated_liquidation_price")
    def test_long_wrong_side_rejected(self, mock_calc):
        # Long position, but helper returns a value AT entry — invariant says <.
        mock_calc.return_value = 100.0
        result = safe_liquidation_price(
            entry_price=100.0,
            collateral_usd=1000.0,
            position_size_usd=1000.0,
            is_long=True,
        )
        assert result is None

    @patch("eth_defi.gmx.ccxt._position_metrics.calculate_estimated_liquidation_price")
    def test_short_wrong_side_rejected(self, mock_calc):
        mock_calc.return_value = 100.0
        result = safe_liquidation_price(
            entry_price=100.0,
            collateral_usd=1000.0,
            position_size_usd=1000.0,
            is_long=False,
        )
        assert result is None

    @patch("eth_defi.gmx.ccxt._position_metrics.calculate_estimated_liquidation_price")
    def test_non_positive_rejected(self, mock_calc):
        mock_calc.return_value = 0.0
        result = safe_liquidation_price(
            entry_price=100.0,
            collateral_usd=1000.0,
            position_size_usd=1000.0,
            is_long=True,
        )
        assert result is None


class TestSyncAsyncImportLockstep:
    """Both ``exchange.py`` (sync) and ``async_support/exchange.py``
    (async) must import the SAME helper module — guards against a
    future divergence."""

    def test_sync_imports_safe_liquidation_price(self):
        from eth_defi.gmx.ccxt import exchange as sync_mod

        assert hasattr(sync_mod, "safe_liquidation_price")
        assert sync_mod.safe_liquidation_price is safe_liquidation_price

    def test_async_imports_safe_liquidation_price(self):
        from eth_defi.gmx.ccxt.async_support import exchange as async_mod

        assert hasattr(async_mod, "safe_liquidation_price")
        assert async_mod.safe_liquidation_price is safe_liquidation_price


# ---------------------------------------------------------------------------
# W2 — GMX_MIN_COST_USD guard in _convert_ccxt_to_gmx_params
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_gmx_with_stub_markets():
    """Minimal stub of the sync :pyclass:`GMX` instance that exposes
    just the surface :pymeth:`_convert_ccxt_to_gmx_params` needs:
    ``self.markets``, ``self.markets_loaded``, ``self.leverage``,
    ``self.default_slippage``, and a no-op ``load_markets``.  Avoids
    constructing the real instance (which would require an Arbitrum
    fork)."""
    from eth_defi.gmx.ccxt.exchange import GMX

    gmx = MagicMock(spec=GMX)
    gmx.markets_loaded = True
    gmx.markets = {
        "BTC/USDC:USDC": {
            "base": "BTC",
            "quote": "USDC",
            "symbol": "BTC/USDC:USDC",
        },
    }
    gmx.leverage = {}
    gmx.default_slippage = 0.003

    # Bind the real implementation so we exercise the production code path.
    gmx._convert_ccxt_to_gmx_params = GMX._convert_ccxt_to_gmx_params.__get__(gmx, GMX)
    gmx._normalize_symbol = lambda s: (s if s.endswith(":USDC") else s.replace("/USDC", "/USDC:USDC"))
    gmx._resolve_market_info = lambda symbol, params: {
        "market_token": "0x0000000000000000000000000000000000000000",
        "index_token": "0x0000000000000000000000000000000000000000",
    }
    return gmx


class TestSyncMinCostGuard:
    """Sync ``_convert_ccxt_to_gmx_params`` must reject sub-$2 opens."""

    def test_open_below_min_raises_invalid_order(self, sync_gmx_with_stub_markets):
        with pytest.raises(InvalidOrder, match="below GMX minimum"):
            sync_gmx_with_stub_markets._convert_ccxt_to_gmx_params(
                symbol="BTC/USDC:USDC",
                type="market",
                side="buy",
                amount=0.0,
                price=100000.0,
                params={"size_usd": GMX_MIN_COST_USD - 0.5},
            )

    def test_open_at_min_succeeds(self, sync_gmx_with_stub_markets):
        result = sync_gmx_with_stub_markets._convert_ccxt_to_gmx_params(
            symbol="BTC/USDC:USDC",
            type="market",
            side="buy",
            amount=0.0,
            price=100000.0,
            params={"size_usd": GMX_MIN_COST_USD},
        )
        assert result["size_delta_usd"] == GMX_MIN_COST_USD

    def test_open_above_min_succeeds(self, sync_gmx_with_stub_markets):
        result = sync_gmx_with_stub_markets._convert_ccxt_to_gmx_params(
            symbol="BTC/USDC:USDC",
            type="market",
            side="buy",
            amount=0.0,
            price=100000.0,
            params={"size_usd": 100.0},
        )
        assert result["size_delta_usd"] == 100.0

    def test_reduce_only_below_min_allowed(self, sync_gmx_with_stub_markets):
        # A dust-close reduce-only at $0.50 must NOT raise — GMX accepts
        # tiny remainder closes.
        result = sync_gmx_with_stub_markets._convert_ccxt_to_gmx_params(
            symbol="BTC/USDC:USDC",
            type="market",
            side="sell",
            amount=0.0,
            price=100000.0,
            params={
                "size_usd": 0.5,
                "reduceOnly": True,
            },
        )
        assert result["size_delta_usd"] == 0.5


class TestAsyncMinCostGuard:
    """Async ``_convert_ccxt_to_gmx_params_async`` must mirror the sync
    guard verbatim (sync/async lockstep)."""

    @pytest.mark.asyncio
    async def test_open_below_min_raises_invalid_order(self):
        from eth_defi.gmx.ccxt.async_support.exchange import GMX as AsyncGMX

        gmx = MagicMock(spec=AsyncGMX)
        gmx._convert_ccxt_to_gmx_params_async = AsyncGMX._convert_ccxt_to_gmx_params_async.__get__(gmx, AsyncGMX)
        with pytest.raises(InvalidOrder, match="below GMX minimum"):
            await gmx._convert_ccxt_to_gmx_params_async(
                symbol="BTC/USDC:USDC",
                type="market",
                side="buy",
                amount=0.0,
                price=100000.0,
                params={"size_usd": GMX_MIN_COST_USD - 0.5},
            )

    @pytest.mark.asyncio
    async def test_reduce_only_below_min_allowed(self):
        from eth_defi.gmx.ccxt.async_support.exchange import GMX as AsyncGMX

        gmx = MagicMock(spec=AsyncGMX)
        gmx._convert_ccxt_to_gmx_params_async = AsyncGMX._convert_ccxt_to_gmx_params_async.__get__(gmx, AsyncGMX)
        result = await gmx._convert_ccxt_to_gmx_params_async(
            symbol="BTC/USDC:USDC",
            type="market",
            side="sell",
            amount=0.0,
            price=100000.0,
            params={
                "size_usd": 0.5,
                "reduceOnly": True,
            },
        )
        assert result["size_delta_usd"] == 0.5

    @pytest.mark.asyncio
    async def test_open_above_min_succeeds(self):
        from eth_defi.gmx.ccxt.async_support.exchange import GMX as AsyncGMX

        gmx = MagicMock(spec=AsyncGMX)
        gmx._convert_ccxt_to_gmx_params_async = AsyncGMX._convert_ccxt_to_gmx_params_async.__get__(gmx, AsyncGMX)
        result = await gmx._convert_ccxt_to_gmx_params_async(
            symbol="BTC/USDC:USDC",
            type="market",
            side="buy",
            amount=0.0,
            price=100000.0,
            params={"size_usd": 100.0},
        )
        assert result["size_delta_usd"] == 100.0
