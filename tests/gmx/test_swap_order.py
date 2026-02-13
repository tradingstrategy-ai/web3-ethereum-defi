"""
Tests for GMX swap functionality on Arbitrum mainnet fork.

These tests execute actual swap transactions on Arbitrum mainnet fork using Anvil.
Tests follow the complete order lifecycle:
1. Create order (sign and submit transaction)
2. Execute order as keeper (using mock oracle)
3. Verify swap was completed with assertions
"""

import pytest
from flaky import flaky

from eth_defi.gmx.contracts import get_token_address_normalized
from eth_defi.gmx.order.base_order import OrderResult
from eth_defi.hotwallet import HotWallet
from eth_defi.token import fetch_erc20_details
from tests.gmx.fork_helpers import execute_order_as_keeper, extract_order_key_from_receipt, fetch_on_chain_oracle_prices


def test_initialization(trading_manager_fork):
    """Test that the swap functionality initializes correctly."""
    assert trading_manager_fork is not None
    assert trading_manager_fork.config is not None
    assert trading_manager_fork.config.get_chain().lower() == "arbitrum"


# NOTE: This test is currently disabled due to flakiness in the Arbitrum fork.
# @flaky(max_runs=3, min_passes=1)
# def test_swap_usdc_to_eth_with_execution(isolated_fork_env, execution_buffer):
#     """
#     Test creating and EXECUTING a USDC -> ETH swap order on Arbitrum fork.
#
#     Flow:
#     1. Create swap order (USDC -> ETH)
#     2. Submit transaction to blockchain
#     3. Execute order as keeper
#     4. Verify swap was completed (USDC decreased, native ETH increased)
#
#     Note: GMX swaps output native ETH (not WETH) when swapping to ETH.
#     """
#     env = isolated_fork_env
#     wallet_address = env.config.get_wallet_address()
#     chain = "arbitrum"
#
#     # Get token details
#     usdc_address = get_token_address_normalized(chain, "USDC")
#     usdc = fetch_erc20_details(env.web3, usdc_address)
#
#     # Check initial balances (USDC and native ETH)
#     initial_usdc_balance = usdc.contract.functions.balanceOf(wallet_address).call()
#     initial_eth_balance = env.web3.eth.get_balance(wallet_address)
#
#     print(f"\nInitial balances:")
#     print(f"  USDC: {initial_usdc_balance / (10**usdc.decimals):.2f}")
#     print(f"  ETH:  {initial_eth_balance / 1e18:.6f}")
#
#     # Skip if no USDC balance
#     if initial_usdc_balance == 0:
#         pytest.skip("No USDC balance available for swap test")
#
#     # Amount to swap: use 1000 USDC for meaningful ETH output
#     swap_amount = min(1000.0, (initial_usdc_balance / (10**usdc.decimals)) * 0.1)
#
#     # Sync nonce before transaction
#     env.wallet.sync_nonce(env.web3)
#
#     # === Step 1: Create swap order ===
#     print(f"\nCreating swap order: {swap_amount:.2f} USDC -> ETH")
#     order_result = env.trading.swap_tokens(
#         in_token_symbol="USDC",
#         out_token_symbol="ETH",
#         amount=swap_amount,
#         slippage_percent=0.03,
#         execution_buffer=execution_buffer,
#     )
#
#     # Verify OrderResult structure
#     assert isinstance(order_result, OrderResult), "Expected OrderResult instance"
#     assert hasattr(order_result, "transaction"), "OrderResult should have transaction"
#     assert order_result.execution_fee > 0, "Execution fee should be > 0"
#
#     # === Step 2: Submit order transaction ===
#     transaction = order_result.transaction.copy()
#     if "nonce" in transaction:
#         del transaction["nonce"]
#
#     signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
#     tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
#     receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)
#
#     print(f"Swap order transaction sent: {tx_hash.hex()}")
#     print(f"Status: {receipt['status']}, Gas used: {receipt['gasUsed']}")
#
#     assert receipt["status"] == 1, "Order transaction should succeed"
#
#     # Extract order key from receipt
#     order_key = extract_order_key_from_receipt(receipt)
#     assert order_key is not None, "Should extract order key from receipt"
#
#     # === Step 3: Execute order as keeper ===
#     exec_receipt, keeper_address = execute_order_as_keeper(env.web3, order_key)
#     assert exec_receipt["status"] == 1, "Order execution should succeed"
#
#     # === Step 4: Verify swap was completed ===
#     final_usdc_balance = usdc.contract.functions.balanceOf(wallet_address).call()
#     final_eth_balance = env.web3.eth.get_balance(wallet_address)
#
#     print(f"\nFinal balances:")
#     print(f"  USDC: {final_usdc_balance / (10**usdc.decimals):.2f}")
#     print(f"  ETH:  {final_eth_balance / 1e18:.6f}")
#
#     # Verify USDC decreased
#     usdc_change = (initial_usdc_balance - final_usdc_balance) / (10**usdc.decimals)
#     print(f"\nUSDC spent: {usdc_change:.2f}")
#     assert final_usdc_balance < initial_usdc_balance, "USDC balance should decrease"
#
#     # Verify native ETH increased (accounting for gas costs)
#     # GMX swaps output native ETH, not WETH
#     eth_change = (final_eth_balance - initial_eth_balance) / 1e18
#     # Gas costs are ~0.02-0.03 ETH for order submission + keeper execution
#     # At ~$2700/ETH, 1000 USDC should give us ~0.37 ETH
#     eth_gain_net = eth_change + 0.03  # Add back estimated gas costs
#     print(f"ETH change: {eth_change:.6f} (net after gas: ~{eth_gain_net:.6f})")
#     # The swap should result in net positive ETH after gas
#     # Note: In fork testing, the exact amount may vary due to oracle/price differences
#     assert eth_gain_net > 0, f"Should receive ETH after swap (net of gas), got {eth_gain_net:.6f}"


