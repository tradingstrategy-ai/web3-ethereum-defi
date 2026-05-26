"""Shared position-metrics helpers for the GMX CCXT adapter.

Both :pyfile:`eth_defi/gmx/ccxt/exchange.py` (sync ``parse_position``) and
:pyfile:`eth_defi/gmx/ccxt/async_support/exchange.py` (async
``fetch_positions``) need to compute a liquidation price for every open
position they report.  Before this module existed they diverged: the sync
path called :pyfunc:`calculate_estimated_liquidation_price` and validated
the result, while the async path hardcoded ``None``.  That divergence
masked the May-2026 production crash regression and the lockstep rule in
``feedback_eth_defi_sync_async_lockstep`` requires the same code on both
sides.

Centralising the call here lets both paths reuse one validation chain
(input sanity → helper call → direction invariant → non-positive guard)
without code duplication or import cycles.

:see: :pyfunc:`eth_defi.gmx.utils.calculate_estimated_liquidation_price`
"""

from __future__ import annotations

from eth_defi.gmx.utils import calculate_estimated_liquidation_price


def safe_liquidation_price(
    *,
    entry_price: float | None,
    collateral_usd: float | None,
    position_size_usd: float | None,
    is_long: bool,
) -> float | None:
    """Compute a sanity-checked liquidation price for a GMX position.

    Thin wrapper around :pyfunc:`calculate_estimated_liquidation_price`
    that rejects any result violating the direction invariant (a long's
    liquidation must sit below entry, a short's above) or that is
    non-positive.  Returns ``None`` whenever:

    - any of the three USD inputs is falsy / missing,
    - ``position_size_usd`` is non-positive,
    - the underlying helper returned ``None`` (small positions dominated
      by the GMX min-collateral floor, degenerate inputs),
    - the produced value would be on the wrong side of ``entry_price``
      (defensive belt-and-suspenders against any future refactor).

    Freqtrade's ``stoploss_or_liquidation`` treats ``None`` as "unknown"
    and skips the check, but treats *any* non-zero number — including a
    stale or wrong-side value — as a real liquidation level.  Returning
    a bogus float here is what caused the production crash-loop, hence
    the conservative None-on-doubt policy.

    Both ``parse_position`` (sync) and ``fetch_positions`` (async) MUST
    delegate liquidation-price computation to this helper instead of
    inlining it, so the two paths cannot diverge again.

    :param entry_price: Position entry price in USD.
    :param collateral_usd: Initial collateral amount in USD.
    :param position_size_usd: Position size in USD.
    :param is_long: Direction flag — ``True`` for longs, ``False`` for shorts.
    :returns: Liquidation price as ``float`` when reliable, otherwise ``None``.
    """
    if not entry_price or not collateral_usd or not position_size_usd:
        return None
    if position_size_usd <= 0:
        return None

    liquidation_price = calculate_estimated_liquidation_price(
        entry_price=entry_price,
        collateral_usd=collateral_usd,
        size_usd=position_size_usd,
        is_long=is_long,
        maintenance_margin=0.01,  # GMX standard
        include_closing_fee=True,  # GMX 0.07% closing fee
    )

    if liquidation_price is None or liquidation_price <= 0:
        return None
    if is_long and liquidation_price >= entry_price:
        return None
    if (not is_long) and liquidation_price <= entry_price:
        return None

    return liquidation_price
