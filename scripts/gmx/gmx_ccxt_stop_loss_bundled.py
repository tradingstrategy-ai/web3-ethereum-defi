"""GMX CCXT Bundled Stop-Loss and Take-Profit Example.

This script demonstrates creating a position with automatic stop-loss and take-profit
orders in a single atomic transaction. This is the recommended approach for opening
new positions with risk management.

The bundled approach ensures SL/TP orders are created atomically with the main position,
preventing scenarios where the position opens but protective orders fail.

Usage:
    # Anvil mode (automatic fork):
    export PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
    python scripts/gmx/gmx_ccxt_stop_loss_bundled.py

    # Tenderly mode (for better debugging):
    export PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
    export TD_ARB=<your_tenderly_fork_url>
    python scripts/gmx/gmx_ccxt_stop_loss_bundled.py --tenderly
"""

import argparse
import logging
import os

from eth_utils import to_checksum_address
from web3 import Web3
from rich.console import Console
from rich.logging import RichHandler

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

# Configure logging to show approval messages
logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[RichHandler(console=Console(), show_time=False, show_path=False)])

print = Console().print

# Fork test configuration
FORK_BLOCK = 392496384
LARGE_USDC_HOLDER = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
LARGE_WETH_HOLDER = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")


def setup_fork_environment(web3: Web3, wallet_address: str, wallet: HotWallet):
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

    # Sync wallet nonce
    wallet.sync_nonce(web3)

    # Note: Token approvals are handled automatically by the GMX CCXT wrapper

    return chain


def test_bundled_sltp_unified_style(web3: Web3, wallet: HotWallet):
    """Test bundled SL/TP using CCXT unified style (stopLossPrice, takeProfitPrice)."""
    print("\n" + "=" * 80)
    print("Test 1: Bundled SL/TP - CCXT Unified Style")
    print("=" * 80)

    config = GMXConfig(web3=web3, user_wallet_address=wallet.address)
    gmx = GMX(config, wallet=wallet)
    gmx.load_markets()

    # Fetch current price to calculate SL/TP levels
    ticker = gmx.fetch_ticker("ETH/USDC:USDC")
    current_price = ticker["last"]
    print(f"\nCurrent ETH price: ${current_price:,.2f}")

    # Calculate SL/TP prices (5% stop loss, 10% take profit)
    stop_loss_price = current_price * 0.95  # 5% below
    take_profit_price = current_price * 1.10  # 10% above

    print(f"Stop Loss Price: ${stop_loss_price:,.2f} (-5%)")
    print(f"Take Profit Price: ${take_profit_price:,.2f} (+10%)")

    # Create market buy order with bundled SL/TP (CCXT unified style)
    print("\nCreating position with bundled SL/TP (unified style)...")
    print("[dim]Note: Token approvals will be checked and executed automatically if needed[/dim]\n")
    try:
        order = gmx.create_market_buy_order(
            "ETH/USDC:USDC",
            10.0,  # $10 USD position size
            {
                "leverage": 2.5,
                "collateral_symbol": "ETH",
                "slippage_percent": 0.005,
                "execution_buffer": 2.2,
                # CCXT unified style
                "stopLossPrice": stop_loss_price,
                "takeProfitPrice": take_profit_price,
            },
        )

        print("\n[green]Order created successfully![/green]")
        print(f"  Status: {order['status']}")
        print(f"  TX Hash: {order['id']}")
        print(f"  Symbol: {order['symbol']}")
        print(f"  Side: {order['side']}")
        print(f"  Amount: ${order['amount']}")
        print(f"  Execution Fee: {order['fee']['cost']:.6f} ETH")

        # Show SL/TP info
        if order["info"].get("has_stop_loss"):
            print(f"\n  Stop Loss:")
            print(f"    Trigger Price: ${order['info']['stop_loss_trigger']:,.2f}")
            print(f"    Execution Fee: {order['info']['stop_loss_tx']['execution_fee'] / 10**18:.6f} ETH")

        if order["info"].get("has_take_profit"):
            print(f"\n  Take Profit:")
            print(f"    Trigger Price: ${order['info']['take_profit_trigger']:,.2f}")
            print(f"    Execution Fee: {order['info']['take_profit_tx']['execution_fee'] / 10**18:.6f} ETH")

        # Check if transaction was successful
        receipt = order["info"]["receipt"]
        if receipt["status"] != 1:
            print("\n[red]Transaction reverted! Checking reason...[/red]")
            try:
                assert_transaction_success_with_explanation(web3, order["id"])
            except Exception as trace_error:
                print(f"Transaction revert reason: {trace_error}")
                raise

        # Execute order as keeper
        print("\nExecuting main order as keeper...")
        order_key = extract_order_key_from_receipt(order["info"]["receipt"])
        exec_receipt, keeper_address = execute_order_as_keeper(web3, order_key)
        print(f"  Order executed in block {exec_receipt['blockNumber']}")

        print("\n[green]Bundled SL/TP (unified style) works correctly![/green]")
        return True

    except Exception as e:
        print(f"\n[red]Error: {e}[/red]")
        import traceback

        traceback.print_exc()
        return False


