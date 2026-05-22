"""Tests for :func:`eth_defi.gmx.ccxt.exchange._resolve_reduce_only_size_delta_usd`.

The helper is the load-bearing decision point for reduce-only close sizing.
Prior to its introduction, the sync adapter substituted the external GMX net
position size whenever ``params['sub_trade_amt']`` was absent — which made it
impossible to close a single logical Freqtrade ``Trade`` row when multiple
trades shared one GMX market position (the NT-HEDGING parity case in
``feat/orchestrator-live-independent-same-pair`` downstream of this fix).

These tests pin both halves of the contract:

1. Partial requested close ⇒ helper returns the request (capped at actual size
   by ``min`` for defence in depth).
2. Effectively-full requested close (≥ 99.5% of actual) ⇒ helper returns the
   raw uint256 size for dust prevention — preserving the historical behaviour
   for true full closes.
"""

from __future__ import annotations

import pytest

from eth_defi.gmx.ccxt.exchange import _resolve_reduce_only_size_delta_usd


def _position(size_usd: float) -> dict:
    """Build a ``GetOpenPositions`` dict with both float and raw uint256 size."""
    return {
        "position_size": size_usd,
        "position_size_usd_raw": int(size_usd * 10**30),
    }


def test_reduce_only_close_uses_requested_trade_amount_when_less_than_net_position() -> None:
    """Logical per-trade close (1/3 of the on-chain position) must NOT flatten the position.

    Pre-fix behaviour: with ``sub_trade_amt`` absent, the sync adapter
    substituted the external net GMX position size — silently flattening
    sibling logical trades. Post-fix the helper returns the requested amount.
    """
    actual_position = _position(3000.0)

    size_delta_usd = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=1000.0,
        gmx_position=actual_position,
    )

    assert size_delta_usd == 1000.0
    assert size_delta_usd != actual_position["position_size_usd_raw"]
    assert size_delta_usd != actual_position["position_size"]


def test_reduce_only_close_uses_raw_position_size_when_request_matches_full_position() -> None:
    """A request within the full-close tolerance band still uses the raw uint256.

    Preserves the historical dust-prevention behaviour: closing the full
    position via ``Trade.amount`` (which translates to ~100% of actual size)
    must use the raw int to avoid ``int(float * 10^30)`` overshooting and
    triggering a GMX ``InvalidDecreaseOrderSize`` revert.
    """
    actual_position = _position(3000.0)

    size_delta_usd = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=2999.0,  # 99.97% — well above default 0.995 tolerance
        gmx_position=actual_position,
    )

    assert size_delta_usd == actual_position["position_size_usd_raw"]
    assert isinstance(size_delta_usd, int)


def test_reduce_only_close_exactly_full_request_returns_raw_int() -> None:
    """Requesting exactly the actual position size returns the raw uint256."""
    actual_position = _position(3000.0)

    size_delta_usd = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=3000.0,
        gmx_position=actual_position,
    )

    assert size_delta_usd == actual_position["position_size_usd_raw"]


def test_reduce_only_close_request_above_actual_caps_at_raw_int() -> None:
    """Even if the caller over-asks, helper never returns > actual position size.

    A buggy caller passing more than the on-chain size must NOT cause GMX to
    revert with ``InvalidDecreaseOrderSize`` from float overshoot — the raw
    int substitution kicks in once the request crosses the tolerance band.
    """
    actual_position = _position(3000.0)

    size_delta_usd = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=4500.0,
        gmx_position=actual_position,
    )

    assert size_delta_usd == actual_position["position_size_usd_raw"]


def test_reduce_only_close_at_tolerance_boundary_returns_raw_int() -> None:
    """At exactly the tolerance threshold (default 0.995), behave as full close."""
    actual_position = _position(3000.0)
    boundary_request = 3000.0 * 0.995  # 2985.0

    size_delta_usd = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=boundary_request,
        gmx_position=actual_position,
    )

    assert size_delta_usd == actual_position["position_size_usd_raw"]


def test_reduce_only_close_just_under_tolerance_returns_requested() -> None:
    """One bp below the tolerance threshold stays in partial-close path."""
    actual_position = _position(3000.0)
    just_below = 3000.0 * 0.995 - 1.0  # 2984.0

    size_delta_usd = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=just_below,
        gmx_position=actual_position,
    )

    assert size_delta_usd == just_below
    assert isinstance(size_delta_usd, float)


def test_reduce_only_close_invalid_position_returns_requested_unchanged() -> None:
    """``position_size <= 0`` (already closed / invalid) returns request as-is.

    The calling ``create_order`` path will surface the resulting
    ``InvalidDecreaseOrderSize`` revert if the position really is gone — this
    helper's job is sizing, not gating.
    """
    invalid_position = {"position_size": 0.0, "position_size_usd_raw": 0}

    size_delta_usd = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=500.0,
        gmx_position=invalid_position,
    )

    assert size_delta_usd == 500.0


def test_reduce_only_close_missing_raw_uses_float_actual_as_full_close() -> None:
    """When ``position_size_usd_raw`` is absent, full-close path returns the float.

    Older GMX SDK paths may not populate ``position_size_usd_raw``; the helper
    must still satisfy the full-close contract using the float field.
    """
    legacy_position = {"position_size": 3000.0}  # no _raw field

    size_delta_usd = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=3000.0,
        gmx_position=legacy_position,
    )

    assert size_delta_usd == 3000.0


def test_reduce_only_close_custom_tolerance_band() -> None:
    """The tolerance band is parameterised — tighter band keeps partial-close active longer."""
    actual_position = _position(3000.0)

    # With default 0.995, a 99% close (2970) would still be partial.
    size_default = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=2970.0,
        gmx_position=actual_position,
    )
    assert size_default == 2970.0

    # With a looser 0.95 tolerance, the same request crosses into full-close.
    size_loose = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=2970.0,
        gmx_position=actual_position,
        full_close_tolerance=0.95,
    )
    assert size_loose == actual_position["position_size_usd_raw"]


def test_async_adapter_imports_same_helper() -> None:
    """Sync/async lockstep — both adapter modules expose the same helper.

    The eth_defi.gmx.ccxt.async_support.exchange module must import the same
    helper so a future async close path uses identical sizing semantics.
    """
    from eth_defi.gmx.ccxt.async_support import exchange as async_exchange

    assert async_exchange._resolve_reduce_only_size_delta_usd is _resolve_reduce_only_size_delta_usd
