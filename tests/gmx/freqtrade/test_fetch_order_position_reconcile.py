"""Regression tests for on-chain position reconciliation in ``Gmx.fetch_order``.

When the parent ``fetch_order`` returns ``status="open"`` for a limit / stop /
take-profit order, the wrapper cross-checks against ``fetch_positions`` and
flips the order to ``"closed"`` if a matching on-chain position exists.  This
catches the BONK / SHIB stuck-order case where Subsquid 4xx errors blind the
primary resolver to a keeper-executed fill.

See tradingstrategy-ai/gmx-strategies#67 and the 2026-05-14 logs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("freqtrade.enums", reason="freqtrade.enums required for these tests")


def _fake_gmx_with_positions(positions: list[dict]):
    """Build a ``Gmx`` instance with ``_api.fetch_positions`` mocked to return
    the supplied position list.  Bypasses ``Gmx.__init__`` so the test does
    not need a live web3 / freqtrade config.
    """
    from eth_defi.gmx.freqtrade.gmx_exchange import Gmx

    fake = Gmx.__new__(Gmx)
    fake._api = MagicMock()
    fake._api.fetch_positions = MagicMock(return_value=positions)
    return fake


def _limit_order(side: str = "buy", amount: float = 1.0, status: str = "open", order_id: str = "0xabc", pair_in_order: str = "BTC/USDC:USDC") -> dict:
    """Limit-order CCXT shape.  The reconciler ignores ``type=='market'``,
    so the test default is ``limit`` — the case that matters for Issue B.
    """
    from datetime import datetime, timezone

    return {
        "id": order_id,
        "type": "limit",
        "status": status,
        "side": side,
        "amount": amount,
        "filled": 0.0,
        "remaining": amount,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "info": {},
        "symbol": pair_in_order,
    }


def _position(symbol: str, side: str, contracts: float, position_key: str = "0xpos1") -> dict:
    """CCXT-shaped position dict matching ``fetch_positions`` output."""
    return {
        "symbol": symbol,
        "side": side,
        "contracts": contracts,
        "id": position_key,
    }


def _invoke_fetch_order(fake_gmx, order: dict, pair: str = "BTC/USDC:USDC") -> dict:
    """Run ``Gmx.fetch_order`` with the parent ``Exchange.fetch_order`` stubbed
    to return ``order`` verbatim.  Isolates the wrapper logic.
    """
    with patch(
        "eth_defi.gmx.freqtrade.gmx_exchange.Exchange.fetch_order",
        return_value=order,
    ):
        return fake_gmx.fetch_order(order["id"], pair)


class TestReconcileViaPositions:
    def test_limit_buy_with_matching_long_position_flips_to_closed(self):
        # BONK/SHIB-style stuck-trade scenario: resolver says still "open",
        # on-chain there's a matching long position → order has filled.
        positions = [_position("BTC/USDC:USDC", side="long", contracts=1.0)]
        gmx = _fake_gmx_with_positions(positions)

        result = _invoke_fetch_order(gmx, _limit_order(side="buy", amount=1.0))
        assert result["status"] == "closed"
        assert result["filled"] == 1.0
        assert result["remaining"] == 0.0
        # Info carries the reconciliation breadcrumb for audit logs.
        assert result["info"]["reconciled_via_position"] is True
        assert result["info"]["reconciled_position_size"] == 1.0

    def test_limit_sell_with_matching_short_position_flips_to_closed(self):
        # Order side "sell" maps to position side "short".
        positions = [_position("BTC/USDC:USDC", side="short", contracts=0.5)]
        gmx = _fake_gmx_with_positions(positions)

        result = _invoke_fetch_order(gmx, _limit_order(side="sell", amount=0.5))
        assert result["status"] == "closed"

    def test_no_matching_position_returns_original_open_order(self):
        # Empty positions list → order stays "open" (correct: it's genuinely pending).
        gmx = _fake_gmx_with_positions([])
        result = _invoke_fetch_order(gmx, _limit_order())
        assert result["status"] == "open"
        # No reconciliation marker on the info dict.
        assert "reconciled_via_position" not in result.get("info", {})

    def test_position_for_different_pair_does_not_match(self):
        # Position is on ETH/USDC:USDC but the order is on BTC/USDC:USDC.
        positions = [_position("ETH/USDC:USDC", side="long", contracts=1.0)]
        gmx = _fake_gmx_with_positions(positions)
        result = _invoke_fetch_order(gmx, _limit_order())
        assert result["status"] == "open"

    def test_position_with_opposite_side_does_not_match(self):
        # Order is buy (expects long), but on-chain has a short → not our order.
        positions = [_position("BTC/USDC:USDC", side="short", contracts=1.0)]
        gmx = _fake_gmx_with_positions(positions)
        result = _invoke_fetch_order(gmx, _limit_order(side="buy"))
        assert result["status"] == "open"

    def test_position_size_within_tolerance_matches(self):
        # 0.4% drift — under the 0.5% tolerance ceiling.
        positions = [_position("BTC/USDC:USDC", side="long", contracts=0.996)]
        gmx = _fake_gmx_with_positions(positions)
        result = _invoke_fetch_order(gmx, _limit_order(side="buy", amount=1.0))
        assert result["status"] == "closed"

    def test_position_size_outside_tolerance_does_not_match(self):
        # 1% drift exceeds the 0.5% tolerance → treat as different position.
        positions = [_position("BTC/USDC:USDC", side="long", contracts=0.99)]
        gmx = _fake_gmx_with_positions(positions)
        result = _invoke_fetch_order(gmx, _limit_order(side="buy", amount=1.0))
        assert result["status"] == "open"

    def test_market_order_skips_reconciliation(self):
        # Market orders are handled by the zombie path, not reconciliation.
        # Even with a matching position, the wrapper must not flip status.
        from datetime import datetime, timezone

        positions = [_position("BTC/USDC:USDC", side="long", contracts=1.0)]
        gmx = _fake_gmx_with_positions(positions)
        market_order = {
            "id": "0xmkt",
            "type": "market",
            "status": "open",
            "side": "buy",
            "amount": 1.0,
            "filled": 0.0,
            "remaining": 1.0,
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "info": {},
            "symbol": "BTC/USDC:USDC",
        }
        result = _invoke_fetch_order(gmx, market_order)
        # The reconciler must not have been called — fetch_positions stays untouched.
        # (Zombie path may have flipped status to cancelled if the timestamp is old,
        # but the reconciler-specific marker must be absent.)
        assert "reconciled_via_position" not in result.get("info", {})

    def test_already_closed_order_skips_reconciliation(self):
        positions = [_position("BTC/USDC:USDC", side="long", contracts=1.0)]
        gmx = _fake_gmx_with_positions(positions)
        order = _limit_order()
        order["status"] = "closed"
        result = _invoke_fetch_order(gmx, order)
        # Reconciler not invoked — status was already closed.
        assert "reconciled_via_position" not in result.get("info", {})

    def test_fetch_positions_exception_does_not_break_fetch_order(self, caplog):
        # Soft fallback: a failure in fetch_positions must not change the
        # original order's contract.
        import logging

        from eth_defi.gmx.freqtrade.gmx_exchange import Gmx

        fake = Gmx.__new__(Gmx)
        fake._api = MagicMock()
        fake._api.fetch_positions = MagicMock(side_effect=RuntimeError("RPC down"))

        caplog.set_level(logging.DEBUG, logger="eth_defi.gmx.freqtrade.gmx_exchange")
        result = _invoke_fetch_order(fake, _limit_order())
        # Order unchanged.
        assert result["status"] == "open"
        # DEBUG log records the soft failure for audit.
        assert any("fetch_positions" in rec.message.lower() for rec in caplog.records)

    def test_zero_amount_order_does_not_match(self):
        positions = [_position("BTC/USDC:USDC", side="long", contracts=1.0)]
        gmx = _fake_gmx_with_positions(positions)
        order = _limit_order(amount=0.0)
        result = _invoke_fetch_order(gmx, order)
        # An order with zero amount cannot meaningfully match a position.
        assert result["status"] == "open"
