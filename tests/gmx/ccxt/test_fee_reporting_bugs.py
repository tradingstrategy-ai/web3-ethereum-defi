"""Test fee reporting bugs in GMX CCXT exchange.

Issue 1 (PR #793)
-----------------
``_build_trading_fee()`` expects a USD notional (``size_delta_usd``) but all
three call sites in the open-order parse methods pass ``amount`` in base
currency units (e.g. ETH).  This makes the estimated fee ~price-factor too
small (e.g. ~2 500× for ETH at $2 500).

Affected lines before fix:

- ``exchange.py`` — ``_parse_sltp_result_to_ccxt``
- ``exchange.py`` — ``_parse_order_result_to_ccxt``
- ``async_support/exchange.py`` — ``_parse_sltp_result_to_ccxt``

Issue 2
-------
``_convert_token_fee_to_usd()`` stablecoin fallback returns
``fee_in_tokens`` directly, treating the post-decimal token amount as USD.
For pegged stablecoins this is a reasonable 1:1 approximation (< 1 % error).
For non-stablecoins without a price, the method already returns 0 defensively.
These tests document the existing behaviour.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

logger = logging.getLogger(__name__)

ETH_SYMBOL = "ETH/USDC:USDC"
ETH_PRICE = 2_500.0  # USD per ETH, representative value
AMOUNT_ETH = 0.5  # base-currency units
AMOUNT_USD = AMOUNT_ETH * ETH_PRICE  # $1 250 USD notional


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gmx_exchange():
    """Minimal GMX exchange instance without loaded markets.

    Sufficient for fee unit tests that do not make on-chain calls.  Markets
    are not loaded so ``_build_trading_fee`` defaults to USDC currency.

    A mock web3 is injected so no live RPC is required — ``JSON_RPC_ARBITRUM``
    is not needed for these pure unit tests.
    """
    from eth_defi.gmx.ccxt.exchange import GMX

    mock_web3 = MagicMock()
    mock_web3.eth.chain_id = 42161  # Arbitrum mainnet

    with patch("eth_defi.gmx.ccxt.exchange.create_multi_provider_web3", return_value=mock_web3):
        exchange = GMX(params={"rpcUrl": "http://localhost:8545", "chainId": 42161})

    return exchange


def _make_order_result(mark_price: float) -> MagicMock:
    """Build a minimal mock ``OrderResult`` for parse-method tests."""
    result = MagicMock()
    result.mark_price = mark_price
    result.execution_fee = 1_000_000_000_000_000  # 0.001 ETH in wei
    result.acceptable_price = mark_price * 1.003
    result.gas_limit = 300_000
    result.estimated_price_impact = None
    return result


def _make_sltp_result(entry_price: float) -> MagicMock:
    """Build a minimal mock ``SLTPOrderResult`` for parse-method tests."""
    result = MagicMock()
    result.entry_price = entry_price
    result.total_execution_fee = 1_000_000_000_000_000  # 0.001 ETH in wei
    result.main_order_fee = 500_000_000_000_000
    result.stop_loss_fee = None
    result.take_profit_fee = None
    result.stop_loss_trigger_price = None
    result.take_profit_trigger_price = None
    return result


def _make_receipt(success: bool = True) -> dict:
    return {"status": 1 if success else 0, "blockNumber": 100, "gasUsed": 200_000}


# ---------------------------------------------------------------------------
# Issue 1 — _build_trading_fee() unit tests
# ---------------------------------------------------------------------------


def test_build_trading_fee_correct_with_usd(gmx_exchange):
    """``_build_trading_fee(symbol, usd_notional)`` produces fee = notional × 0.06 %.

    $1 250 notional → $0.75 fee.
    """
    fee = gmx_exchange._build_trading_fee(ETH_SYMBOL, AMOUNT_USD)

    assert fee["cost"] == pytest.approx(0.75, rel=0.01)
    assert fee["rate"] == pytest.approx(0.0006)
    assert fee["currency"] == "USDC"


def test_build_trading_fee_underreports_when_base_units_passed(gmx_exchange):
    """Passing base units (ETH) instead of USD produces a fee smaller by the price factor.

    Demonstrates the bug: 0.5 ETH treated as $0.5 → fee = $0.0003 instead of
    the correct $0.75 (off by ``ETH_PRICE`` = 2 500×).
    """
    fee_buggy = gmx_exchange._build_trading_fee(ETH_SYMBOL, AMOUNT_ETH)  # base units — wrong
    fee_correct = gmx_exchange._build_trading_fee(ETH_SYMBOL, AMOUNT_USD)  # USD — correct

    # The buggy call produces a fee that is ETH_PRICE times smaller
    assert fee_buggy["cost"] == pytest.approx(fee_correct["cost"] / ETH_PRICE, rel=0.01)


def test_build_trading_fee_zero_amount(gmx_exchange):
    """Zero size delta produces zero fee."""
    fee = gmx_exchange._build_trading_fee(ETH_SYMBOL, 0.0)
    assert fee["cost"] == 0.0


def test_build_trading_fee_negative_amount_uses_abs(gmx_exchange):
    """Negative amount (short / decrease) should use absolute value for fee."""
    fee_pos = gmx_exchange._build_trading_fee(ETH_SYMBOL, AMOUNT_USD)
    fee_neg = gmx_exchange._build_trading_fee(ETH_SYMBOL, -AMOUNT_USD)
    assert fee_pos["cost"] == pytest.approx(fee_neg["cost"], rel=0.001)


# ---------------------------------------------------------------------------
# Issue 1 — Parse methods must multiply amount by price before calling
#            _build_trading_fee()
# ---------------------------------------------------------------------------


def test_parse_order_result_fee_reflects_usd_notional(gmx_exchange):
    """``_parse_order_result_to_ccxt`` fee must use ``amount × mark_price`` as USD notional.

    With 0.5 ETH at $2 500 = $1 250 notional, fee = $0.75 (not $0.0003).

    This test fails before the fix.
    """
    order_result = _make_order_result(mark_price=ETH_PRICE)
    receipt = _make_receipt()

    order = gmx_exchange._parse_order_result_to_ccxt(
        order_result=order_result,
        symbol=ETH_SYMBOL,
        side="buy",
        type="market",
        amount=AMOUNT_ETH,
        tx_hash="0x" + "ab" * 32,
        receipt=receipt,
    )

    fee = order["fee"]
    expected_cost = AMOUNT_ETH * ETH_PRICE * 0.0006  # $0.75

    logger.info("_parse_order_result_to_ccxt fee: %s (expected ~$%.4f)", fee, expected_cost)

    assert fee["cost"] == pytest.approx(expected_cost, rel=0.01), f"Fee should be ~${expected_cost:.4f} (0.5 ETH × $2 500 × 0.06 %%), but got ${fee['cost']:.6f}.  Bug: amount is in ETH units, not USD."
    assert fee["rate"] == pytest.approx(0.0006)


def test_parse_sltp_result_fee_reflects_usd_notional(gmx_exchange):
    """``_parse_sltp_result_to_ccxt`` fee must use ``amount × entry_price`` as USD notional.

    With 0.5 ETH at $2 500 entry price, fee = $0.75 (not $0.0003).

    This test fails before the fix.
    """
    sltp_result = _make_sltp_result(entry_price=ETH_PRICE)
    receipt = _make_receipt()

    order = gmx_exchange._parse_sltp_result_to_ccxt(
        sltp_result=sltp_result,
        symbol=ETH_SYMBOL,
        side="buy",
        type="market",
        amount=AMOUNT_ETH,
        tx_hash="0x" + "cd" * 32,
        receipt=receipt,
    )

    fee = order["fee"]
    expected_cost = AMOUNT_ETH * ETH_PRICE * 0.0006  # $0.75

    logger.info("_parse_sltp_result_to_ccxt fee: %s (expected ~$%.4f)", fee, expected_cost)

    assert fee["cost"] == pytest.approx(expected_cost, rel=0.01), f"Fee should be ~${expected_cost:.4f} (0.5 ETH × $2 500 × 0.06 %%), but got ${fee['cost']:.6f}.  Bug: amount is in ETH units, not USD."
    assert fee["rate"] == pytest.approx(0.0006)


def test_parse_order_result_fee_zero_price_returns_zero_fee(gmx_exchange):
    """When mark_price is 0 or None, fee cost must be 0 (not NaN/inf)."""
    order_result = _make_order_result(mark_price=0.0)
    receipt = _make_receipt()

    order = gmx_exchange._parse_order_result_to_ccxt(
        order_result=order_result,
        symbol=ETH_SYMBOL,
        side="buy",
        type="market",
        amount=AMOUNT_ETH,
        tx_hash="0x" + "ef" * 32,
        receipt=receipt,
    )

    assert order["fee"]["cost"] == 0.0


# ---------------------------------------------------------------------------
# Issue 2 — _convert_token_fee_to_usd() behaviour documentation
# ---------------------------------------------------------------------------


def test_convert_fee_stablecoin_without_price_approximates_usd(gmx_exchange):
    """Stablecoin fallback: ``fee_in_tokens`` returned directly as USD (1:1 approximation).

    For USDC/USDT, 1 token ≈ $1.  The error is < 1 % unless the stablecoin is
    de-pegged.  This test documents the current approximation — it is not a bug.
    """
    market = {
        "short_token": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
        "long_token": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
    }
    usdc_addr = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"

    # Inject token metadata directly — no load_markets() needed
    original_meta = getattr(gmx_exchange, "_token_metadata", {})
    gmx_exchange._token_metadata = {
        usdc_addr: {"symbol": "USDC", "decimals": 6, "address": usdc_addr},
    }

    try:
        result = gmx_exchange._convert_token_fee_to_usd(
            fee_tokens=1_800_000,  # 1.8 USDC (6 decimals)
            market=market,
            is_long=False,
            collateral_token=usdc_addr,
            collateral_token_price=None,  # no price → stablecoin fallback
        )
    finally:
        gmx_exchange._token_metadata = original_meta

    assert result == pytest.approx(1.8, rel=0.01), f"Stablecoin fallback should return fee_in_tokens as USD, got {result}"


def test_convert_fee_nonstable_without_price_returns_zero(gmx_exchange):
    """Non-stablecoin without price data returns 0.0 to avoid a 2 500× error.

    Returning 0.001 WETH as $0.001 would be off by ~$2 500 (the ETH price).
    Zero is the correct defensive response when no price is available.
    """
    market = {
        "short_token": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
        "long_token": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
    }
    weth_addr = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"

    original_meta = getattr(gmx_exchange, "_token_metadata", {})
    gmx_exchange._token_metadata = {
        weth_addr: {"symbol": "WETH", "decimals": 18, "address": weth_addr},
    }

    try:
        result = gmx_exchange._convert_token_fee_to_usd(
            fee_tokens=1_000_000_000_000_000,  # 0.001 WETH in raw units
            market=market,
            is_long=True,
            collateral_token=weth_addr,
            collateral_token_price=None,  # no price → defensive fallback
        )
    finally:
        gmx_exchange._token_metadata = original_meta

    assert result == 0.0, f"Non-stablecoin without price should return 0 defensively, got {result}"
