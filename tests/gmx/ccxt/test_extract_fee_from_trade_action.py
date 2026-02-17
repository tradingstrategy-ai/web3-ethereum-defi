"""Test _extract_fee_from_trade_action with real on-chain GMX V2 transactions.

Verifies that the shared fee extraction method correctly:
1. Extracts fees when trade_action has fee fields (EventEmitter path)
2. Enriches from on-chain PositionFeesCollected when trade_action lacks fee data (Subsquid path)
3. Returns reasonable USD fee amounts for USDC-collateral long positions

Uses 3 real keeper execution transactions on Arbitrum:
- TX1 (0x0f07...): MarketDecrease, Long ETH/USD, USDC collateral, ~$3 size, ~$0.0012 fee
- TX2 (0x5eb0...): MarketIncrease, Long ETH/USD, USDC collateral, ~$3 size, ~$0.0018 fee
- TX3 (0x9f45...): MarketDecrease, Long ETH/USD, USDC collateral, ~$3 size, ~$0.0012 fee
"""

import logging
import os

import pytest

from eth_defi.gmx.events import extract_order_execution_result, decode_gmx_events
from eth_defi.provider.multi_provider import create_multi_provider_web3

logger = logging.getLogger(__name__)

# Real keeper execution tx hashes on Arbitrum
TX1 = "0x0f078ae48afaf52d6aded49e1d6856f8ca0f0b65a3dd0a39b249808dcd29a6b9"
TX2 = "0x5eb04ad2de28c1f2b79309499a0d355cc8e5ea91a5cb297d55c2f3cf8d670239"
TX3 = "0x9f4545fd254f8ef687f16759a2c00fe947539e16e986fcef5fcdca4b6ff16903"

# USDC on Arbitrum
USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

# Correct GMX CCXT market symbol for ETH on Arbitrum
ETH_SYMBOL = "ETH/USDC:USDC"


@pytest.fixture(scope="module")
def web3():
    """Create Web3 instance for Arbitrum."""
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    if not rpc_url:
        pytest.skip("JSON_RPC_ARBITRUM not set")
    return create_multi_provider_web3(rpc_url)


@pytest.fixture(scope="module")
def gmx_exchange():
    """Create a minimal GMX exchange instance with loaded markets and token metadata."""
    from eth_defi.gmx.ccxt.exchange import GMX

    rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    if not rpc_url:
        pytest.skip("JSON_RPC_ARBITRUM not set")

    exchange = GMX({
        "rpcUrl": rpc_url,
        "privateKey": "0x" + "01" * 32,  # Dummy key - read-only operations
    })
    exchange.load_markets()

    # Sanity check: verify market exists
    assert ETH_SYMBOL in exchange.markets, (
        f"Market {ETH_SYMBOL} not found. Available ETH markets: "
        f"{[k for k in exchange.markets if 'ETH' in k]}"
    )

    return exchange


def _get_order_key_for_tx(web3, tx_hash: str) -> bytes | None:
    """Extract order key from a keeper execution receipt."""
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    for event in decode_gmx_events(web3, receipt):
        if event.event_name == "OrderExecuted":
            return event.get_bytes32("key") or event.topic1
    return None


def test_extract_order_execution_result_tx1(web3):
    """Verify extract_order_execution_result returns fee data for TX1 (MarketDecrease)."""
    receipt = web3.eth.get_transaction_receipt(TX1)
    order_key = _get_order_key_for_tx(web3, TX1)
    assert order_key is not None, "OrderExecuted event not found in TX1"

    result = extract_order_execution_result(web3, receipt, order_key)
    assert result is not None
    assert result.status == "executed"
    assert result.collateral_token is not None
    assert result.collateral_token.lower() == USDC_ADDRESS.lower()
    assert result.collateral_token_price is not None
    assert result.collateral_token_price > 0

    # Fees should be present from PositionFeesCollected
    assert result.fees is not None
    assert result.fees.position_fee > 0
    logger.info(
        "TX1 fees: position=%s, borrowing=%s, funding=%s, collateral_token=%s, collateral_price=%s",
        result.fees.position_fee,
        result.fees.borrowing_fee,
        result.fees.funding_fee,
        result.collateral_token,
        result.collateral_token_price,
    )


def test_extract_order_execution_result_tx2(web3):
    """Verify extract_order_execution_result returns fee data for TX2 (MarketIncrease)."""
    receipt = web3.eth.get_transaction_receipt(TX2)
    order_key = _get_order_key_for_tx(web3, TX2)
    assert order_key is not None, "OrderExecuted event not found in TX2"

    result = extract_order_execution_result(web3, receipt, order_key)
    assert result is not None
    assert result.status == "executed"
    assert result.collateral_token is not None
    assert result.collateral_token.lower() == USDC_ADDRESS.lower()
    assert result.fees is not None
    assert result.fees.position_fee > 0
    logger.info(
        "TX2 fees: position=%s, borrowing=%s, funding=%s",
        result.fees.position_fee,
        result.fees.borrowing_fee,
        result.fees.funding_fee,
    )


