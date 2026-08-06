"""Reduce-only closes sized in USD rather than in tokens.

``size_usd`` sizes an open, but the reduce-only path derived its size from the
token ``amount`` alone. A caller passing ``amount=0`` with ``size_usd`` therefore
priced zero tokens and the close failed outright with "Invalid close size 0.0".

Closing in USD is now supported, which in turn means the CCXT response can no
longer take the caller's token ``amount`` at face value: it is zero on exactly
this path, and reporting ``filled=0`` for a position that was fully closed on
chain would tell any downstream consumer the close never happened.
"""

from __future__ import annotations

import pytest

from eth_defi.gmx.ccxt.exchange import _resolve_close_order_filled_amount  # noqa: PLC2701

#: A $5 long closed at $1880/ETH — roughly the testnet position these tests model.
_SIZE_DELTA_USD = 5.0
_EXECUTION_PRICE = 1880.0
_EXPECTED_TOKENS = _SIZE_DELTA_USD / _EXECUTION_PRICE

#: ``GetOpenPositions`` dict for a position of exactly that size.
_FULL_POSITION = {"position_size": 5.0}


def test_zero_requested_amount_reports_token_derived_amount():
    """A USD-sized full close must not report ``filled=0``.

    The caller passed no token amount, so there is nothing to echo back. Echoing
    the zero would mark a fully closed position as unfilled.
    """
    resolved = _resolve_close_order_filled_amount(
        requested_amount=0,
        size_delta_usd=_SIZE_DELTA_USD,
        execution_price=_EXECUTION_PRICE,
        gmx_position=_FULL_POSITION,
    )

    assert resolved == pytest.approx(_EXPECTED_TOKENS)
    assert resolved > 0


def test_zero_requested_amount_without_position_still_reports_amount():
    """The zero-amount guard must not depend on position data being available."""
    resolved = _resolve_close_order_filled_amount(
        requested_amount=0,
        size_delta_usd=_SIZE_DELTA_USD,
        execution_price=_EXECUTION_PRICE,
        gmx_position=None,
    )

    assert resolved == pytest.approx(_EXPECTED_TOKENS)


def test_usd_sized_open_also_reports_a_real_amount():
    """USD-sized *opens* are covered too, deliberately.

    An open sized via ``size_usd`` with ``amount=0`` ("Approach 2" in
    :meth:`GMX.create_order`) reaches this helper with ``gmx_position=None``,
    because ``_gmx_position`` is only populated for closes. The zero-amount guard
    sits ahead of that fallback, so such opens now report the token-derived amount
    instead of echoing the caller's zero.

    This is a deliberate widening of the original close-side fix — those opens
    previously reported ``filled=0`` for an order that executed, which was the
    same untruth. Pinned here so it stays intentional.
    """
    resolved = _resolve_close_order_filled_amount(
        requested_amount=0,
        size_delta_usd=1000.0,
        execution_price=_EXECUTION_PRICE,
        gmx_position=None,
    )

    assert resolved == pytest.approx(1000.0 / _EXECUTION_PRICE)


def test_open_passing_a_token_amount_is_unchanged():
    """Opens that do supply a token amount still echo it verbatim."""
    resolved = _resolve_close_order_filled_amount(
        requested_amount=0.53,
        size_delta_usd=1000.0,
        execution_price=_EXECUTION_PRICE,
        gmx_position=None,
    )

    assert resolved == 0.53


def test_full_close_still_echoes_a_supplied_token_amount():
    """Token-sized full closes keep echoing the requested amount verbatim.

    Freqtrade compares ``isclose(filled, amount, abs_tol=1e-14)`` to decide
    whether a close was full; returning the token-derived value here would differ
    by a few wei and be misread as a partial fill, leaving a dust residual.
    """
    requested = 0.00265798
    resolved = _resolve_close_order_filled_amount(
        requested_amount=requested,
        size_delta_usd=_SIZE_DELTA_USD,
        execution_price=_EXECUTION_PRICE,
        gmx_position=_FULL_POSITION,
    )

    assert resolved == requested


def test_partial_close_still_reports_token_derived_amount():
    """Partial closes keep reporting the on-chain delta for wallet sync."""
    resolved = _resolve_close_order_filled_amount(
        requested_amount=0.01,
        size_delta_usd=_SIZE_DELTA_USD,
        execution_price=_EXECUTION_PRICE,
        gmx_position={"position_size": 100.0},
    )

    assert resolved == pytest.approx(_EXPECTED_TOKENS)


def test_zero_amount_with_no_execution_price_falls_back():
    """Without an execution price there is no token-derived value to report."""
    resolved = _resolve_close_order_filled_amount(
        requested_amount=0,
        size_delta_usd=_SIZE_DELTA_USD,
        execution_price=None,
        gmx_position=_FULL_POSITION,
    )

    assert resolved == 0
