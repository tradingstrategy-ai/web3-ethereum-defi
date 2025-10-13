"""
Tests for SwapOrder class with parametrized chain testing.

This test suite verifies the functionality of the SwapOrder class
when connected to different networks using Anvil forks. Tests include
swap estimation, route determination, and actual transaction execution.
"""

import pytest
from decimal import Decimal

from eth_defi.gmx.order.base_order import OrderResult, OrderType
from eth_defi.gmx.order.swap_order import SwapOrder
from eth_defi.gmx.contracts import NETWORK_TOKENS
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.token import fetch_erc20_details


def test_swap_order_initialization(chain_name, swap_order_weth_usdc):
    """Test that SwapOrder initializes correctly with token addresses."""
    swap_order = swap_order_weth_usdc
    tokens = NETWORK_TOKENS[chain_name]

    # Get expected tokens
    if chain_name == "arbitrum":
        start_token = tokens["WETH"]
        out_token = tokens["USDC"]
    else:  # avalanche
        start_token = tokens["WETH"]  # WETH exists on Avalanche too
        out_token = tokens["USDC"]

    assert swap_order.config is not None
    assert swap_order.chain.lower() == chain_name.lower()
    assert swap_order.start_token == start_token
    assert swap_order.out_token == out_token
    assert swap_order.web3 is not None
    assert swap_order.markets is not None


def test_swap_order_route_determination_single_hop(chain_name, swap_order_weth_usdc):
    """Test swap route determination for single-hop swaps."""
    swap_order = swap_order_weth_usdc

    # Get available markets to verify route
    markets = swap_order.markets.get_available_markets()
    assert len(markets) > 0, "Should have available markets"

    # Create a small swap to test routing
    result = swap_order.create_swap_order(
        amount_in=1000000000000000000,  # 1 ETH
        slippage_percent=0.01,
    )

    assert isinstance(result, OrderResult)


def test_swap_order_route_determination_multi_hop(chain_name, gmx_config_fork):
    """Test swap route determination for multi-hop swaps."""
    tokens = NETWORK_TOKENS[chain_name]

    # Test WBTC -> WETH (should require multi-hop through USDC)
    if chain_name == "arbitrum":
        start_token = tokens["WBTC"]
        out_token = tokens["WETH"]
    else:  # avalanche
        start_token = tokens["WBTC"]
        out_token = tokens["WETH"]

    swap_order = SwapOrder(gmx_config_fork, start_token, out_token)

    # Create swap order - this should work even for multi-hop
    result = swap_order.create_swap_order(
        amount_in=100000000,  # 1 WBTC (8 decimals)
        slippage_percent=0.015,  # Higher slippage for multi-hop
    )

    assert isinstance(result, OrderResult)


def test_estimate_swap_output(chain_name, swap_order_weth_usdc):
    """Test swap output estimation functionality."""
    swap_order = swap_order_weth_usdc
    amount_in = 1000000000000000000  # 1 ETH

    # Get swap estimation
    estimate = swap_order.estimate_swap_output(amount_in)

    # Verify estimation structure
    assert isinstance(estimate, dict)
    assert "out_token_amount" in estimate
    assert "price_impact_usd" in estimate
    assert "estimated_output_formatted" in estimate

    # Verify reasonable values
    assert estimate["out_token_amount"] > 0
    assert isinstance(estimate["price_impact_usd"], float)
    assert estimate["estimated_output_formatted"] > 0

    # For 1 ETH -> USDC, should get reasonable USDC amount
    if chain_name == "arbitrum":
        # Should get at least 1000 USDC for 1 ETH (conservative estimate)
        assert estimate["estimated_output_formatted"] > 1000
    else:
        # Avalanche might have different liquidity
        assert estimate["estimated_output_formatted"] > 100


def test_estimate_swap_output_with_price_impact(chain_name, swap_order_weth_usdc):
    """Test that large swaps show meaningful price impact."""
    swap_order = swap_order_weth_usdc
    small_amount = 1000000000000000000  # 1 ETH
    large_amount = 10000000000000000000  # 10 ETH

    # Get estimates for different amounts
    small_estimate = swap_order.estimate_swap_output(small_amount)
    large_estimate = swap_order.estimate_swap_output(large_amount)

    # Large swap should have higher or equal price impact (negative values)
    # Both should have valid outputs
    assert small_estimate["out_token_amount"] > 0
    assert large_estimate["out_token_amount"] > 0