def test_bundled_sltp_object_style(web3: Web3, wallet: HotWallet):
    """Test bundled SL/TP using CCXT object style with GMX extensions."""
    print("\n" + "=" * 80)
    print("Test 2: Bundled SL/TP - CCXT Object Style with GMX Extensions")
    print("=" * 80)

    config = GMXConfig(web3=web3, user_wallet_address=wallet.address)
    gmx = GMX(config, wallet=wallet)
    gmx.load_markets()

    # Create market buy order with bundled SL/TP (CCXT object style + GMX extensions)
    print("\nCreating position with bundled SL/TP (object style + GMX extensions)...")
    try:
        order = gmx.create_market_buy_order(
            "ETH/USDC:USDC",
            10.0,  # $10 USD position size
            {
                "leverage": 2.5,
                "collateral_symbol": "ETH",
                "slippage_percent": 0.005,
                "execution_buffer": 2.2,
                # CCXT object style with GMX extensions
                "stopLoss": {
                    "triggerPercent": 0.05,  # GMX extension: 5% below entry
                    "closePercent": 1.0,  # GMX extension: close 100% of position
                    "autoCancel": True,  # GMX extension: cancel if main order fails
                },
                "takeProfit": {
                    "triggerPercent": 0.10,  # GMX extension: 10% above entry
                    "closePercent": 0.5,  # GMX extension: close 50% of position
                    "autoCancel": True,
                },
            },
        )

        print("\n[green]Order created successfully![/green]")
        print(f"  Status: {order['status']}")
        print(f"  TX Hash: {order['id']}")

        # Show SL/TP info
        print("\n  Stop Loss:")
        print("    Trigger: 5% below entry price")
        print("    Close: 100% of position")

        print("\n  Take Profit:")
        print("    Trigger: 10% above entry price")
        print("    Close: 50% of position (partial TP)")

        # Execute order as keeper
        print("\nExecuting main order as keeper...")
        order_key = extract_order_key_from_receipt(order["info"]["receipt"])
        exec_receipt, keeper_address = execute_order_as_keeper(web3, order_key)
        print(f"  Order executed in block {exec_receipt['blockNumber']}")

        print("\n[green]Bundled SL/TP (object style + GMX extensions) works correctly![/green]")
        return True

    except Exception as e:
        print(f"\n[red]Error: {e}[/red]")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Run bundled SL/TP examples."""
    parser = argparse.ArgumentParser(description="GMX CCXT Bundled SL/TP Examples")
    parser.add_argument("--tenderly", action="store_true", help="Use Tenderly fork (requires TD_ARB env var)")
    parser.add_argument("--rpc", type=str, help="Custom RPC URL")
    args = parser.parse_args()

    print("\n" + "=" * 80)
    print("GMX CCXT Bundled Stop-Loss and Take-Profit Examples")
    print("=" * 80)

    # Determine mode and RPC URL
    if args.tenderly:
        rpc_url = os.environ.get("TD_ARB")
        if not rpc_url:
            print("[red]Error: TD_ARB environment variable not set[/red]")
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
            print("[red]Failed to connect to RPC[/red]")
            return 1

        print(f"  Connected (block: {web3.eth.block_number})")

        # Create wallet
        wallet = HotWallet.from_private_key(private_key)
        wallet.sync_nonce(web3)
        print(f"  Wallet: {wallet.address}")

        # Setup fork environment
        setup_fork_environment(web3, wallet.address, wallet)

        # Run examples
        results = [
            ("Bundled SL/TP - Unified Style", test_bundled_sltp_unified_style(web3, wallet)),
            ("Bundled SL/TP - Object Style", test_bundled_sltp_object_style(web3, wallet)),
        ]

        # Print summary
        print("\n" + "=" * 80)
        print("Example Summary")
        print("=" * 80)

        for example_name, passed in results:
            status = "[green][PASS][/green]" if passed else "[red][FAIL][/red]"
            print(f"  {example_name}: {status}")

        all_passed = all(result[1] for result in results)

        if all_passed:
            print("\n[green]All examples PASSED[/green]")
            return 0
        else:
            print("\n[red]Some examples FAILED[/red]")
            return 1

    except Exception as e:
        print(f"\n[red]Error: {e}[/red]")
        import traceback

        traceback.print_exc()
        return 1

    finally:
        if launch:
            print("\nShutting down Anvil fork...")
            launch.close()


if __name__ == "__main__":
    exit(main())
