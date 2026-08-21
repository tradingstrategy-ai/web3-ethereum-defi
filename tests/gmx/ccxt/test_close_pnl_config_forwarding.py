"""The ccxt close path must forward the configured PnL-payout direction.

Regression for a gap found while reviewing the GMX PnL-token fix
(``docs/claude-plans/2026-08-20-gmx-pnl-token-nav-leak.md``): ``OrderParams``
and ``DecreaseOrder.create_decrease_order()`` accept
``decrease_position_swap_type`` / ``should_unwrap_native_token``, but neither
``_convert_ccxt_to_gmx_params()`` nor the ``close_kwargs`` it feeds into
``trader.close_position()`` ever read them from the caller's ``params`` --
so a ccxt caller had no way to configure the payout direction at all; every
close silently used the hardcoded default regardless of what was passed.

Uses the ``ccxt_gmx_fork_open_close`` fixture -- a real, wallet-backed GMX
instance on an isolated Anvil fork, the same fixture
``test_execution_buffer_forwarding.py`` uses to intercept opens. A view-only
instance (no wallet) has ``self.trader is None`` -- there is nothing to
build orders with -- so it cannot be used here. ``_execute_close_with_position()``
is documented as extracted from ``create_order()`` specifically so this
assembly logic can be exercised without broadcasting anything: the trader's
``close_position`` is intercepted via ``monkeypatch`` on the real object
(matching ``test_execution_buffer_forwarding.py``'s pattern), not replaced
with a mock, so nothing is silently faked.
"""

import pytest

from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.constants import DECREASE_POSITION_SWAP_TYPES


class _Intercepted(Exception):
    """Raised by the stub to stop before anything is signed or broadcast."""

    def __init__(self, kwargs: dict):
        super().__init__("intercepted")
        self.kwargs = kwargs


def _make_gmx_params(gmx: GMX, symbol: str, market_key: str, **overrides) -> dict:
    """Build a ``gmx_params`` dict as ``_convert_ccxt_to_gmx_params`` would produce.

    :param gmx: Live GMX CCXT exchange instance (markets already loaded).
    :param symbol: CCXT unified symbol (e.g. ``ETH/USDC:USDC``).
    :param market_key: Pool address.
    :param overrides: Additional keys to merge in (e.g. the two PnL-payout fields).
    :return: Parameters dict compatible with ``_execute_close_with_position``.
    """
    market = gmx.markets[symbol]
    base = market["base"]
    index_token = market.get("info", {}).get("index_token", "")
    params = {
        "market_symbol": base,
        "collateral_symbol": "USDC",
        "start_token_symbol": "USDC",
        "is_long": True,
        "size_delta_usd": 100.0,
        "leverage": 2.0,
        "slippage_percent": 0.003,
        "execution_buffer": 1.3,
        "auto_cancel": False,
        "market_key": market_key,
        "index_token_address": index_token,
        "_gmx_position": None,
        "_resolved_market_info": {},
        "_collateral_explicitly_set": False,
    }
    params.update(overrides)
    return params


def _make_position(market: str) -> dict:
    """Build a minimal on-chain position dict as ``GetOpenPositions`` returns.

    :param market: Authoritative onchain pool address.
    :return: Position dict compatible with ``_execute_close_with_position``.
    """
    return {
        "market": market,
        "collateral_token": "USDC",
        "is_long": True,
        "position_size": 200.0,
        "position_size_usd_raw": 200 * 10**30,
        "initial_collateral_amount_usd": 100.0,
        "leverage": 2.0,
    }


def test_close_forwards_configured_swap_type(ccxt_gmx_fork_open_close: GMX, monkeypatch):
    """A non-default ``decrease_position_swap_type`` must reach ``trader.close_position``."""
    gmx = ccxt_gmx_fork_open_close
    symbol = "ETH/USDC:USDC"
    market_key = gmx.markets[symbol]["info"]["market_token"]

    captured: dict = {}

    def _stub(**kwargs):
        captured.update(kwargs)
        raise _Intercepted(kwargs)

    monkeypatch.setattr(gmx.trader, "close_position", _stub)

    gmx_params = _make_gmx_params(
        gmx,
        symbol,
        market_key,
        decrease_position_swap_type=DECREASE_POSITION_SWAP_TYPES["no_swap"],
        should_unwrap_native_token=True,
    )
    position_to_close = _make_position(market=market_key)

    with pytest.raises(_Intercepted):
        gmx._execute_close_with_position(
            symbol=symbol,
            type="market",
            side="sell",
            gmx_params=gmx_params,
            position_to_close=position_to_close,
            size_delta_usd=200 * 10**30,
            initial_collateral_delta=100.0,
        )

    assert captured.get("decrease_position_swap_type") == DECREASE_POSITION_SWAP_TYPES["no_swap"]
    assert captured.get("should_unwrap_native_token") is True


def test_close_omits_swap_type_when_caller_does_not_configure_one(ccxt_gmx_fork_open_close: GMX, monkeypatch):
    """When the caller configures nothing, ``close_kwargs`` must not force a value.

    ``DecreaseOrder.create_decrease_order()`` supplies its own default
    (``swap_pnl_token_to_collateral_token`` / ``should_unwrap_native_token=False``)
    when the keyword is absent entirely. The forwarding must be conditional,
    not an unconditional pass-through of a possibly-stale default, so that
    default stays the single source of truth.
    """
    gmx = ccxt_gmx_fork_open_close
    symbol = "ETH/USDC:USDC"
    market_key = gmx.markets[symbol]["info"]["market_token"]

    captured: dict = {}

    def _stub(**kwargs):
        captured.update(kwargs)
        raise _Intercepted(kwargs)

    monkeypatch.setattr(gmx.trader, "close_position", _stub)

    gmx_params = _make_gmx_params(gmx, symbol, market_key)
    position_to_close = _make_position(market=market_key)

    with pytest.raises(_Intercepted):
        gmx._execute_close_with_position(
            symbol=symbol,
            type="market",
            side="sell",
            gmx_params=gmx_params,
            position_to_close=position_to_close,
            size_delta_usd=200 * 10**30,
            initial_collateral_delta=100.0,
        )

    assert "decrease_position_swap_type" not in captured
    assert "should_unwrap_native_token" not in captured
