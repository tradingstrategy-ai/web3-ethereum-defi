"""Forking integration tests for IEEE 754 float precision guard.

These tests prove end-to-end that:
1. The safety cap (cap_size_delta_to_position) intercepts sizeDeltaUsd > sizeInUsd
   and prevents the InvalidDecreaseOrderSize revert that would otherwise occur.
2. Float corruption changes position size (demonstrating the root cause).
3. The close path with an overshoot input still closes the position successfully.

Required environment variables:
- JSON_RPC_ARBITRUM: Arbitrum mainnet RPC endpoint for forking

Uses isolated_fork_env fixture which provides:
- Fresh Anvil fork per test
- Mock oracle set up FIRST (matching debug.py flow)
- Funded wallet with ETH/WETH/USDC
- GMX config with approved tokens
"""

import logging

import pytest
from flaky import flaky

from eth_defi.gmx.precision import (
    assert_not_float_corrupted,
    cap_size_delta_to_position,
    is_raw_usd_amount,
)
from tests.gmx.fork_helpers import (
    execute_order_as_keeper,
    extract_order_key_from_receipt,
    fetch_on_chain_oracle_prices,
    setup_mock_oracle,
)

logger = logging.getLogger(__name__)


@flaky(max_runs=3, min_passes=1)
def test_safety_cap_prevents_invalid_close(isolated_fork_env, execution_buffer):
    """Prove that the safety cap intercepts overshooting sizeDeltaUsd and closes successfully.

    Root cause: IEEE 754 float rounding changes raw uint256 position sizes.
    If the result is larger than sizeInUsd, GMX reverts with InvalidDecreaseOrderSize.

    The safety cap (cap_size_delta_to_position) prevents this by clamping any
    sizeDeltaUsd to the on-chain position size before the order is submitted.

    Flow:
    1. Open a $10 long ETH position
    2. Read position_size_usd_raw from Reader contract
    3. Demonstrate float corruption: int(float(raw)) != raw for large uint256 values
    4. Verify safety cap math: cap(raw+1, raw) == raw
    5. Attempt close with raw_size + 1 (simulated overshoot) via the full exchange path
       — the safety cap intercepts and caps to raw_size
    6. Verify the close SUCCEEDS (cap prevented the InvalidDecreaseOrderSize revert)
    7. Verify position is closed
    """
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()

    env.wallet.sync_nonce(env.web3)

    # === Step 1: Open a $10 long ETH position ===
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=10,
        leverage=2.5,
        slippage_percent=0.005,
        execution_buffer=execution_buffer,
    )

    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1, "Open order transaction should succeed"

    order_key = extract_order_key_from_receipt(receipt)
    exec_receipt, _ = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Open order execution should succeed"

    # === Step 2: Read position and demonstrate float corruption ===
    positions = env.positions.get_data(wallet_address)
    assert len(positions) >= 1, "Should have at least 1 position"

    _, position = list(positions.items())[0]
    raw_size = position["position_size_usd_raw"]
    assert is_raw_usd_amount(raw_size), "Position size should be in raw format"

    # Demonstrate that float conversion alters raw position sizes.
    # IEEE 754 float64 has only 53 bits of mantissa; GMX uint256 values have
    # ~103 bits, so the lower ~50 bits are lost. Rounding direction (up or down)
    # depends on the specific value — we do NOT assume direction here.
    float_size = int(float(raw_size))
    assert float_size != raw_size, (
        "Expected float corruption for position size %s but int(float(x)) == x. "
        "The position might be too small to trigger precision loss." % raw_size
    )
    logger.info(
        "Float corruption confirmed: raw=%s, float_result=%s, delta=%s (direction=%s)",
        raw_size,
        float_size,
        abs(float_size - raw_size),
        "UP" if float_size > raw_size else "DOWN",
    )

    # assert_not_float_corrupted flags raw_size as not float-safe:
    # it raises when int(float(v)) != v, meaning float conversion would alter v.
    with pytest.raises(AssertionError, match="float-corrupted"):
        assert_not_float_corrupted(raw_size, "test")

    # === Step 3: Verify safety cap math ===
    # overshoot_size = raw_size + 1 always exceeds the position, regardless of
    # which direction float rounding went.
    overshoot_size = raw_size + 1

    # cap_size_delta_to_position must clamp overshoot to raw_size.
    capped = cap_size_delta_to_position(overshoot_size, raw_size)
    assert capped == raw_size, (
        "Safety cap must reduce overshoot_size=%s to raw_size=%s, got %s"
        % (overshoot_size, raw_size, capped)
    )
    logger.info("Safety cap math verified: cap(%s, %s) == %s", overshoot_size, raw_size, capped)

    # === Step 4: Close with OVERSHOOT value — safety cap saves the day ===
    # Pass overshoot_size through the full exchange.close_position() path.
    # The exchange-level safety cap intercepts it, caps to raw_size, and
    # the resulting close order has the correct sizeDeltaUsd == sizeInUsd.
    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(env.web3)
    new_eth_price = int(current_eth_price * 1.01)
    setup_mock_oracle(env.web3, eth_price_usd=new_eth_price, usdc_price_usd=current_usdc_price)
    env.wallet.sync_nonce(env.web3)

    collateral_amount_usd = position["initial_collateral_amount_usd"]

    close_result = env.trading.close_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=overshoot_size,  # Overshoot — exchange cap will clamp this
        initial_collateral_delta=collateral_amount_usd,
        slippage_percent=0.005,
        execution_buffer=execution_buffer,
    )

    close_tx = close_result.transaction.copy()
    if "nonce" in close_tx:
        del close_tx["nonce"]

    signed_close = env.wallet.sign_transaction_with_new_nonce(close_tx)
    close_hash = env.web3.eth.send_raw_transaction(signed_close.rawTransaction)
    close_receipt = env.web3.eth.wait_for_transaction_receipt(close_hash)
    assert close_receipt["status"] == 1, "Close order creation tx should succeed"

    close_order_key = extract_order_key_from_receipt(close_receipt)

    # Keeper should SUCCEED because the cap prevented the overshoot.
    close_exec_receipt, _ = execute_order_as_keeper(env.web3, close_order_key)
    assert close_exec_receipt["status"] == 1, (
        "Keeper should succeed: safety cap clamped overshoot to raw_size, preventing "
        "InvalidDecreaseOrderSize. Got status=%s" % close_exec_receipt["status"]
    )
    logger.info("Confirmed: overshoot close succeeded because safety cap intercepted it")

    # === Step 5: Verify position is now closed ===
    positions_after = env.positions.get_data(wallet_address)
    assert len(positions_after) == 0, "Position should be closed after capped close"
    logger.info("Confirmed: position closed successfully")
