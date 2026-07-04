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

This module also covers :func:`eth_defi.gmx.ccxt.exchange._resolve_reduce_only_requested_size_usd`
— the entry-price repricing fix for the ~1% token-dust incident. GMX's
on-chain ``position_size`` (``sizeInUsd``) is an ENTRY-priced invariant that
never re-marks. Pricing the close request at the CURRENT market price (the
pre-fix basis) instead of ENTRY price creates a basis mismatch whenever price
has moved since entry — a short in profit or a long in loss both understate
``requested_size_usd`` relative to ``position_size``, so a genuine
100%-of-tokens close can be misclassified as PARTIAL by
:func:`_resolve_reduce_only_size_delta_usd`, leaving token dust roughly equal
to the price-move percentage. Repricing at entry removes the mismatch and is
decimals-free — no on-chain token-decimals bookkeeping is needed because both
sides of the full-close ratio are expressed in the same entry-priced USD
space.
"""

from __future__ import annotations

import pytest

from eth_defi.gmx.ccxt.exchange import (
    _resolve_close_order_filled_amount,
    _resolve_reduce_only_requested_size_usd,
    _resolve_reduce_only_size_delta_usd,
)


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


# ---------------------------------------------------------------------------
# Entry-price repricing fix (~1% token-dust incident)
# ---------------------------------------------------------------------------


def _position_with_entry(size_usd: float, entry_price: float | None, size_in_tokens: int = 255862000000000) -> dict:
    """Build a ``GetOpenPositions`` dict including ``entry_price`` and ``size_in_tokens``.

    Mirrors the real dict shape returned by ``eth_defi.gmx.core.open_positions``
    (``entry_price`` is a float USD/token present in every code path). The
    ``size_in_tokens`` default is a representative raw uint256 placeholder —
    its value is irrelevant to the fix, which is decimals-free by design.

    :param size_usd: ``position_size`` (float USD, the GMX ``sizeInUsd``).
    :param entry_price: ``entry_price`` to store on the dict, or ``None`` to
        omit the key entirely (simulates a legacy/incomplete position dict).
    :param size_in_tokens: Representative raw uint256 token size placeholder.
    """
    position = {
        "position_size": size_usd,
        "position_size_usd_raw": int(size_usd * 10**30),
        "size_in_tokens": size_in_tokens,
    }
    if entry_price is not None:
        position["entry_price"] = entry_price
    return position


def test_incident_replay_short_in_profit_full_close_reprices_at_entry() -> None:
    """Incident replay — BTC short in profit, current price below entry.

    entry_price=60689.84, current_price=60066.0, close_amount=0.00255856
    tokens (a genuine 100%-of-tokens close), position_size (sizeInUsd)=155.28.

    Pre-fix (current-price basis): 0.00255856 x 60066 = 153.68 USD;
    153.68 / 155.28 = 0.9897 < 0.995 tolerance -> misclassified PARTIAL ->
    GMX reduces only proportionally, leaving ~1.03% token dust (the
    documented 0.00002616 BTC ghost).

    Post-fix (entry-price basis, via
    :func:`_resolve_reduce_only_requested_size_usd`): 0.00255856 x 60689.84
    = 155.28 USD ~= position_size -> correctly classified FULL -> raw
    uint256 substitution -> zero dust.
    """
    entry_price = 60689.84
    current_price = 60066.0
    close_amount = 0.00255856
    position_size = 155.28
    gmx_position = _position_with_entry(position_size, entry_price)

    requested_size_usd = _resolve_reduce_only_requested_size_usd(
        close_amount=close_amount,
        gmx_position=gmx_position,
        current_price=current_price,
    )
    assert requested_size_usd == pytest.approx(close_amount * entry_price)
    assert requested_size_usd == pytest.approx(155.28, abs=0.01)

    size_delta_usd = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=requested_size_usd,
        gmx_position=gmx_position,
    )

    assert isinstance(size_delta_usd, int)
    assert size_delta_usd == gmx_position["position_size_usd_raw"]

    # Documents the bug this test guards against: the pre-fix current-price
    # basis misclassifies the same close as PARTIAL.
    buggy_requested_size_usd = close_amount * current_price
    buggy_size_delta_usd = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=buggy_requested_size_usd,
        gmx_position=gmx_position,
    )
    assert buggy_size_delta_usd == pytest.approx(buggy_requested_size_usd)
    assert buggy_size_delta_usd != gmx_position["position_size_usd_raw"]


def test_long_in_loss_full_close_reprices_at_entry() -> None:
    """Symmetric incident case — a LONG in loss (current price below entry).

    entry_price=3000.0, current_price=2700.0 (10% adverse move against the
    long), close_amount=0.9999 tokens (a genuine ~100%-of-tokens close),
    position_size=3000.0.

    Pre-fix (current-price basis): 0.9999 x 2700 = 2699.73 USD;
    2699.73 / 3000 = 0.8999 < 0.995 -> misclassified PARTIAL.

    Post-fix (entry-price basis): 0.9999 x 3000 = 2999.7 USD;
    2999.7 / 3000 = 0.9999 >= 0.995 -> correctly classified FULL.
    """
    entry_price = 3000.0
    current_price = 2700.0
    close_amount = 0.9999
    position_size = 3000.0
    gmx_position = _position_with_entry(position_size, entry_price)

    requested_size_usd = _resolve_reduce_only_requested_size_usd(
        close_amount=close_amount,
        gmx_position=gmx_position,
        current_price=current_price,
    )
    assert requested_size_usd == pytest.approx(close_amount * entry_price)

    size_delta_usd = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=requested_size_usd,
        gmx_position=gmx_position,
    )

    assert isinstance(size_delta_usd, int)
    assert size_delta_usd == gmx_position["position_size_usd_raw"]


def test_genuine_partial_close_uses_entry_priced_amount_not_current_priced() -> None:
    """A true ~50% partial close must use the entry-priced amount, not current-priced.

    entry_price=1000.0, current_price=1200.0 (price rallied since entry),
    close_amount=0.5 tokens (half the 1.0-token position), position_size=1000.0.

    Entry-priced basis: 0.5 x 1000 = 500.0 -> correctly ~50% of position_size.
    Current-priced basis would have given 0.5 x 1200 = 600.0 -> wrong value
    (not proportional to the actual token fraction closed).
    """
    entry_price = 1000.0
    current_price = 1200.0
    close_amount = 0.5
    position_size = 1000.0
    gmx_position = _position_with_entry(position_size, entry_price)

    requested_size_usd = _resolve_reduce_only_requested_size_usd(
        close_amount=close_amount,
        gmx_position=gmx_position,
        current_price=current_price,
    )
    assert requested_size_usd == pytest.approx(500.0)
    assert requested_size_usd != pytest.approx(close_amount * current_price)

    size_delta_usd = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=requested_size_usd,
        gmx_position=gmx_position,
    )

    assert size_delta_usd == pytest.approx(500.0)
    assert not isinstance(size_delta_usd, int)


def test_dca_sub_trade_amt_small_partial_unaffected() -> None:
    """A small DCA ``sub_trade_amt`` partial close remains correctly partial.

    entry_price=1000.0, current_price=1050.0, sub_trade_amt=1.0 token out of
    a 10-token (10000 USD) position -> a genuine 10% partial close.
    """
    entry_price = 1000.0
    current_price = 1050.0
    sub_trade_amt = 1.0
    position_size = 10000.0
    gmx_position = _position_with_entry(position_size, entry_price)

    requested_size_usd = _resolve_reduce_only_requested_size_usd(
        close_amount=sub_trade_amt,
        gmx_position=gmx_position,
        current_price=current_price,
    )
    assert requested_size_usd == pytest.approx(1000.0)

    size_delta_usd = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=requested_size_usd,
        gmx_position=gmx_position,
    )

    assert size_delta_usd == pytest.approx(1000.0)
    assert not isinstance(size_delta_usd, int)


@pytest.mark.parametrize("missing_entry_price", [None, 0, 0.0])
def test_entry_price_missing_or_zero_falls_back_to_current_price(missing_entry_price) -> None:
    """``entry_price`` missing/``None``/``0`` must fall back to ``current_price`` without crashing."""
    current_price = 100.0
    close_amount = 2.0
    gmx_position = _position_with_entry(500.0, missing_entry_price)
    if missing_entry_price == 0 or missing_entry_price == 0.0:
        # Exercise the explicit key-present-but-falsy path too.
        gmx_position["entry_price"] = missing_entry_price

    requested_size_usd = _resolve_reduce_only_requested_size_usd(
        close_amount=close_amount,
        gmx_position=gmx_position,
        current_price=current_price,
    )

    assert requested_size_usd == pytest.approx(close_amount * current_price)


def test_price_flat_full_close_still_full_no_regression() -> None:
    """When current price equals entry price, full-close classification is unchanged."""
    entry_price = current_price = 50000.0
    close_amount = 0.9999
    position_size = 50000.0
    gmx_position = _position_with_entry(position_size, entry_price)

    requested_size_usd = _resolve_reduce_only_requested_size_usd(
        close_amount=close_amount,
        gmx_position=gmx_position,
        current_price=current_price,
    )
    assert requested_size_usd == pytest.approx(close_amount * entry_price)

    size_delta_usd = _resolve_reduce_only_size_delta_usd(
        requested_size_usd=requested_size_usd,
        gmx_position=gmx_position,
    )

    assert isinstance(size_delta_usd, int)
    assert size_delta_usd == gmx_position["position_size_usd_raw"]


def test_resolve_close_order_filled_amount_reports_requested_amount_once_entry_priced() -> None:
    """Once sizing sends a FULL decrease (entry-priced), fill reporting already reports requested_amount.

    Complement to the incident-replay test above: once
    :func:`_resolve_reduce_only_requested_size_usd` reprices the close request
    at entry, the keeper executes a full decrease and the trade-action
    ``sizeDeltaUsd`` it reports back is ~= the full ``position_size``. This
    test pins that :func:`_resolve_close_order_filled_amount` already
    classifies that as a full close and returns ``requested_amount`` — no
    change to that helper is required.
    """
    requested_amount = 0.00255856
    entry_price = 60689.84
    position_size = 155.28
    gmx_position = _position_with_entry(position_size, entry_price)

    # The on-chain execution price at fill time (current market price),
    # independent of the entry-priced sizing basis used to build the order.
    execution_price = 60066.0
    # size_delta_usd as reported by the keeper trade-action event, now
    # computed from the entry-priced request rather than the current-priced one.
    size_delta_usd = requested_amount * entry_price

    filled_amount = _resolve_close_order_filled_amount(
        requested_amount=requested_amount,
        size_delta_usd=size_delta_usd,
        execution_price=execution_price,
        gmx_position=gmx_position,
    )

    assert filled_amount == requested_amount
