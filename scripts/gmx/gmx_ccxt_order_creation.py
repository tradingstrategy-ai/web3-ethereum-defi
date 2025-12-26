"""Test GMX CCXT order creation functionality.

This script tests the new order creation methods to ensure they work correctly.
Supports both Anvil (automatic) and Tenderly (manual) fork modes.

Usage:
    # Anvil mode (automatic fork):
    export PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
    python scripts/gmx/gmx_ccxt_order_creation.py

    # Tenderly mode (for better debugging):
    export PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
    export TD_ARB=<your_tenderly_fork_url>
    python scripts/gmx/gmx_ccxt_order_creation.py --tenderly
"""

import argparse
import os

from eth_utils import to_checksum_address
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.gmx.ccxt import GMX
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_token_address_normalized, get_contract_addresses
from eth_defi.gmx.core import GetOpenPositions
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from tests.gmx.fork_helpers import execute_order_as_keeper, setup_mock_oracle, extract_order_key_from_receipt
from rich.console import Console
from ccxt.base.errors import NotSupported

print = Console().print

# Fork test configuration
FORK_BLOCK = 392496384
LARGE_USDC_HOLDER = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
LARGE_WETH_HOLDER = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")


def setup_fork_environment(
    web3: Web3,
    wallet_address: str,
    wallet: HotWallet,
):
    """Setup fork network with mock oracle and funded wallet."""
    print("\n" + "=" * 80)
    print("Setting up fork environment")
    print("=" * 80)

    chain_id = web3.eth.chain_id
    chain = get_chain_name(chain_id).lower()

    print(f"  Chain ID: {chain_id}")
    print(f"  Chain: {chain}")
    print(f"  Block: {web3.eth.block_number}")

    # Setup mock oracle
    print("\nSetting up mock oracle...")
    setup_mock_oracle(web3)
    print("  Mock oracle configured")

    # Determine which RPC method to use for setting balance
    # Try Tenderly first, fall back to Anvil
    set_balance_method = None
    try:
        web3.provider.make_request("tenderly_setBalance", [wallet_address, hex(1)])
        set_balance_method = "tenderly_setBalance"
        print("  Using Tenderly for balance manipulation")
    except Exception:
        try:
            web3.provider.make_request("anvil_setBalance", [wallet_address, hex(1)])
            set_balance_method = "anvil_setBalance"
            print("  Using Anvil for balance manipulation")
        except Exception:
            print("  Warning: Cannot manipulate balances (not on fork)")
            set_balance_method = None

    # Fund wallet with ETH
    print("\nFunding wallet...")
    eth_amount_wei = 100 * 10**18
    if set_balance_method:
        web3.provider.make_request(set_balance_method, [wallet_address, hex(eth_amount_wei)])
        print(f"  ETH balance: 100 ETH")

        # Give whales some ETH for gas
        gas_eth = 1 * 10**18
        web3.provider.make_request(set_balance_method, [LARGE_USDC_HOLDER, hex(gas_eth)])
        web3.provider.make_request(set_balance_method, [LARGE_WETH_HOLDER, hex(gas_eth)])
    else:
        print("  Skipping ETH funding (not available on this fork)")

    # Transfer USDC from whale
    usdc_address = get_token_address_normalized(chain, "USDC")
    usdc_amount = 100_000 * (10**6)
    usdc_token = fetch_erc20_details(web3, usdc_address)
    usdc_token.contract.functions.transfer(wallet_address, usdc_amount).transact({"from": LARGE_USDC_HOLDER})
    balance = usdc_token.contract.functions.balanceOf(wallet_address).call()
    print(f"  USDC balance: {balance / 10**6:,.2f} USDC")

    # Transfer WETH from whale
    weth_address = get_token_address_normalized(chain, "ETH")  # GMX uses "ETH" for WETH
    weth_amount = 10 * (10**18)
    weth_token = fetch_erc20_details(web3, weth_address)
    weth_token.contract.functions.transfer(wallet_address, weth_amount).transact({"from": LARGE_WETH_HOLDER})
    balance = weth_token.contract.functions.balanceOf(wallet_address).call()
    print(f"  WETH balance: {balance / 10**18:.6f} WETH")

    # Sync wallet nonce (whale transfers may have advanced block)
    wallet.sync_nonce(web3)

    # Note: Token approvals are handled automatically by the GMX CCXT wrapper

    return chain


