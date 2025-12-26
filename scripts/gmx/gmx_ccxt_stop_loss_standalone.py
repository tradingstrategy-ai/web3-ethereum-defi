"""GMX CCXT Standalone Stop-Loss and Take-Profit Example.

This script demonstrates adding stop-loss and take-profit orders to existing positions.
This is useful when you want to add protective orders after a position is already open,
or when you want more granular control over SL/TP placement.

The standalone approach allows you to:
- Add SL/TP to positions opened without them
- Modify existing SL/TP levels (by creating new orders)
- Add partial SL/TP (close only percentage of position)

Usage:
    # Anvil mode (automatic fork):
    export PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
    python scripts/gmx/gmx_ccxt_stop_loss_standalone.py

    # Tenderly mode (for better debugging):
    export PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
    export TD_ARB=<your_tenderly_fork_url>
    python scripts/gmx/gmx_ccxt_stop_loss_standalone.py --tenderly
"""

import argparse
import os

from eth_utils import to_checksum_address
from web3 import Web3
from rich.console import Console

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
    weth_address = get_token_address_normalized(chain, "ETH")
    weth_amount = 10 * (10**18)
    weth_token = fetch_erc20_details(web3, weth_address)
    weth_token.contract.functions.transfer(wallet_address, weth_amount).transact({"from": LARGE_WETH_HOLDER})
    balance = weth_token.contract.functions.balanceOf(wallet_address).call()
    print(f"  WETH balance: {balance / 10**18:.6f} WETH")

    # Sync wallet nonce
    wallet.sync_nonce(web3)

    # Note: Token approvals are handled automatically by the GMX CCXT wrapper

    return chain


def open_position_without_sltp(web3: Web3, wallet: HotWallet):
    """Open a position without SL/TP to demonstrate adding them later."""
    print("\n" + "=" * 80)
    print("Step 1: Opening Position Without SL/TP")
    print("=" * 80)

    config = GMXConfig(web3=web3, user_wallet_address=wallet.address)
    gmx = GMX(config, wallet=wallet)
    gmx.load_markets()

    # Create simple market buy order (no SL/TP)
    print("\nCreating position without SL/TP...")
    try:
        order = gmx.create_market_buy_order(
            "ETH/USDC:USDC",
            10.0,  # $10 USD position size
            {
                "leverage": 2.5,
                "collateral_symbol": "ETH",
                "slippage_percent": 0.005,
                "execution_buffer": 30,
            },
        )

        print("\n[green]Position opened successfully![/green]")
        print(f"  TX Hash: {order['id']}")

        # Execute order as keeper
        print("\nExecuting order as keeper...")
        order_key = extract_order_key_from_receipt(order["info"]["receipt"])
        exec_receipt, keeper_address = execute_order_as_keeper(web3, order_key)
        print(f"  Order executed in block {exec_receipt['blockNumber']}")

        # Verify position was created
        print("\nVerifying position...")
        positions_manager = GetOpenPositions(config)
        open_positions = positions_manager.get_data(wallet.address)

        if open_positions:
            for idx, (position_key, position) in enumerate(open_positions.items(), 1):
                market = position.get("market_symbol", "Unknown")
                direction = "LONG" if position.get("is_long", False) else "SHORT"
                size = position.get("position_size", 0)
                entry_price = position.get("entry_price", 0)

                print(f"\n  Position #{idx}:")
                print(f"    Market: {market}/USD")
                print(f"    Direction: {direction}")
                print(f"    Size: ${size:,.2f}")
                print(f"    Entry Price: ${entry_price:,.2f}")

            print("\n[green]Position ready for adding SL/TP![/green]")
            return True, entry_price
        else:
            print("[red]No position found![/red]")
            return False, 0

    except Exception as e:
        print(f"\n[red]Error: {e}[/red]")
        import traceback

        traceback.print_exc()
        return False, 0