def test_extract_fee_with_trade_action_fields(gmx_exchange, web3):
    """Test _extract_fee_from_trade_action when trade_action has fee fields (EventEmitter path).

    Simulates the EventEmitter path where trade_action dict already contains
    positionFeeAmount, borrowingFeeAmount, fundingFeeAmount, collateralToken,
    and collateralTokenPriceMax from the event data.

    Must go through _convert_token_fee_to_usd (NOT _build_trading_fee fallback).
    """
    # First get the actual fee values from on-chain for TX2 (MarketIncrease)
    receipt = web3.eth.get_transaction_receipt(TX2)
    order_key = _get_order_key_for_tx(web3, TX2)
    result = extract_order_execution_result(web3, receipt, order_key)
    assert result is not None and result.fees is not None

    # Build a trade_action dict that mimics EventEmitter output (has fee fields)
    trade_action = {
        "positionFeeAmount": str(result.fees.position_fee),
        "borrowingFeeAmount": str(result.fees.borrowing_fee),
        "fundingFeeAmount": str(result.fees.funding_fee),
        "collateralToken": result.collateral_token,
        "collateralTokenPriceMax": str(result.collateral_token_price),
        "sizeDeltaUsd": str(int(3.0 * 1e30)),  # ~$3
        "isLong": True,
    }

    fee_dict = gmx_exchange._extract_fee_from_trade_action(
        trade_action=trade_action,
        symbol=ETH_SYMBOL,
        size_delta_usd=3.0,
        is_long=True,
        execution_tx_hash=TX2,
        order_key=order_key,
        log_prefix="test_with_fields",
    )

    logger.info("TX2 fee_dict (with fields): %s", fee_dict)

    assert fee_dict["currency"] == "USDC"
    assert fee_dict["cost"] > 0, f"Fee cost should be > 0, got {fee_dict['cost']}"
    assert fee_dict["cost"] < 0.01, f"Fee cost should be < $0.01 for $3 position, got {fee_dict['cost']}"

    # Rate should be ~0.06% for increase — verify it's NOT exactly 0.0006
    # (which would indicate the estimated fallback was used instead of actual conversion)
    assert fee_dict["rate"] > 0, f"Fee rate should be > 0, got {fee_dict['rate']}"
    assert fee_dict["rate"] < 0.01, f"Fee rate should be < 1%, got {fee_dict['rate']}"

    # TX2 has position_fee=1800 raw USDC (6 decimals) = 0.001800 USDC
    # With event price conversion: 0.001800 * ~$1.00 = ~$0.001800
    assert pytest.approx(fee_dict["cost"], abs=0.0005) == 0.0018


def test_extract_fee_without_trade_action_fields(gmx_exchange, web3):
    """Test _extract_fee_from_trade_action when trade_action lacks fee fields (Subsquid path).

    Simulates the Subsquid/blockchain fallback where trade_action does NOT have
    positionFeeAmount or collateralToken. The method should fall back to estimated
    fee since total_fee_tokens will be 0.
    """
    order_key = _get_order_key_for_tx(web3, TX1)
    assert order_key is not None

    # Build a trade_action dict that mimics Subsquid output (NO fee fields, NO collateral)
    trade_action = {
        "sizeDeltaUsd": str(int(3.0 * 1e30)),
        "isLong": True,
        "transaction": {"hash": TX1},
        # Deliberately NO positionFeeAmount, borrowingFeeAmount, fundingFeeAmount
        # Deliberately NO collateralToken, collateralTokenPriceMax
    }

    fee_dict = gmx_exchange._extract_fee_from_trade_action(
        trade_action=trade_action,
        symbol=ETH_SYMBOL,
        size_delta_usd=3.0,
        is_long=True,
        execution_tx_hash=TX1,
        order_key=order_key,
        log_prefix="test_without_fields",
    )

    logger.info("TX1 fee_dict (without fields, enriched): %s", fee_dict)

    # Without fee fields in trade_action, on-chain enrichment kicks in (Option B):
    # Fetches PositionFeesCollected from execution_tx_hash and extracts real fees.
    # TX1 is a MarketDecrease with ~$0.0012 actual fee (0.04% of $3)
    assert fee_dict["currency"] == "USDC"
    assert fee_dict["cost"] > 0, f"Fee cost should be > 0, got {fee_dict['cost']}"
    assert fee_dict["rate"] > 0, f"Fee rate should be > 0, got {fee_dict['rate']}"
    # Actual on-chain fee: position=1200 + borrowing=3 + funding=5 = 1208 raw USDC
    assert pytest.approx(fee_dict["cost"], abs=0.0005) == 0.0012


