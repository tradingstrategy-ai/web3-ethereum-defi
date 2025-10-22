"""
Tests for GMX swap functionality on Arbitrum Sepolia testnet with actual execution.

These tests execute actual swap transactions on Arbitrum Sepolia testnet

IMPORTANT: These tests REQUIRE a funded wallet on Arbitrum Sepolia:
- Sufficient USDC.SG, BTC, or CRV tokens for swaps
- Sufficient ETH for gas fees
- Token approvals for GMX contracts

Required Environment Variables:
- ARBITRUM_GMX_TEST_SEPOLIA_PRIVATE_KEY: Your wallet private key
- ARBITRUM_SEPOLIA_RPC_URL: Arbitrum Sepolia RPC endpoint

Available tokens on Arbitrum Sepolia:
- USDC.SG (Single-sided GM USDC pool)
- BTC (Wrapped BTC)
- CRV (Curve token)
- USDC (Synthetic USDC)
"""

import pytest

from eth_defi.gmx.contracts import get_token_address_normalized, get_exchange_router_contract
from eth_defi.gmx.order.base_order import OrderResult
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation


def test_initialization(trading_manager_sepolia):
    """Test that the swap functionality initializes correctly."""
    assert trading_manager_sepolia is not None
    assert trading_manager_sepolia.config is not None
    assert trading_manager_sepolia.config.get_chain().lower() == "arbitrum_sepolia"


def test_swap_usdc_to_btc_with_execution(
    trading_manager_sepolia,
    test_wallet_sepolia,
    arbitrum_sepolia_config,
):
    """
    Test creating and EXECUTING a USDC.SG -> BTC swap order.
    """
    web3 = arbitrum_sepolia_config.web3
    wallet_address = test_wallet_sepolia.address
    chain = "arbitrum_sepolia"

    # Get token details
    usdc_sg_address = get_token_address_normalized(chain, "USDC.SG")
    btc_address = get_token_address_normalized(chain, "BTC")

    usdc_sg = fetch_erc20_details(web3, usdc_sg_address)
    btc = fetch_erc20_details(web3, btc_address)

    # Check initial balances
    initial_usdc_balance = usdc_sg.contract.functions.balanceOf(wallet_address).call()
    initial_btc_balance = btc.contract.functions.balanceOf(wallet_address).call()

    print(f"\nInitial balances:")
    print(f"  USDC.SG: {initial_usdc_balance / (10**usdc_sg.decimals):.2f}")
    print(f"  BTC: {initial_btc_balance / (10**btc.decimals):.8f}")

    # Skip if no USDC.SG balance
    if initial_usdc_balance == 0:
        pytest.skip("No USDC.SG balance available for swap test")

    # Amount to swap: use 10% of balance or 5 USDC, whichever is smaller
    swap_amount = min(5.0, (initial_usdc_balance / (10**usdc_sg.decimals)) * 0.1)
    amount_wei = int(swap_amount * (10**usdc_sg.decimals))

    # Check and approve token if needed
    exchange_router = get_exchange_router_contract(web3, chain)
    spender_address = exchange_router.address

    current_allowance = usdc_sg.contract.functions.allowance(wallet_address, spender_address).call()
    print(f"\nCurrent USDC.SG allowance: {current_allowance / (10**usdc_sg.decimals):.2f}")

    if current_allowance < amount_wei:
        print(f"Approving USDC.SG tokens for GMX contract...")

        approve_tx = usdc_sg.contract.functions.approve(spender_address, amount_wei * 2).build_transaction(
            {
                "from": wallet_address,
                "gas": 100000,
                "gasPrice": web3.eth.gas_price,
            }
        )

        if "nonce" in approve_tx:
            del approve_tx["nonce"]

        signed_approve_tx = test_wallet_sepolia.sign_transaction_with_new_nonce(approve_tx)
        approve_tx_hash = web3.eth.send_raw_transaction(signed_approve_tx.rawTransaction)

        print(f"Approval transaction sent: {approve_tx_hash.hex()}")
        approve_receipt = web3.eth.wait_for_transaction_receipt(approve_tx_hash, timeout=120)
        print(f"Approval confirmed! Status: {approve_receipt.status}")

        assert approve_receipt.status == 1, "Approval transaction failed"

    # Create swap order
    print(f"\nCreating swap order: {swap_amount:.2f} USDC.SG -> BTC")
    order_result = trading_manager_sepolia.swap_tokens(
        in_token_symbol="USDC.SG",
        out_token_symbol="BTC",
        amount=swap_amount,
        slippage_percent=0.03,
        execution_buffer=5.0,
    )

    # Verify OrderResult structure
    assert isinstance(order_result, OrderResult), "Expected OrderResult instance"
    assert hasattr(order_result, "transaction"), "OrderResult should have transaction"

    # Sign and send the transaction
    transaction = order_result.transaction
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = test_wallet_sepolia.sign_transaction_with_new_nonce(transaction)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)

    print(f"Swap transaction sent: {tx_hash.hex()}")

    # Wait for confirmation
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    print(f"Swap transaction confirmed! Status: {receipt['status']}")
    print(f"Block number: {receipt['blockNumber']}")
    print(f"Gas used: {receipt['gasUsed']}")

    # Verify transaction success
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert receipt["status"] == 1, "Swap transaction failed"

    # Check final balances
    final_usdc_balance = usdc_sg.contract.functions.balanceOf(wallet_address).call()
    final_btc_balance = btc.contract.functions.balanceOf(wallet_address).call()

    print(f"\nFinal balances:")
    print(f"  USDC.SG: {final_usdc_balance / (10**usdc_sg.decimals):.2f}")
    print(f"  BTC: {final_btc_balance / (10**btc.decimals):.8f}")

    # Verify USDC.SG decreased
    usdc_change = (initial_usdc_balance - final_usdc_balance) / (10**usdc_sg.decimals)
    print(f"\nUSDC.SG spent: {usdc_change:.2f}")
    assert final_usdc_balance < initial_usdc_balance, "USDC.SG balance should decrease"

    # Note: BTC balance might not change immediately due to GMX's order execution model
    # The order is submitted but may not execute immediately
    print(f"BTC change: {(final_btc_balance - initial_btc_balance) / (10**btc.decimals):.8f}")


