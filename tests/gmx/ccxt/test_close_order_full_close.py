"""Tests for :func:`eth_defi.gmx.ccxt.exchange._resolve_close_order_filled_amount`.

The helper decides what value the GMX adapter reports in the close-order
CCXT response's ``filled`` and ``amount`` fields. Prior to its introduction,
the sync adapter returned ``size_delta_usd / execution_price`` — a
token-derived value computed from the on-chain execution. On a FULL close,
that value differs from the Freqtrade-requested ``amount`` by a few wei
because GMX price impact + ``sizeInTokens`` rounding shift the on-chain
fill price slightly relative to the signal price.

Freqtrade's :func:`freqtrade.persistence.trade_model.Trade.update_trade`
(at ``trade_model.py:926-936``) uses ``isclose(filled, amount, abs_tol=1e-14)``
to decide partial-vs-full. The token-derived gap blows that tolerance →
Freqtrade flagged the successful full close as a PARTIAL fill, kept the
``Trade`` row open with a tiny residual base amount, and re-fired the exit
signal on the next bot loop. Live evidence: LINK trade #8 on 2026-05-22
closed at 01:37 for 0.30608767 LINK then again at 01:43 for 0.00107856 LINK
dust via the 6-minute Subsquid event-scanner retry path.

These tests pin both halves of the contract:

1. Full close (requested ≥ 99.5% × actual) ⇒ helper returns the
   Freqtrade-requested amount (``params['sub_trade_amt']`` or ``amount``).
   Keeps ``filled == amount`` exactly so ``update_trade`` sees a full fill.
2. Partial close (requested < 99.5% × actual) ⇒ helper returns the
   token-derived ``size_delta_usd / execution_price``. Preserves the
   pre-existing wallet-sync behaviour that avoids
   "Wallet shows X but trade has Y" warnings for partial closes where
   Freqtrade actually needs the accurate base-currency delta.

This is a complement to :func:`_resolve_reduce_only_size_delta_usd` (sized
on entry to ``create_order``) — that helper fixes the *sizing*, this one
fixes the *reporting*. Both are required for correct same-pair NT-HEDGING
parity behaviour shipped in ``feat/orchestrator-live-independent-same-pair``.
"""

from __future__ import annotations

import pytest

from eth_defi.gmx.ccxt.exchange import _resolve_close_order_filled_amount


def _position(size_usd: float) -> dict:
    """Build a ``GetOpenPositions``-shaped dict with both float and raw uint256 size."""
    return {
        "position_size": size_usd,
        "position_size_usd_raw": int(size_usd * 10**30),
    }


def test_full_close_returns_requested_amount_not_token_derived() -> None:
    """Full close (requested ≥ 99.5% × actual) returns the FT-requested amount.

    Live LINK trade #8 reproducer: Freqtrade requested ``amount = 0.30716623``
    LINK, GMX executed at a slightly different price → ``size_delta_usd /
    execution_price = 0.30608767``. The few-wei gap (~0.00108) exceeded
    ``isclose(..., abs_tol=1e-14)`` → Freqtrade treated the full close as
    partial → 6-minute dust-retry storm. Post-fix the helper returns
    0.30716623 so ``filled == amount`` exactly.
    """
    actual_position = _position(1000.0)  # $1000 LINK position

    # Freqtrade asks to close the full position.
    requested_amount = 0.30716623
    size_delta_usd = 999.5  # actual on-chain ≈ 99.95% of $1000 → full-close
    execution_price = 3.2667  # produces token-derived 0.30608767 (different from requested)

    filled = _resolve_close_order_filled_amount(
        requested_amount=requested_amount,
        size_delta_usd=size_delta_usd,
        execution_price=execution_price,
        gmx_position=actual_position,
    )

    token_derived = size_delta_usd / execution_price
    assert filled == requested_amount
    assert filled != token_derived, f"Full-close path must return requested amount ({requested_amount}), not the token-derived value ({token_derived})"