def test_extract_fee_all_three_txs(gmx_exchange, web3):
    """Verify fee extraction for all 3 real transactions with actual on-chain fee data.

    For each TX, fetch the real PositionFeesCollected data and pass it
    as trade_action fields, verifying fees are reasonable USD amounts.
    Uses _convert_token_fee_to_usd path (NOT estimated fallback).
    """
    test_cases = [
        {
            "tx_hash": TX1,
            "label": "TX1 (MarketDecrease)",
            "expected_fee_approx": 0.0012,  # ~$0.0012 (0.04% of $3)
            "size_usd": 3.0,
        },
        {
            "tx_hash": TX2,
            "label": "TX2 (MarketIncrease)",
            "expected_fee_approx": 0.0018,  # ~$0.0018 (0.06% of $3)
            "size_usd": 3.0,
        },
        {
            "tx_hash": TX3,
            "label": "TX3 (MarketDecrease)",
            "expected_fee_approx": 0.0012,  # ~$0.0012 (0.04% of $3)
            "size_usd": 3.0,
        },
    ]

    for case in test_cases:
        tx_hash = case["tx_hash"]
        receipt = web3.eth.get_transaction_receipt(tx_hash)
        order_key = _get_order_key_for_tx(web3, tx_hash)
        assert order_key is not None, f"No order key for {case['label']}"

        result = extract_order_execution_result(web3, receipt, order_key)
        assert result is not None, f"No execution result for {case['label']}"
        assert result.fees is not None, f"No fees for {case['label']}"

        # Build trade_action with actual on-chain fee data
        trade_action = {
            "positionFeeAmount": str(result.fees.position_fee),
            "borrowingFeeAmount": str(result.fees.borrowing_fee),
            "fundingFeeAmount": str(result.fees.funding_fee),
            "collateralToken": result.collateral_token,
            "collateralTokenPriceMax": str(result.collateral_token_price),
            "sizeDeltaUsd": str(int(case["size_usd"] * 1e30)),
            "isLong": True,
        }

        fee_dict = gmx_exchange._extract_fee_from_trade_action(
            trade_action=trade_action,
            symbol=ETH_SYMBOL,
            size_delta_usd=case["size_usd"],
            is_long=True,
            execution_tx_hash=tx_hash,
            order_key=order_key,
            log_prefix=case["label"],
        )

        logger.info("%s: fee_dict=%s", case["label"], fee_dict)

        assert fee_dict["currency"] == "USDC", f"{case['label']}: expected USDC, got {fee_dict['currency']}"
        assert fee_dict["cost"] > 0, f"{case['label']}: fee cost should be > 0, got {fee_dict['cost']}"
        assert fee_dict["cost"] < 0.01, f"{case['label']}: fee should be < $0.01 for ~$3 position, got {fee_dict['cost']}"
        assert pytest.approx(fee_dict["cost"], abs=0.001) == case["expected_fee_approx"], (
            f"{case['label']}: expected ~${case['expected_fee_approx']}, got ${fee_dict['cost']}"
        )

        logger.info(
            "%s: PASS - fee=$%s, rate=%s%%",
            case["label"],
            fee_dict["cost"],
            fee_dict["rate"] * 100,
        )


def test_subsquid_trade_actions_query():
    """Test that Subsquid tradeActions query returns fee fields (Option A).

    Queries the real Subsquid endpoint with a known order key and account
    to verify the tradeActions entity returns positionFeeAmount, borrowingFeeAmount,
    fundingFeeAmount, and collateralToken fields.
    """
    from eth_defi.gmx.graphql.client import GMXSubsquidClient

    # TX2 order key (MarketIncrease) — we know this order's account from Arbiscan
    account = "0x9Eecd13C4E0aeF29B321c49575601B9d33974aDB"

    client = GMXSubsquidClient(chain="arbitrum")

    # Get order key from TX2
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    if not rpc_url:
        pytest.skip("JSON_RPC_ARBITRUM not set")

    w3 = create_multi_provider_web3(rpc_url)
    order_key = _get_order_key_for_tx(w3, TX2)
    assert order_key is not None
    order_key_hex = "0x" + order_key.hex()

    trade_action = client.get_trade_action_by_order_key(
        order_key_hex,
        timeout_seconds=10,
        account=account,
    )

    logger.info("tradeActions result: %s", trade_action)

    assert trade_action is not None, "tradeActions query returned None"
    assert trade_action["eventName"] == "OrderExecuted"

    # Verify fee fields are present and non-zero
    pos_fee = trade_action.get("positionFeeAmount")
    borrow_fee = trade_action.get("borrowingFeeAmount")
    funding_fee = trade_action.get("fundingFeeAmount")
    collateral = trade_action.get("collateralToken")
    collateral_price = trade_action.get("collateralTokenPriceMax")

    logger.info(
        "tradeActions fee fields: positionFee=%s, borrowingFee=%s, fundingFee=%s, collateral=%s, price=%s",
        pos_fee,
        borrow_fee,
        funding_fee,
        collateral,
        collateral_price,
    )

    assert pos_fee is not None and int(pos_fee) > 0, f"positionFeeAmount should be > 0, got {pos_fee}"
    assert collateral is not None, f"collateralToken should be present, got {collateral}"
    assert collateral.lower() == USDC_ADDRESS.lower(), f"Expected USDC collateral, got {collateral}"
    assert collateral_price is not None, f"collateralTokenPriceMax should be present, got {collateral_price}"
