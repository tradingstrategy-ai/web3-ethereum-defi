"""Regression tests for the close-flow authoritative-pool override.

Covers the production bug where SOL/USDC:USDC close failed with
"Not a valid collateral for selected market!" because ``exchange.py`` used
``self.markets[symbol]["info"]["market_token"]`` (which could hold the
SOL-SOL synthetic pool address) instead of the authoritative on-chain pool
recorded in ``position_to_close["market"]``.

These tests are pure-unit: they mock all network calls and assert that
``trader.close_position`` receives the correct ``market_key`` regardless of
what ``self.markets`` holds.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from eth_defi.gmx.ccxt.exchange import GMX


#: Correct SOL-USDC pool address on Arbitrum — long token WSOL, short token USDC.
SOL_USDC_POOL = "0x09400D9DB990D5ed3f35D7be61DfAEB900Af03C9"

#: Wrong SOL-SOL synthetic pool address — long token == short token == WSOL.
SOL_SOL_SYNTHETIC = "0xf22CFFA7B4174554FF9dBf7B5A8c01FaaDceA722"

#: Wrapped SOL index-token address on Arbitrum.
WSOL_ADDRESS = "0x2bCc6D6CdBbDC0a4071e48bb3B969b06B3330c07"

#: Native USDC address on Arbitrum.
USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"


def _make_exchange(markets_market_token: str) -> MagicMock:
    """Build a minimal fake exchange instance with ``self.markets`` set up.

    :param markets_market_token:
        The ``market_token`` value stored in ``self.markets`` for
        ``SOL/USDC:USDC``.  Pass :data:`SOL_SOL_SYNTHETIC` to simulate the
        broken state, :data:`SOL_USDC_POOL` for the correct state.
    :return:
        A :class:`unittest.mock.MagicMock` with the attributes the tested
        code path reads.
    """
    exchange = MagicMock()
    exchange.markets = {
        "SOL/USDC:USDC": {
            "base": "SOL",
            "info": {
                "market_token": markets_market_token,
                "index_token": WSOL_ADDRESS,
                "long_token": WSOL_ADDRESS,
                "short_token": (WSOL_ADDRESS if markets_market_token == SOL_SOL_SYNTHETIC else USDC_ADDRESS),
            },
        }
    }
    exchange.default_slippage = 0.003
    exchange.execution_buffer = 1.3
    exchange._orders = {}
    return exchange


def _make_gmx_params(market_key: str, collateral_symbol: str = "USDC") -> dict:
    """Build a minimal ``gmx_params`` dict as ``_convert_ccxt_to_gmx_params`` produces.

    :param market_key:
        The pool address pre-set by :meth:`_resolve_market_info`.
    :param collateral_symbol:
        Collateral symbol (defaults to ``USDC``).
    :return:
        Parameters dict compatible with :meth:`_execute_close_with_position`.
    """
    return {
        "market_symbol": "SOL",
        "collateral_symbol": collateral_symbol,
        "start_token_symbol": collateral_symbol,
        "is_long": True,
        "size_delta_usd": 100.0,
        "leverage": 2.0,
        "slippage_percent": 0.003,
        "execution_buffer": 1.3,
        "auto_cancel": False,
        "market_key": market_key,
        "index_token_address": WSOL_ADDRESS,
        "_gmx_position": None,
        "_resolved_market_info": {},
        "_collateral_explicitly_set": False,
    }


def _make_position(market: str, collateral_token: str = "USDC", is_long: bool = True) -> dict:
    """Build a minimal on-chain position dict as ``GetOpenPositions`` returns.

    :param market:
        Authoritative on-chain pool address.
    :param collateral_token:
        Collateral token symbol.
    :param is_long:
        Position direction.
    :return:
        Position dict compatible with :meth:`_execute_close_with_position`.
    """
    return {
        "market": market,
        "market_symbol": "SOL",
        "collateral_token": collateral_token,
        "is_long": is_long,
        "position_size": 200.0,
        "position_size_usd_raw": 200 * 10**30,
        "initial_collateral_amount_usd": 100.0,
        "leverage": 2.0,
    }


# ---------------------------------------------------------------------------
# Regression test: wrong pool in self.markets, correct pool in position
# ---------------------------------------------------------------------------


def test_close_uses_on_chain_pool_when_self_markets_is_wrong() -> None:
    """Regression for SOL/USDC:USDC close failure.

    Scenario: ``self.markets`` holds the SOL-SOL synthetic pool (wrong) but
    the actual open position is on the SOL-USDC pool (correct).  The close
    must target the position's pool, not ``self.markets``.
    """
    exchange = _make_exchange(markets_market_token=SOL_SOL_SYNTHETIC)
    gmx_params = _make_gmx_params(market_key=SOL_SOL_SYNTHETIC)
    position_to_close = _make_position(market=SOL_USDC_POOL, collateral_token="USDC")

    captured_kwargs: dict = {}

    def fake_close_position(**kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return MagicMock()

    exchange.trader.close_position.side_effect = fake_close_position

    with (
        patch("eth_defi.gmx.ccxt.exchange.cap_size_delta_to_position", side_effect=lambda s, p, label=None: s),
        patch("eth_defi.gmx.ccxt.exchange.is_raw_usd_amount", return_value=False),
    ):
        GMX._execute_close_with_position(
            exchange,
            symbol="SOL/USDC:USDC",
            type="market",
            side="sell",
            gmx_params=gmx_params,
            position_to_close=position_to_close,
            size_delta_usd=200 * 10**30,
            initial_collateral_delta=100.0,
        )

    assert captured_kwargs.get("market_key", "").lower() == SOL_USDC_POOL.lower(), f"Expected market_key={SOL_USDC_POOL}, got {captured_kwargs.get('market_key')}"
    assert captured_kwargs.get("collateral_symbol") == "USDC"
    assert captured_kwargs.get("start_token_symbol") == "USDC"


def test_close_leaves_market_key_unchanged_when_position_matches_self_markets() -> None:
    """No spurious override when position pool matches ``self.markets``.

    When the on-chain position is on the same pool already in
    ``self.markets``, ``close_kwargs`` should use that pool unchanged.
    """
    exchange = _make_exchange(markets_market_token=SOL_USDC_POOL)
    gmx_params = _make_gmx_params(market_key=SOL_USDC_POOL)
    position_to_close = _make_position(market=SOL_USDC_POOL, collateral_token="USDC")

    captured_kwargs: dict = {}

    def fake_close_position(**kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return MagicMock()

    exchange.trader.close_position.side_effect = fake_close_position

    with (
        patch("eth_defi.gmx.ccxt.exchange.cap_size_delta_to_position", side_effect=lambda s, p, label=None: s),
        patch("eth_defi.gmx.ccxt.exchange.is_raw_usd_amount", return_value=False),
    ):
        GMX._execute_close_with_position(
            exchange,
            symbol="SOL/USDC:USDC",
            type="market",
            side="sell",
            gmx_params=gmx_params,
            position_to_close=position_to_close,
            size_delta_usd=200 * 10**30,
            initial_collateral_delta=100.0,
        )

    assert captured_kwargs.get("market_key", "").lower() == SOL_USDC_POOL.lower(), f"market_key should remain {SOL_USDC_POOL}, got {captured_kwargs.get('market_key')}"
    assert captured_kwargs.get("collateral_symbol") == "USDC"


def test_close_logs_warning_when_pool_override_triggers(caplog: pytest.LogCaptureFixture) -> None:
    """The override path must emit a ``WARNING`` for production auditability.

    Operators need a breadcrumb in the logs when the helper corrects a stale
    pool address; otherwise the fix is invisible in production log streams.
    """
    exchange = _make_exchange(markets_market_token=SOL_SOL_SYNTHETIC)
    gmx_params = _make_gmx_params(market_key=SOL_SOL_SYNTHETIC)
    position_to_close = _make_position(market=SOL_USDC_POOL, collateral_token="USDC")

    exchange.trader.close_position.return_value = MagicMock()

    with (
        patch("eth_defi.gmx.ccxt.exchange.cap_size_delta_to_position", side_effect=lambda s, p, label=None: s),
        patch("eth_defi.gmx.ccxt.exchange.is_raw_usd_amount", return_value=False),
        caplog.at_level(logging.WARNING, logger="eth_defi.gmx.ccxt.exchange"),
    ):
        GMX._execute_close_with_position(
            exchange,
            symbol="SOL/USDC:USDC",
            type="market",
            side="sell",
            gmx_params=gmx_params,
            position_to_close=position_to_close,
            size_delta_usd=200 * 10**30,
            initial_collateral_delta=100.0,
        )

    assert any("overriding market_key" in rec.message for rec in caplog.records), "Expected pool-override warning to be logged when market_key is overridden"