def test_partial_close_returns_token_derived_amount() -> None:
    """Partial close (requested < 99.5% × actual) returns token-derived value.

    Preserves the historical wallet-sync behaviour that avoids the
    "Wallet shows X but trade has Y" warning when Freqtrade needs the
    accurate base-currency delta for a logical partial exit.
    """
    actual_position = _position(3000.0)  # $3000 position

    # Freqtrade asks to close ~1/3 of the position.
    requested_amount = 0.30716623  # what Freqtrade thinks based on signal price
    size_delta_usd = 1000.0  # ~33% of $3000 → well below 99.5% tolerance
    execution_price = 3.2667
    token_derived = size_delta_usd / execution_price  # 306.16 / 1000 ≈ 0.30612

    filled = _resolve_close_order_filled_amount(
        requested_amount=requested_amount,
        size_delta_usd=size_delta_usd,
        execution_price=execution_price,
        gmx_position=actual_position,
    )

    assert filled == pytest.approx(token_derived, rel=1e-12)
    assert filled != requested_amount


def test_boundary_at_995_pct_uses_full_close_path() -> None:
    """At exactly the 99.5% tolerance threshold, behave as full close."""
    actual_position = _position(1000.0)

    requested_amount = 0.30716623
    size_delta_usd = 995.0  # exactly 99.5% — full-close path
    execution_price = 3.2667

    filled = _resolve_close_order_filled_amount(
        requested_amount=requested_amount,
        size_delta_usd=size_delta_usd,
        execution_price=execution_price,
        gmx_position=actual_position,
    )

    assert filled == requested_amount


def test_just_under_995_pct_uses_partial_close_path() -> None:
    """One dollar below the threshold stays in partial-close (token-derived) path."""
    actual_position = _position(1000.0)

    requested_amount = 0.30716623
    size_delta_usd = 994.0  # 99.4% — under tolerance
    execution_price = 3.2667
    token_derived = size_delta_usd / execution_price

    filled = _resolve_close_order_filled_amount(
        requested_amount=requested_amount,
        size_delta_usd=size_delta_usd,
        execution_price=execution_price,
        gmx_position=actual_position,
    )

    assert filled == pytest.approx(token_derived, rel=1e-12)


def test_missing_gmx_position_falls_back_to_requested_amount() -> None:
    """When ``gmx_position`` is None, fall back to requested amount.

    The close path always tries to fetch the position before this code
    executes; when the position has already been closed by stop-loss /
    keeper / Subsquid fallback, the position dict may be ``None``. In
    that case the on-chain decrease has already happened — Freqtrade's
    requested amount is the correct reportable value.
    """
    filled = _resolve_close_order_filled_amount(
        requested_amount=0.30716623,
        size_delta_usd=1000.0,
        execution_price=3.2667,
        gmx_position=None,
    )

    assert filled == 0.30716623


def test_zero_position_size_falls_back_to_requested_amount() -> None:
    """``position_size <= 0`` means there's no live position to compare against."""
    invalid_position = {"position_size": 0.0, "position_size_usd_raw": 0}

    filled = _resolve_close_order_filled_amount(
        requested_amount=0.30716623,
        size_delta_usd=1000.0,
        execution_price=3.2667,
        gmx_position=invalid_position,
    )

    assert filled == 0.30716623


def test_missing_execution_price_falls_back_to_requested_amount() -> None:
    """No execution_price → no token-derived value possible → return requested."""
    actual_position = _position(1000.0)

    filled = _resolve_close_order_filled_amount(
        requested_amount=0.30716623,
        size_delta_usd=1000.0,
        execution_price=None,
        gmx_position=actual_position,
    )

    assert filled == 0.30716623


def test_missing_size_delta_usd_falls_back_to_requested_amount() -> None:
    """No size_delta_usd → no token-derived value possible → return requested."""
    actual_position = _position(1000.0)

    filled = _resolve_close_order_filled_amount(
        requested_amount=0.30716623,
        size_delta_usd=0.0,
        execution_price=3.2667,
        gmx_position=actual_position,
    )

    assert filled == 0.30716623


def test_async_adapter_imports_same_helper() -> None:
    """Sync/async lockstep — both adapter modules expose the same helper.

    Mirrors the lockstep contract from
    :func:`test_async_adapter_imports_same_helper` in
    ``test_reduce_only_sizing.py``. Any future async close path will use
    identical reporting semantics.
    """
    from eth_defi.gmx.ccxt.async_support import exchange as async_exchange

    assert async_exchange._resolve_close_order_filled_amount is _resolve_close_order_filled_amount