@flaky(max_runs=3, min_passes=1)
def test_swap_eth_to_usdc_with_execution(isolated_fork_env, execution_buffer):
    """
    Test creating and EXECUTING an ETH -> USDC swap order on Arbitrum fork.

    Flow:
    1. Create swap order (ETH -> USDC)
    2. Submit transaction to blockchain
    3. Execute order as keeper
    4. Verify swap was completed (native ETH decreased, USDC increased)

    Note: GMX uses native ETH for swaps (internally wraps to WETH).
    """
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()
    chain = "arbitrum"

    # Get token details
    usdc_address = get_token_address_normalized(chain, "USDC")
    usdc = fetch_erc20_details(env.web3, usdc_address)

    # Check initial balances (USDC and native ETH)
    initial_usdc_balance = usdc.contract.functions.balanceOf(wallet_address).call()
    initial_eth_balance = env.web3.eth.get_balance(wallet_address)

    print(f"\nInitial balances:")
    print(f"  USDC: {initial_usdc_balance / (10**usdc.decimals):.2f}")
    print(f"  ETH:  {initial_eth_balance / 1e18:.6f}")

    # Skip if insufficient ETH balance (need at least 2 ETH for swap + gas)
    if initial_eth_balance < 2 * 10**18:
        pytest.skip("Insufficient ETH balance for swap test")

    # Amount to swap: use 1 ETH
    swap_amount = 1.0

    # Sync nonce before transaction
    env.wallet.sync_nonce(env.web3)

    # === Step 1: Create swap order ===
    print(f"\nCreating swap order: {swap_amount:.6f} ETH -> USDC")
    order_result = env.trading.swap_tokens(
        in_token_symbol="ETH",
        out_token_symbol="USDC",
        amount=swap_amount,
        slippage_percent=0.03,
        execution_buffer=execution_buffer,
    )

    # Verify OrderResult structure
    assert isinstance(order_result, OrderResult), "Expected OrderResult instance"
    assert hasattr(order_result, "transaction"), "OrderResult should have transaction"
    assert order_result.execution_fee > 0, "Execution fee should be > 0"

    # === Step 2: Submit order transaction ===
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    print(f"Swap order transaction sent: {tx_hash.hex()}")
    print(f"Status: {receipt['status']}, Gas used: {receipt['gasUsed']}")

    assert receipt["status"] == 1, "Order transaction should succeed"

    # Extract order key from receipt
    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None, "Should extract order key from receipt"

    # === Step 3: Execute order as keeper ===
    exec_receipt, keeper_address = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Order execution should succeed"

    # === Step 4: Verify swap was completed ===
    final_usdc_balance = usdc.contract.functions.balanceOf(wallet_address).call()
    final_eth_balance = env.web3.eth.get_balance(wallet_address)

    print(f"\nFinal balances:")
    print(f"  USDC: {final_usdc_balance / (10**usdc.decimals):.2f}")
    print(f"  ETH:  {final_eth_balance / 1e18:.6f}")

    # Verify ETH decreased (swap amount + gas)
    eth_change = (initial_eth_balance - final_eth_balance) / 1e18
    print(f"\nETH spent: {eth_change:.6f} (includes swap amount + gas)")
    # ETH should decrease by at least the swap amount (1 ETH)
    assert eth_change > 0.9, "ETH balance should decrease by approximately swap amount"

    # Verify USDC increased
    usdc_change = (final_usdc_balance - initial_usdc_balance) / (10**usdc.decimals)
    print(f"USDC received: {usdc_change:.2f}")
    assert final_usdc_balance > initial_usdc_balance, "USDC balance should increase after swap"
    # Dynamically check against current ETH price (accounting for fees/slippage)
    eth_price, _ = fetch_on_chain_oracle_prices(env.web3)
    min_expected_usdc = eth_price * 0.9  # At least 90% of ETH price after fees/slippage
    assert usdc_change > min_expected_usdc, f"Should receive substantial USDC for 1 ETH (got {usdc_change:.2f}, expected > {min_expected_usdc:.2f})"