def test_order_creation_with_wallet(web3: Web3, wallet: HotWallet):
    """Test creating and executing an order with wallet (Mode B)."""
    print("\n" + "=" * 80)
    print("Test 1: Creating Order with Wallet (Mode B)")
    print("=" * 80)

    config = GMXConfig(web3=web3, user_wallet_address=wallet.address)
    gmx = GMX(config, wallet=wallet)

    # Load markets
    gmx.load_markets()
    print(f"Loaded {len(gmx.markets)} markets")

    # Create market buy order
    print("\nCreating market buy order for ETH/USD...")
    print("Using parameters similar to debug.py for compatibility...")
    try:
        order = gmx.create_market_buy_order(
            "ETH/USD",
            10.0,  # $10 USD position size (same as debug.py)
            {
                "leverage": 2.5,  # Same as debug.py
                "collateral_symbol": "ETH",  # Same as debug.py (using ETH not USDC)
                "slippage_percent": 0.005,  # Same as debug.py
                "execution_buffer": 2.2,  # Same as debug.py
            },
        )

        print("\nOrder created successfully!")
        print(f"  Status: {order['status']}")
        print(f"  ID (TX Hash): {order['id']}")
        print(f"  Symbol: {order['symbol']}")
        print(f"  Side: {order['side']}")
        print(f"  Amount: {order['amount']}")
        print(f"  Execution Fee: {order['fee']['cost']:.6f} ETH")
        print(f"  Block: {order['info']['block_number']}")
        print(f"  Gas Used: {order['info']['gas_used']:,}")

        # Check if transaction was successful
        receipt = order["info"]["receipt"]
        if receipt["status"] != 1:
            print("\nTransaction reverted! Checking reason...")
            try:
                assert_transaction_success_with_explanation(web3, order["id"])
            except Exception as trace_error:
                print(f"Transaction revert reason: {trace_error}")
                raise

        # Verify order structure
        assert order["status"] == "open", f"Expected open, got {order['status']}"
        assert order["id"] is not None, "ID (tx_hash) should be present"
        assert "tx_hash" in order["info"], "Missing tx_hash"
        assert "receipt" in order["info"], "Missing receipt"
        assert "execution_fee" in order["info"], "Missing execution_fee"

        # Execute order as keeper
        print("\nExecuting order as keeper...")
        order_key = extract_order_key_from_receipt(order["info"]["receipt"])
        exec_receipt, keeper_address = execute_order_as_keeper(web3, order_key)
        print(f"  Order executed by keeper in block {exec_receipt['blockNumber']}")

        # Verify position was created
        print("\nVerifying position was created...")
        config = GMXConfig(web3=web3, user_wallet_address=wallet.address)
        position_verifier = GetOpenPositions(config)
        open_positions = position_verifier.get_data(wallet.address)

        if open_positions:
            print(f"  Found {len(open_positions)} position(s)")

            for idx, (position_key, position) in enumerate(open_positions.items(), 1):
                market_symbol = position.get("market_symbol", "Unknown")
                is_long = position.get("is_long", False)
                direction = "LONG" if is_long else "SHORT"
                collateral_token = position.get("collateral_token", "Unknown")

                position_size = position.get("position_size", 0)
                initial_collateral_amount = position.get("initial_collateral_amount", 0)
                initial_collateral_amount_usd = position.get("initial_collateral_amount_usd", 0)
                entry_price = position.get("entry_price", 0)
                mark_price = position.get("mark_price", 0)
                leverage_val = position.get("leverage", 0)

                token_decimals = 18 if "ETH" in collateral_token.upper() else 6
                collateral_amount = initial_collateral_amount / (10**token_decimals)

                print(f"\n  Position #{idx}:")
                print(f"    Market:           {market_symbol}/USD")
                print(f"    Direction:        {direction}")
                print(f"    Collateral Token: {collateral_token}")
                print(f"    Position Size:    ${position_size:,.2f}")
                print(f"    Collateral:       {collateral_amount:.6f} {collateral_token}")
                print(f"    Collateral Value: ${initial_collateral_amount_usd:,.2f}")
                print(f"    Leverage:         {leverage_val:.2f}x")
                print(f"    Entry Price:      ${entry_price:,.2f}")
                print(f"    Mark Price:       ${mark_price:,.2f}")

                # Verify position matches order parameters
                assert position["market_symbol"] == "ETH", "Position market should be ETH"
                assert position["is_long"] is True, "Position should be long"
                assert position["leverage"] > 0, "Leverage should be > 0"
                print(f"\n  Position details verified")
        else:
            print("  No positions found - order may not have been executed properly")
            return False

        print("\nOrder creation with wallet works correctly")
        return True

    except Exception as e:
        print(f"\nError creating order: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_parameter_conversion(web3: Web3, wallet: HotWallet):
    """Test that CCXT parameters are correctly converted to GMX parameters.

    Verifies that CCXT adapter produces the same GMX parameters as debug.py uses:
    - market_symbol="ETH"
    - collateral_symbol="ETH"
    - start_token_symbol="ETH"
    - is_long=True
    - size_delta_usd=10
    - leverage=2.5
    - slippage_percent=0.005
    - execution_buffer=2.2
    """
    print("\n" + "=" * 80)
    print("Test 2: Parameter Conversion")
    print("=" * 80)

    config = GMXConfig(web3=web3, user_wallet_address=wallet.address)
    gmx = GMX(config, wallet=wallet)
    gmx.load_markets()

    # Test parameter conversion
    ccxt_params = {
        "leverage": 2.5,
        "collateral_symbol": "ETH",
        "slippage_percent": 0.005,
        "execution_buffer": 2.2,
    }

    gmx_params = gmx._convert_ccxt_to_gmx_params("ETH/USD", "market", "buy", 10.0, None, ccxt_params)

    print("\nCCXT Parameters:")
    print(ccxt_params)

    print("\nConverted GMX Parameters:")
    print(gmx_params)

    # Verify conversion
    assert gmx_params["market_symbol"] == "ETH", "Market symbol should be ETH"
    assert gmx_params["is_long"] is True, "Should be long for buy side"
    assert gmx_params["size_delta_usd"] == 10.0, "Size should match"
    assert gmx_params["leverage"] == 2.5, "Leverage should match"
    assert gmx_params["collateral_symbol"] == "ETH", "Collateral should match"
    assert gmx_params["start_token_symbol"] == "ETH", "Start token should match collateral"
    assert gmx_params["slippage_percent"] == 0.005, "Slippage should match"
    assert gmx_params["execution_buffer"] == 2.2, "Execution buffer should match"

    print("\nParameter conversion works correctly")
    return True


def test_error_handling(web3: Web3):
    """Test that order creation fails without wallet."""
    print("\n" + "=" * 80)
    print("Test 3: Error Handling (No Wallet)")
    print("=" * 80)

    config = GMXConfig(web3=web3)
    gmx_no_wallet = GMX(config)  # No wallet provided
    gmx_no_wallet.load_markets()

    try:
        order = gmx_no_wallet.create_market_buy_order("ETH/USD", 100.0)
        print("Should have raised ValueError")
        return False
    except ValueError as e:
        print(f"Correctly raised ValueError: {e}")
        return True


def test_unsupported_methods(web3: Web3, wallet: HotWallet):
    """Test that unsupported methods raise correct error types.

    - NotSupported: GMX protocol doesn't support this feature
    - NotImplementedError: Feature not yet coded but could be in future
    """
    print("\n" + "=" * 80)
    print("Test 4: Unsupported Methods")
    print("=" * 80)

    config = GMXConfig(web3=web3, user_wallet_address=wallet.address)
    gmx = GMX(config, wallet=wallet)
    gmx.load_markets()

    # Methods that GMX protocol doesn't support (should raise NotSupported)
    protocol_unsupported = [
        ("cancel_order", lambda: gmx.cancel_order("0x123"), NotSupported),
        ("fetch_order", lambda: gmx.fetch_order("0x123"), NotSupported),
        ("fetch_order_book", lambda: gmx.fetch_order_book("ETH/USD"), NotSupported),
        ("fetch_closed_orders", lambda: gmx.fetch_closed_orders(), NotSupported),
        ("fetch_orders", lambda: gmx.fetch_orders(), NotSupported),
    ]

    # Methods not yet implemented but could be (should raise NotImplementedError)
    not_implemented = [
        ("add_margin", lambda: gmx.add_margin("ETH/USD", 1000.0), NotImplementedError),
        ("reduce_margin", lambda: gmx.reduce_margin("ETH/USD", 500.0), NotImplementedError),
    ]

    all_passed = True

    print("\nProtocol limitations (NotSupported):")
    for name, func, expected_error in protocol_unsupported:
        try:
            func()
            print(f"  {name} should raise {expected_error.__name__}")
            all_passed = False
        except expected_error as e:
            print(f"  {name}: {str(e)[:60]}...")
        except Exception as e:
            print(f"  {name} raised {type(e).__name__} instead of {expected_error.__name__}")
            all_passed = False

    print("\nNot yet implemented (NotImplementedError):")
    for name, func, expected_error in not_implemented:
        try:
            func()
            print(f"  {name} should raise {expected_error.__name__}")
            all_passed = False
        except expected_error as e:
            print(f"  {name}: {str(e)[:60]}...")
        except Exception as e:
            print(f"  {name} raised {type(e).__name__} instead of {expected_error.__name__}")
            all_passed = False

    return all_passed


def main():
    """Run all tests."""
    parser = argparse.ArgumentParser(description="GMX CCXT Order Creation Tests")
    parser.add_argument("--tenderly", action="store_true", help="Use Tenderly fork (requires TD_ARB env var)")
    parser.add_argument("--rpc", type=str, help="Custom RPC URL (for testing on existing fork)")
    args = parser.parse_args()

    print("\n" + "=" * 80)
    print("GMX CCXT Order Creation Tests")
    print("=" * 80)

    # Determine mode and RPC URL
    if args.tenderly:
        rpc_url = os.environ.get("TD_ARB")
        if not rpc_url:
            print("Error: TD_ARB environment variable not set")
            print("Set it to your Tenderly fork URL:")
            print("  export TD_ARB=https://rpc.tenderly.co/fork/...")
            return 1
        mode = "Tenderly"
        launch = None
    elif args.rpc:
        rpc_url = args.rpc
        mode = "Custom RPC"
        launch = None
    else:
        # Default: Anvil fork
        rpc_url = os.environ.get("ARBITRUM_CHAIN_JSON_RPC", "https://arb1.arbitrum.io/rpc")
        mode = "Anvil (Automatic)"
        launch = None

    private_key = os.environ.get("PRIVATE_KEY", "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")

    print(f"Mode: {mode}")
    if not args.tenderly and not args.rpc:
        print(f"Fork source: {rpc_url}")
        print(f"Fork block: {FORK_BLOCK}")

    try:
        # Setup connection
        if not args.tenderly and not args.rpc:
            # Launch Anvil fork
            print(f"\nLaunching Anvil fork at block {FORK_BLOCK}...")
            launch = fork_network_anvil(
                rpc_url,
                unlocked_addresses=[LARGE_USDC_HOLDER, LARGE_WETH_HOLDER],
                fork_block_number=FORK_BLOCK,
            )
            rpc_url = launch.json_rpc_url
            print(f"  Anvil fork started on {rpc_url}")
        else:
            print(f"\nConnecting to: {rpc_url}")

        # Connect to fork
        web3 = create_multi_provider_web3(
            rpc_url,
            default_http_timeout=(3.0, 180.0),
        )

        if not web3.is_connected():
            print("Failed to connect to RPC")
            return 1

        print(f"  Connected (block: {web3.eth.block_number})")

        # Create wallet
        wallet = HotWallet.from_private_key(private_key)
        wallet.sync_nonce(web3)
        print(f"  Wallet: {wallet.address}")

        # Setup fork environment
        setup_fork_environment(web3, wallet.address, wallet)

        # Run tests
        results = [
            ("Order Creation with Wallet", test_order_creation_with_wallet(web3, wallet)),
            ("Parameter Conversion", test_parameter_conversion(web3, wallet)),
            ("Error Handling", test_error_handling(web3)),
            ("Unsupported Methods", test_unsupported_methods(web3, wallet)),
        ]

        # Print summary
        print("\n" + "=" * 80)
        print("Test Summary")
        print("=" * 80)

        for test_name, passed in results:
            status = "[PASS]" if passed else "[FAIL]"
            print(f"  {test_name}: {status}")

        all_passed = all(result[1] for result in results)

        if all_passed:
            print("\nAll tests PASSED")
            return 0
        else:
            print("\nSome tests FAILED")
            return 1

    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
        return 1

    finally:
        if launch:
            print("\nShutting down Anvil fork...")
            launch.close()


if __name__ == "__main__":
    exit(main())