def test_swap_order_creation_without_execution(trading_manager_sepolia):
    """
    Test creating a USDC.SG -> BTC swap order WITHOUT executing.

    This verifies order creation works even without a funded wallet.
    """
    order_result = trading_manager_sepolia.swap_tokens(
        in_token_symbol="USDC.SG",
        out_token_symbol="BTC",
        amount=5.0,
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


def test_swap_btc_to_usdc_order_creation(trading_manager_sepolia):
    """Test creating a BTC -> USDC.SG swap order (reverse direction)."""
    order_result = trading_manager_sepolia.swap_tokens(
        in_token_symbol="BTC",
        out_token_symbol="USDC.SG",
        amount=0.001,  # 0.001 BTC
        slippage_percent=0.03,
        execution_buffer=5.0,
    )

    assert isinstance(order_result, OrderResult)
    assert hasattr(order_result, "transaction")


def test_swap_usdc_to_crv_order_creation(trading_manager_sepolia):
    """Test creating a USDC.SG -> CRV swap order."""
    order_result = trading_manager_sepolia.swap_tokens(
        in_token_symbol="USDC.SG",
        out_token_symbol="CRV",
        amount=10.0,
        slippage_percent=0.03,
        execution_buffer=5.0,
    )

    assert isinstance(order_result, OrderResult)
    assert hasattr(order_result, "transaction")


def test_swap_with_different_slippage(trading_manager_sepolia):
    """Test swap with different slippage parameters."""
    # Low slippage
    order_low = trading_manager_sepolia.swap_tokens(
        in_token_symbol="USDC.SG",
        out_token_symbol="BTC",
        amount=5.0,
        slippage_percent=0.01,  # 1%
        execution_buffer=2.0,
    )

    # High slippage
    order_high = trading_manager_sepolia.swap_tokens(
        in_token_symbol="USDC.SG",
        out_token_symbol="BTC",
        amount=5.0,
        slippage_percent=0.05,  # 5%
        execution_buffer=5.0,
    )

    assert isinstance(order_low, OrderResult)
    assert isinstance(order_high, OrderResult)


def test_swap_produces_valid_transaction_structure(trading_manager_sepolia):
    """Test that swap produces a valid unsigned transaction structure."""
    order_result = trading_manager_sepolia.swap_tokens(
        in_token_symbol="USDC.SG",
        out_token_symbol="BTC",
        amount=5.0,
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


def test_swap_execution_fee_included(trading_manager_sepolia):
    """Test that swap includes execution fee."""
    order_result = trading_manager_sepolia.swap_tokens(
        in_token_symbol="USDC.SG",
        out_token_symbol="BTC",
        amount=5.0,
        slippage_percent=0.03,
        execution_buffer=5.0,
    )

    assert hasattr(order_result, "execution_fee")
    assert order_result.execution_fee > 0
    assert order_result.transaction["value"] >= order_result.execution_fee


def test_swap_invalid_token_symbol(trading_manager_sepolia):
    """Test that invalid token symbol raises appropriate error."""
    with pytest.raises((ValueError, KeyError, Exception)):
        trading_manager_sepolia.swap_tokens(
            in_token_symbol="INVALID_TOKEN",
            out_token_symbol="BTC",
            amount=5.0,
            slippage_percent=0.03,
            execution_buffer=5.0,
        )


def test_swap_zero_amount(trading_manager_sepolia):
    """Test that zero amount raises error."""
    with pytest.raises((ValueError, Exception)):
        trading_manager_sepolia.swap_tokens(
            in_token_symbol="USDC.SG",
            out_token_symbol="BTC",
            amount=0.0,
            slippage_percent=0.03,
            execution_buffer=5.0,
        )


def test_check_wallet_balances(test_wallet_sepolia, arbitrum_sepolia_config):
    """
    Helper test to check wallet balances on Arbitrum Sepolia.

    This helps verify your wallet has the necessary tokens for swap tests.
    """
    web3 = arbitrum_sepolia_config.web3
    wallet_address = test_wallet_sepolia.address
    chain = "arbitrum_sepolia"

    print(f"\nWallet address: {wallet_address}")

    # Check ETH balance
    eth_balance = web3.eth.get_balance(wallet_address)
    print(f"ETH balance: {eth_balance / 1e18:.6f} ETH")

    # Check token balances
    tokens = ["USDC.SG", "BTC", "CRV", "USDC"]

    for token_symbol in tokens:
        try:
            token_address = get_token_address_normalized(chain, token_symbol)
            token = fetch_erc20_details(web3, token_address)
            balance = token.contract.functions.balanceOf(wallet_address).call()
            print(f"{token_symbol} balance: {balance / (10**token.decimals):.6f}")
        except Exception as e:
            print(f"{token_symbol}: Error - {e}")

    # This test always passes - it's just for information
    assert True