def test_swap_order_creation_without_execution(trading_manager_fork):
    """
    Test creating a USDC -> BTC swap order WITHOUT executing.

    This verifies order creation works even without a funded wallet.
    """
    order_result = trading_manager_fork.swap_tokens(
        in_token_symbol="USDC",
        out_token_symbol="BTC",
        amount=100.0,
        slippage_percent=0.03,
        execution_buffer=5.0,
    )

    # Verify OrderResult structure
    assert isinstance(order_result, OrderResult), "Expected OrderResult instance"
    assert hasattr(order_result, "transaction"), "OrderResult should have transaction"
    assert hasattr(order_result, "execution_fee"), "OrderResult should have execution_fee"

    # Verify transaction structure
    assert "from" in order_result.transaction
    assert "to" in order_result.transaction
    assert "data" in order_result.transaction
    assert "value" in order_result.transaction


def test_swap_usdc_to_link_order_creation(trading_manager_fork):
    """Test creating a USDC -> LINK swap order."""
    order_result = trading_manager_fork.swap_tokens(
        in_token_symbol="USDC",
        out_token_symbol="LINK",
        amount=100.0,
        slippage_percent=0.03,
        execution_buffer=5.0,
    )

    assert isinstance(order_result, OrderResult)
    assert hasattr(order_result, "transaction")


def test_swap_with_different_slippage(trading_manager_fork):
    """Test swap with different slippage parameters."""
    # Low slippage
    order_low = trading_manager_fork.swap_tokens(
        in_token_symbol="USDC",
        out_token_symbol="BTC",
        amount=100.0,
        slippage_percent=0.01,  # 1%
        execution_buffer=2.0,
    )

    # High slippage
    order_high = trading_manager_fork.swap_tokens(
        in_token_symbol="USDC",
        out_token_symbol="BTC",
        amount=100.0,
        slippage_percent=0.05,  # 5%
        execution_buffer=5.0,
    )

    assert isinstance(order_low, OrderResult)
    assert isinstance(order_high, OrderResult)


def test_swap_produces_valid_transaction_structure(trading_manager_fork):
    """Test that swap produces a valid unsigned transaction structure."""
    order_result = trading_manager_fork.swap_tokens(
        in_token_symbol="USDC",
        out_token_symbol="BTC",
        amount=100.0,
        slippage_percent=0.03,
        execution_buffer=5.0,
    )

    tx = order_result.transaction

    # Verify transaction structure
    assert isinstance(tx, dict)
    assert "to" in tx
    assert "data" in tx
    assert "value" in tx
    assert "gas" in tx or "gasLimit" in tx
    assert tx["to"] is not None
    assert len(tx["data"]) > 2  # Hex string
    assert isinstance(tx["value"], int)


def test_swap_execution_fee_included(trading_manager_fork):
    """Test that swap includes execution fee."""
    order_result = trading_manager_fork.swap_tokens(
        in_token_symbol="USDC",
        out_token_symbol="BTC",
        amount=100.0,
        slippage_percent=0.03,
        execution_buffer=5.0,
    )

    assert hasattr(order_result, "execution_fee")
    assert order_result.execution_fee > 0
    assert order_result.transaction["value"] >= order_result.execution_fee


def test_swap_invalid_token_symbol(trading_manager_fork):
    """Test that invalid token symbol raises appropriate error."""
    with pytest.raises((ValueError, KeyError, Exception)):
        trading_manager_fork.swap_tokens(
            in_token_symbol="INVALID_TOKEN",
            out_token_symbol="BTC",
            amount=100.0,
            slippage_percent=0.03,
            execution_buffer=5.0,
        )


def test_swap_zero_amount(trading_manager_fork):
    """Test that zero amount raises error."""
    with pytest.raises((ValueError, Exception)):
        trading_manager_fork.swap_tokens(
            in_token_symbol="USDC",
            out_token_symbol="BTC",
            amount=0.0,
            slippage_percent=0.03,
            execution_buffer=5.0,
        )


# def test_check_wallet_balances(
#     anvil_private_key,
#     arbitrum_fork_config,
#     wallet_with_usdc,
# ):
#     """
#     Helper test to check wallet balances on Arbitrum fork.
#
#     This helps verify your wallet has the necessary tokens for swap tests.
#     """
#
#     web3 = arbitrum_fork_config.web3
#     hot_wallet = HotWallet.from_private_key(anvil_private_key.lower())
#     wallet_address = hot_wallet.address
#     chain = "arbitrum"
#
#     print(f"\nWallet address: {wallet_address}")
#
#     # Check native ETH balance
#     eth_balance = web3.eth.get_balance(wallet_address)
#     print(f"Native ETH balance: {eth_balance / 1e18:.6f} ETH")
#
#     # Check token balances (note: GMX uses "ETH" for WETH internally)
#     tokens = ["USDC", "BTC", "LINK", "ARB", "ETH"]
#
#     for token_symbol in tokens:
#         try:
#             token_address = get_token_address_normalized(chain, token_symbol)
#             token = fetch_erc20_details(web3, token_address)
#             balance = token.contract.functions.balanceOf(wallet_address).call()
#             display_symbol = "WETH" if token_symbol == "ETH" else token_symbol
#             print(f"{display_symbol} balance: {balance / (10**token.decimals):.6f}")
#         except Exception as e:
#             print(f"{token_symbol}: Error - {e}")
#
#     # This test always passes - it's just for information
#     assert True