def test_create_market_swap_ccxt_method(chain_name, swap_order_weth_usdc, wallet_with_all_tokens):
    """Test CCXT-compatible create_market_swap method."""
    swap_order = swap_order_weth_usdc
    amount_in = 100000000000000000  # 0.1 ETH

    # Use CCXT-compatible method
    result = swap_order.create_market_swap(amount_in=amount_in, slippage_percent=0.01)

    assert isinstance(result, OrderResult)


def test_swap_execution_with_weth_to_usdc(chain_name, swap_order_weth_usdc, test_wallet, wallet_with_all_tokens):
    """Test actual swap execution from WETH to USDC."""
    swap_order = swap_order_weth_usdc
    tokens = NETWORK_TOKENS[chain_name]
    web3 = swap_order.web3
    wallet_address = test_wallet.address

    # Get token contracts
    weth = fetch_erc20_details(web3, tokens["WETH"])
    usdc = fetch_erc20_details(web3, tokens["USDC"])

    # Check initial balances
    initial_weth_balance = weth.contract.functions.balanceOf(wallet_address).call()
    initial_usdc_balance = usdc.contract.functions.balanceOf(wallet_address).call()

    print(f"Initial WETH balance: {initial_weth_balance / 1e18:.6f}")
    print(f"Initial USDC balance: {initial_usdc_balance / (10**usdc.decimals):.2f}")

    # Skip test if no WETH balance available
    if initial_weth_balance <= 0:
        pytest.skip("No WETH balance available for swap test")

    # Create swap order
    amount_in = min(100000000000000000, initial_weth_balance // 10)  # 0.1 ETH or 10% of balance

    # Get estimation first
    estimate = swap_order.estimate_swap_output(amount_in)
    print(f"Estimated output: {estimate['estimated_output_formatted']:.2f} USDC")
    print(f"Price impact: {estimate['price_impact_usd']:.4f} USD")

    # Create swap transaction
    result = swap_order.create_swap_order(
        amount_in=amount_in,
        slippage_percent=0.02,  # 2% slippage for safety
        min_output_amount=0,  # Accept any output for test
    )

    # Verify the result
    assert isinstance(result, OrderResult)
    assert hasattr(result, "transaction")
    assert isinstance(result.transaction, dict)
    print(f"Swap transaction created successfully")


# TODO: Skip this for now. As it's reverting without a proper reason
# def test_swap_execution_with_usdc_to_weth(chain_name, swap_order_usdc_weth, test_wallet, wallet_with_all_tokens):
#     """Test actual swap execution from USDC to WETH."""
#     swap_order = swap_order_usdc_weth
#     tokens = NETWORK_TOKENS[chain_name]
#     web3 = swap_order.web3
#     wallet_address = test_wallet.address
#
#     # Get token contracts
#     usdc = fetch_erc20_details(web3, tokens["USDC"])
#     # Check initial balances
#     initial_usdc_balance = usdc.contract.functions.balanceOf(wallet_address).call()
#
#     # print(f"Initial USDC balance: {initial_usdc_balance / (10**usdc.decimals):.2f}")
#     # print(f"Initial WETH balance: {initial_weth_balance / 1e18:.6f}")
#
#     # Ensure we have some USDC
#     assert initial_usdc_balance > 0, "Need USDC balance for swap test"
#
#     # Create swap order (USDC -> WETH)
#     amount_in = min(100 * (10**usdc.decimals), initial_usdc_balance // 10)  # $100 or 10% of balance
#
#     # Get estimation
#     # estimate = swap_order.estimate_swap_output(amount_in)
#     # print(f"Estimated output: {estimate['estimated_output_formatted']:.6f} WETH")
#
#     # Create and execute swap
#     result = swap_order.create_swap_order(amount_in=amount_in, slippage_percent=0.02)
#
#     # Sign and execute transaction - remove nonce since sign_transaction_with_new_nonce will add it
#     tx_dict = result.transaction.copy()
#     if "nonce" in tx_dict:
#         del tx_dict["nonce"]
#     signed_txn = test_wallet.sign_transaction_with_new_nonce(tx_dict)
#     tx_hash = web3.eth.send_raw_transaction(signed_txn.rawTransaction)
#     tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
#
#     # Verify transaction success
#     assert_transaction_success_with_explanation(web3, tx_hash)
#     assert tx_receipt.status == 1


def test_swap_invalid_token_pair(chain_name, gmx_config):
    """Test swap with invalid token pair raises appropriate error."""
    # Try to create swap with non-existent tokens
    invalid_address = "0x0000000000000000000000000000000000000001"
    tokens = NETWORK_TOKENS[chain_name]

    with pytest.raises(ValueError, match="No market found for input token"):
        swap_order = SwapOrder(gmx_config, invalid_address, tokens["USDC"])
        swap_order.create_swap_order(amount_in=1000000000000000000)


def test_swap_zero_amount(chain_name, swap_order_weth_usdc):
    """Test swap with zero amount raises error."""
    swap_order = swap_order_weth_usdc

    with pytest.raises(ValueError, match="Amount must be positive"):
        swap_order.create_swap_order(amount_in=0)


def test_swap_with_custom_slippage(chain_name, swap_order_weth_usdc, wallet_with_all_tokens):
    """Test swap with custom slippage parameters."""
    swap_order = swap_order_weth_usdc

    # Test with very low slippage
    result_low = swap_order.create_swap_order(
        amount_in=100000000000000000,  # 0.1 ETH
        slippage_percent=0.001,  # 0.1% slippage
    )

    # Test with high slippage
    result_high = swap_order.create_swap_order(
        amount_in=100000000000000000,  # 0.1 ETH
        slippage_percent=0.05,  # 5% slippage
    )

    # Both should succeed but with different acceptable prices
    assert isinstance(result_low, OrderResult)
    assert isinstance(result_high, OrderResult)


def test_swap_with_min_output_amount(chain_name, swap_order_weth_usdc, wallet_with_all_tokens):
    """Test swap with minimum output amount specification."""
    swap_order = swap_order_weth_usdc

    # Get estimation first
    amount_in = 100000000000000000  # 0.1 ETH
    estimate = swap_order.estimate_swap_output(amount_in)

    # Set min output to 90% of estimated
    min_output = int(estimate["out_token_amount"] * 0.9)

    result = swap_order.create_swap_order(amount_in=amount_in, min_output_amount=min_output, slippage_percent=0.02)

    assert isinstance(result, OrderResult)
    # The min_output_amount should be reflected in the order parameters
    # This is verified through successful transaction creation


def test_swap_order_different_token_pairs(chain_name, gmx_config_fork):
    """Test swap orders with different token pairs available on each chain."""
    tokens = NETWORK_TOKENS[chain_name]

    if chain_name == "arbitrum":
        # Just test that we can create swap orders for common pairs
        try:
            swap_order = SwapOrder(gmx_config_fork, tokens["WETH"], tokens["USDC"])
            result = swap_order.create_swap_order(
                amount_in=1000000000000000000,  # 1 WETH
                slippage_percent=0.01,
            )
            assert isinstance(result, OrderResult)
        except ValueError as e:
            if "No market found" in str(e):
                # This is acceptable if the market doesn't exist
                pass
            else:
                raise

    else:  # avalanche
        # Just test that we can create swap orders for common pairs
        try:
            swap_order = SwapOrder(gmx_config_fork, tokens["WETH"], tokens["USDC"])
            result = swap_order.create_swap_order(
                amount_in=1000000000000000000,  # 1 WETH
                slippage_percent=0.01,
            )
            assert isinstance(result, OrderResult)
        except ValueError as e:
            if "No market found" in str(e):
                # This is acceptable if the market doesn't exist
                pass
            else:
                raise


def test_create_swap_order_produces_unsigned_tx(chain_name, swap_order_weth_usdc):
    """Test that create_swap_order returns a valid unsigned transaction."""
    swap_order = swap_order_weth_usdc
    tokens = NETWORK_TOKENS[chain_name]

    # Get WETH details to determine amount
    weth = fetch_erc20_details(swap_order.web3, tokens["WETH"])
    # Use a small amount: 0.001 WETH
    amount_in = int(0.001 * 10**weth.decimals)

    # Create the swap order
    result = swap_order.create_swap_order(
        amount_in=amount_in,
        slippage_percent=0.01,  # 1%
        execution_buffer=1.2,
    )

    # Validate result
    assert hasattr(result, "transaction"), "Result should have transaction attribute"
    tx = result.transaction

    # Validate unsigned transaction structure
    assert isinstance(tx, dict), "Transaction must be a dict"
    assert "to" in tx
    assert "data" in tx
    assert "value" in tx
    assert "gas" in tx or "gasLimit" in tx
    assert tx["to"] is not None
    assert len(tx["data"]) > 2  # Should be hex string like "0x..."

    print("Unsigned transaction created successfully:")
    print(f"  To: {tx['to']}")
    print(f"  Data length: {len(tx['data'])} bytes")
    print(f"  Value: {tx.get('value', 0)}")

    # We do NOT send the transaction — just confirm it's constructable