def test_standalone_stop_loss(web3: Web3, wallet: HotWallet, entry_price: float):
    """Test adding standalone stop-loss order to existing position."""
    print("\n" + "=" * 80)
    print("Step 2: Adding Standalone Stop-Loss Order")
    print("=" * 80)

    config = GMXConfig(web3=web3, user_wallet_address=wallet.address)
    gmx = GMX(config, wallet=wallet)
    gmx.load_markets()

    # Calculate stop loss price (5% below entry)
    stop_loss_price = entry_price * 0.95
    print(f"\nEntry Price: ${entry_price:,.2f}")
    print(f"Stop Loss Price: ${stop_loss_price:,.2f} (-5%)")

    # Create standalone stop-loss order
    print("\nCreating standalone stop-loss order...")
    try:
        sl_order = gmx.create_order(
            "ETH/USDC:USDC",
            "stop_loss",  # CCXT order type for stop loss
            "sell",  # Sell to close long position
            10.0,  # Amount in USD
            None,  # No price for market stop loss
            {
                "triggerPrice": stop_loss_price,
                "collateral_symbol": "ETH",
                "slippage_percent": 0.005,
                "execution_buffer": 30,
            },
        )

        print("\n[green]Stop-loss order created successfully![/green]")
        print(f"  TX Hash: {sl_order['id']}")
        print(f"  Order Type: {sl_order['type']}")
        print(f"  Trigger Price: ${sl_order['price']:,.2f}")
        print(f"  Execution Fee: {sl_order['fee']['cost']:.6f} ETH")

        # Check transaction success
        receipt = sl_order["info"]["receipt"]
        if receipt["status"] != 1:
            print("\n[red]Transaction reverted![/red]")
            assert_transaction_success_with_explanation(web3, sl_order["id"])
            return False

        print("\n[green]Standalone stop-loss works correctly![/green]")
        return True

    except Exception as e:
        print(f"\n[red]Error: {e}[/red]")
        import traceback

        traceback.print_exc()
        return False


def test_standalone_take_profit(web3: Web3, wallet: HotWallet, entry_price: float):
    """Test adding standalone take-profit order to existing position."""
    print("\n" + "=" * 80)
    print("Step 3: Adding Standalone Take-Profit Order")
    print("=" * 80)

    config = GMXConfig(web3=web3, user_wallet_address=wallet.address)
    gmx = GMX(config, wallet=wallet)
    gmx.load_markets()

    # Calculate take profit price (10% above entry)
    take_profit_price = entry_price * 1.10
    print(f"\nEntry Price: ${entry_price:,.2f}")
    print(f"Take Profit Price: ${take_profit_price:,.2f} (+10%)")

    # Create standalone take-profit order with partial close
    print("\nCreating standalone take-profit order (50% partial close)...")
    try:
        tp_order = gmx.create_order(
            "ETH/USDC:USDC",
            "take_profit",  # CCXT order type for take profit
            "sell",  # Sell to close long position
            5.0,  # Amount: 50% of position ($5 out of $10)
            None,
            {
                "triggerPrice": take_profit_price,
                "collateral_symbol": "ETH",
                "closePercent": 0.5,  # GMX extension: close 50% of position
                "slippage_percent": 0.005,
                "execution_buffer": 30,
            },
        )

        print("\n[green]Take-profit order created successfully![/green]")
        print(f"  TX Hash: {tp_order['id']}")
        print(f"  Order Type: {tp_order['type']}")
        print(f"  Trigger Price: ${tp_order['price']:,.2f}")
        print(f"  Close Amount: 50% of position")
        print(f"  Execution Fee: {tp_order['fee']['cost']:.6f} ETH")

        # Check transaction success
        receipt = tp_order["info"]["receipt"]
        if receipt["status"] != 1:
            print("\n[red]Transaction reverted![/red]")
            assert_transaction_success_with_explanation(web3, tp_order["id"])
            return False

        print("\n[green]Standalone take-profit works correctly![/green]")
        return True

    except Exception as e:
        print(f"\n[red]Error: {e}[/red]")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Run standalone SL/TP examples."""
    parser = argparse.ArgumentParser(description="GMX CCXT Standalone SL/TP Examples")
    parser.add_argument("--tenderly", action="store_true", help="Use Tenderly fork (requires TD_ARB env var)")
    parser.add_argument("--rpc", type=str, help="Custom RPC URL")
    args = parser.parse_args()

    print("\n" + "=" * 80)
    print("GMX CCXT Standalone Stop-Loss and Take-Profit Examples")
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
            # print(f"\nConnecting to: {rpc_url}")
            pass
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

        # Step 1: Open position without SL/TP
        position_opened, entry_price = open_position_without_sltp(web3, wallet)
        if not position_opened:
            print("\n[red]Failed to open position[/red]")
            return 1

        # Step 2: Add standalone stop-loss
        sl_success = test_standalone_stop_loss(web3, wallet, entry_price)

        # Step 3: Add standalone take-profit
        tp_success = test_standalone_take_profit(web3, wallet, entry_price)

        # Print summary
        print("\n" + "=" * 80)
        print("Example Summary")
        print("=" * 80)

        results = [
            ("Open Position", position_opened),
            ("Add Standalone Stop-Loss", sl_success),
            ("Add Standalone Take-Profit", tp_success),
        ]

        for step_name, passed in results:
            status = "[green][PASS][/green]" if passed else "[red][FAIL][/red]"
            print(f"  {step_name}: {status}")

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
