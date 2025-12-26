"""GMX CCXT Stop-Loss and Take-Profit Test - Fork Testing

Creates and executes GMX positions with bundled SL/TP orders using CCXT interface on forked networks.

MODES:
1. Anvil fork (default):   python tests/gmx/debug_ccxt_sltp.py --fork
2. Tenderly fork:          python tests/gmx/debug_ccxt_sltp.py --td
3. Custom Anvil RPC:       python tests/gmx/debug_ccxt_sltp.py --anvil-rpc http://localhost:8545

Required environment variables:
- PRIVATE_KEY: Private key for signing transactions
- ARBITRUM_CHAIN_JSON_RPC: RPC endpoint for Anvil fork (mainnet URL)
- TD_ARB: Tenderly fork URL (for --td mode)
"""

import argparse
import logging
import os
import sys
import time

from eth_utils import to_checksum_address
from rich.console import Console
from rich.logging import RichHandler
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.gmx.ccxt import GMX
from eth_defi.gmx.contracts import get_token_address_normalized
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from tests.gmx.fork_helpers import execute_order_as_keeper, extract_order_key_from_receipt, fetch_on_chain_oracle_prices, setup_mock_oracle

# Configure logging to show detailed output from fork_helpers
FORMAT = "%(message)s"
logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])

logger = logging.getLogger("rich")

console = Console()

# Fork test configuration
FORK_BLOCK = 392496384
LARGE_USDC_HOLDER = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
LARGE_WETH_HOLDER = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")
EXECUTION_BUFFER = 30


def setup_fork_network(web3: Web3):
    """Setup mock oracle and display network info.

    Follows GMX forked-env-example pattern:
    - Fetches actual on-chain oracle prices before mocking
    - This ensures prices pass GMX's validation
    """
    block_number = web3.eth.block_number
    chain_id = web3.eth.chain_id
    chain = get_chain_name(chain_id).lower()

    console.print(f"  Block: {block_number}")
    console.print(f"  Chain ID: {chain_id}")
    console.print(f"  Chain: {chain}")

    # Setup mock oracle - prices fetched dynamically from chain
    console.print("\n[dim]Setting up mock oracle (fetching on-chain prices)...[/dim]")
    setup_mock_oracle(web3)  # No hardcoded prices - fetches from chain automatically
    console.print(f"[dim]Mock oracle configured with on-chain prices[/dim]\n")

    return chain


def fund_wallet_anvil(web3: Web3, wallet_address: str, tokens: dict):
    """Fund wallet on Anvil fork using anvil_setBalance and whale transfers."""
    console.print("\n[bold]Funding wallet (Anvil mode)...[/bold]")

    # Set ETH balance for wallet
    eth_amount_wei = 100 * 10**18
    web3.provider.make_request("anvil_setBalance", [wallet_address, hex(eth_amount_wei)])
    console.print(f"  [green]ETH balance: 100 ETH[/green]")

    # Give whales some ETH for gas
    gas_eth = 1 * 10**18
    web3.provider.make_request("anvil_setBalance", [LARGE_USDC_HOLDER, hex(gas_eth)])
    web3.provider.make_request("anvil_setBalance", [LARGE_WETH_HOLDER, hex(gas_eth)])

    # Transfer USDC from whale
    usdc_address = tokens.get("USDC")
    if usdc_address:
        usdc_amount = 100_000 * (10**6)
        usdc_token = fetch_erc20_details(web3, usdc_address)
        usdc_token.contract.functions.transfer(wallet_address, usdc_amount).transact({"from": LARGE_USDC_HOLDER})
        balance = usdc_token.contract.functions.balanceOf(wallet_address).call()
        console.print(f"  [green]USDC balance: {balance / 10**6:.2f} USDC[/green]")

    # Transfer WETH from whale
    weth_address = tokens.get("WETH")
    if weth_address:
        weth_amount = 1000 * (10**18)
        weth_token = fetch_erc20_details(web3, weth_address)
        weth_token.contract.functions.transfer(wallet_address, weth_amount).transact({"from": LARGE_WETH_HOLDER})
        balance = weth_token.contract.functions.balanceOf(wallet_address).call()
        console.print(f"  [green]WETH balance: {balance / 10**18:.2f} WETH[/green]")


def fund_wallet_tenderly(web3: Web3, wallet_address: str, tokens: dict):
    """Fund wallet on Tenderly fork using Tenderly RPC methods."""
    console.print("\n[bold]Funding wallet (Tenderly mode)...[/bold]")

    # Set ETH balance
    eth_amount_wei = 100 * 10**18
    web3.provider.make_request("tenderly_setBalance", [wallet_address, hex(eth_amount_wei)])
    console.print(f"  [green]ETH balance: 100 ETH[/green]")

    # Set USDC balance
    usdc_address = tokens.get("USDC")
    if usdc_address:
        usdc_amount = 100_000 * (10**6)
        web3.provider.make_request("tenderly_setErc20Balance", [usdc_address, wallet_address, hex(usdc_amount)])
        console.print(f"  [green]USDC balance: 100,000 USDC[/green]")

    # Set WETH balance
    weth_address = tokens.get("WETH")
    if weth_address:
        weth_amount = 1000 * (10**18)
        web3.provider.make_request("tenderly_setErc20Balance", [weth_address, wallet_address, hex(weth_amount)])
        console.print(f"  [green]WETH balance: 1,000 WETH[/green]")


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="GMX CCXT Stop-Loss/Take-Profit Test - Fork Testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Fork mode options (mutually exclusive)
    fork_group = parser.add_mutually_exclusive_group()
    fork_group.add_argument("--fork", action="store_true", help="Create Anvil fork (default)")
    fork_group.add_argument("--td", action="store_true", help="Use Tenderly fork (requires TD_ARB env var)")
    fork_group.add_argument("--anvil-rpc", type=str, help="Connect to existing Anvil RPC (e.g., http://127.0.0.1:8545)")

    # Position size override
    parser.add_argument("--size", type=float, default=10.0, help="Position size in USD (default: 10)")

    # Test mode selection
    parser.add_argument("--test-mode", type=str, choices=["unified", "object", "both"], default="both",
                       help="Test mode: unified (stopLossPrice), object (stopLoss object), or both (default)")

    return parser.parse_args()


def test_unified_style(gmx: GMX, symbol: str, size_usd: float, leverage: float, web3: Web3):
    """Test bundled SL/TP using CCXT unified style (stopLossPrice, takeProfitPrice)."""
    console.print("\n" + "=" * 80)
    console.print("[bold]Test 1: Bundled SL/TP - CCXT Unified Style[/bold]")
    console.print("=" * 80)

    # Fetch current price to calculate SL/TP levels
    ticker = gmx.fetch_ticker(symbol)
    current_price = ticker["last"]
    console.print(f"\nCurrent ETH price: ${current_price:,.2f}")

    # Calculate SL/TP prices (5% stop loss, 10% take profit)
    stop_loss_price = current_price * 0.95  # 5% below
    take_profit_price = current_price * 1.10  # 10% above

    console.print(f"Stop Loss Price: ${stop_loss_price:,.2f} (-5%)")
    console.print(f"Take Profit Price: ${take_profit_price:,.2f} (+10%)")

    # Create market buy order with bundled SL/TP (CCXT unified style)
    console.print("\n[cyan]Creating position with bundled SL/TP (unified style)...[/cyan]")
    console.print("[dim]Note: Token approvals will be checked and executed automatically if needed[/dim]\n")

    try:
        order = gmx.create_market_buy_order(
            symbol,
            size_usd,
            {
                "leverage": leverage,
                "collateral_symbol": "USDC",  # Use USDC to avoid wstETH complexity
                "slippage_percent": 0.005,
                "execution_buffer": EXECUTION_BUFFER,
                # CCXT unified style
                "stopLossPrice": stop_loss_price,
                "takeProfitPrice": take_profit_price,
            },
        )

        console.print("\n[green]Order created successfully![/green]")
        console.print(f"  Status: {order['status']}")
        console.print(f"  TX Hash: {order['id']}")
        console.print(f"  Symbol: {order['symbol']}")
        console.print(f"  Side: {order['side']}")
        console.print(f"  Amount: ${order['amount']}")
        console.print(f"  Execution Fee: {order['fee']['cost']:.6f} ETH")

        # Show SL/TP info
        if order["info"].get("has_stop_loss"):
            console.print(f"\n[yellow]  Stop Loss:[/yellow]")
            console.print(f"    Trigger Price: ${order['info']['stop_loss_trigger']:,.2f}")
            console.print(f"    Execution Fee: {order['info']['stop_loss_fee'] / 10**18:.6f} ETH")

        if order["info"].get("has_take_profit"):
            console.print(f"\n[yellow]  Take Profit:[/yellow]")
            console.print(f"    Trigger Price: ${order['info']['take_profit_trigger']:,.2f}")
            console.print(f"    Execution Fee: {order['info']['take_profit_fee'] / 10**18:.6f} ETH")

        # Check if transaction was successful
        receipt = order["info"]["receipt"]
        if receipt["status"] != 1:
            console.print("\n[red]Transaction reverted! Checking reason...[/red]")
            try:
                assert_transaction_success_with_explanation(web3, order["id"])
            except Exception as trace_error:
                console.print(f"Transaction revert reason: {trace_error}")
                raise

        # Execute order as keeper
        console.print("\n[cyan]Executing main order as keeper...[/cyan]")
        try:
            order_key = extract_order_key_from_receipt(order["info"]["receipt"])
            exec_receipt, keeper_address = execute_order_as_keeper(web3, order_key)
            console.print(f"  [green]Order executed in block {exec_receipt['blockNumber']}[/green]")

            console.print("\n[green]✓ Bundled SL/TP (unified style) works correctly![/green]")
            return True
        except ValueError as e:
            console.print(f"\n[red]Could not execute order: {e}[/red]")
            console.print("[yellow]This may happen if the transaction failed or no order was created[/yellow]")
            return False

    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        import traceback
        traceback.print_exc()
        return False


def test_object_style(gmx: GMX, symbol: str, size_usd: float, leverage: float, web3: Web3):
    """Test bundled SL/TP using CCXT object style with GMX extensions."""
    console.print("\n" + "=" * 80)
    console.print("[bold]Test 2: Bundled SL/TP - CCXT Object Style with GMX Extensions[/bold]")
    console.print("=" * 80)

    # Create market buy order with bundled SL/TP (CCXT object style + GMX extensions)
    console.print("\n[cyan]Creating position with bundled SL/TP (object style + GMX extensions)...[/cyan]")
    console.print("[dim]Note: Token approvals will be checked and executed automatically if needed[/dim]\n")

    try:
        order = gmx.create_market_buy_order(
            symbol,
            size_usd,
            {
                "leverage": leverage,
                "collateral_symbol": "USDC",  # Use USDC to avoid wstETH complexity
                "slippage_percent": 0.005,
                "execution_buffer": EXECUTION_BUFFER,
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

        console.print("\n[green]Order created successfully![/green]")
        console.print(f"  Status: {order['status']}")
        console.print(f"  TX Hash: {order['id']}")

        # Show SL/TP info
        console.print("\n[yellow]  Stop Loss:[/yellow]")
        console.print("    Trigger: 5% below entry price")
        console.print("    Close: 100% of position")

        console.print("\n[yellow]  Take Profit:[/yellow]")
        console.print("    Trigger: 10% above entry price")
        console.print("    Close: 50% of position (partial TP)")

        # Check if transaction was successful
        receipt = order["info"]["receipt"]
        if receipt["status"] != 1:
            console.print("\n[red]Transaction reverted! Checking reason...[/red]")
            try:
                assert_transaction_success_with_explanation(web3, order["id"])
            except Exception as trace_error:
                console.print(f"Transaction revert reason: {trace_error}")
                return False

        # Execute order as keeper
        console.print("\n[cyan]Executing main order as keeper...[/cyan]")
        try:
            order_key = extract_order_key_from_receipt(order["info"]["receipt"])
            exec_receipt, keeper_address = execute_order_as_keeper(web3, order_key)
            console.print(f"  [green]Order executed in block {exec_receipt['blockNumber']}[/green]")

            console.print("\n[green]✓ Bundled SL/TP (object style + GMX extensions) works correctly![/green]")
            return True
        except ValueError as e:
            console.print(f"\n[red]Could not execute order: {e}[/red]")
            console.print("[yellow]This may happen if the transaction failed or no order was created[/yellow]")
            return False

    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main execution flow."""
    args = parse_arguments()

    private_key = os.environ.get("PRIVATE_KEY")
    if not private_key:
        console.print("[red]Error: PRIVATE_KEY environment variable not set[/red]")
        sys.exit(1)

    launch = None
    is_tenderly = False

    try:
        console.print("\n[bold green]=== GMX CCXT SL/TP Fork Test ===[/bold green]\n")

        # ========================================================================
        # STEP 1: Connect to Network
        # ========================================================================

        if args.td:
            # Tenderly fork mode
            tenderly_rpc = os.environ.get("TD_ARB")
            if not tenderly_rpc:
                console.print("[red]Error: TD_ARB environment variable not set[/red]")
                sys.exit(1)

            console.print("Using Tenderly fork...")
            web3 = create_multi_provider_web3(tenderly_rpc)
            is_tenderly = True

        elif args.anvil_rpc:
            # Custom Anvil RPC mode
            console.print(f"Using custom Anvil at {args.anvil_rpc}...")
            web3 = create_multi_provider_web3(args.anvil_rpc, default_http_timeout=(3.0, 180.0))

        else:
            # Anvil fork mode (default) - uses mainnet URL
            fork_rpc = os.environ.get("ARBITRUM_CHAIN_JSON_RPC")
            if not fork_rpc:
                console.print("[red]Error: ARBITRUM_CHAIN_JSON_RPC environment variable not set[/red]")
                console.print("[yellow]This should be set to an Arbitrum mainnet RPC URL[/yellow]")
                sys.exit(1)

            console.print(f"Creating Anvil fork from mainnet RPC...")
            launch = fork_network_anvil(
                fork_rpc,
                unlocked_addresses=[LARGE_USDC_HOLDER, LARGE_WETH_HOLDER],
                # NOTE: forking at an older block is throwing error while retrieving empty market data
                # fork_block_number=FORK_BLOCK,
            )

            web3 = create_multi_provider_web3(
                launch.json_rpc_url,
                default_http_timeout=(3.0, 180.0),
            )
            console.print(f"  Anvil fork started on {launch.json_rpc_url}")

        # Setup network and oracle
        chain = setup_fork_network(web3)

        # ========================================================================
        # STEP 2: Setup Wallet and CCXT Exchange
        # ========================================================================
        console.print("\n[bold]Setting up wallet and CCXT exchange...[/bold]")
        wallet = HotWallet.from_private_key(private_key)
        wallet.sync_nonce(web3)
        wallet_address = wallet.get_main_address()
        console.print(f"  Wallet: {wallet_address}")

        # Get token addresses
        tokens = {
            "WETH": get_token_address_normalized(chain, "WETH"),
            "USDC": get_token_address_normalized(chain, "USDC"),
        }
        for symbol, address in tokens.items():
            console.print(f"  {symbol}: {address}")

        # Initialize CCXT GMX exchange
        gmx = GMX(
            params={
                "rpcUrl": web3.provider.endpoint_uri if hasattr(web3.provider, "endpoint_uri") else None,
                "wallet": wallet,
            }
        )
        console.print("  [green]CCXT GMX exchange initialized[/green]")

        # ========================================================================
        # STEP 3: Fund Wallet
        # ========================================================================
        if is_tenderly:
            fund_wallet_tenderly(web3, wallet_address, tokens)
        else:
            fund_wallet_anvil(web3, wallet_address, tokens)

        # ========================================================================
        # STEP 4: Run Tests
        # ========================================================================
        symbol = "ETH/USDC:USDC"
        leverage = 2.5
        size_usd = args.size

        console.print(f"\n[bold]Test Configuration:[/bold]")
        console.print(f"  Symbol: {symbol}")
        console.print(f"  Size: ${size_usd} at {leverage}x leverage")
        console.print(f"  Collateral: USDC")
        console.print(f"  Execution Buffer: {EXECUTION_BUFFER}x")

        results = []

        if args.test_mode in ["unified", "both"]:
            passed = test_unified_style(gmx, symbol, size_usd, leverage, web3)
            results.append(("Unified Style (stopLossPrice/takeProfitPrice)", passed))

        if args.test_mode in ["object", "both"]:
            passed = test_object_style(gmx, symbol, size_usd, leverage, web3)
            results.append(("Object Style (stopLoss/takeProfit objects)", passed))

        # ========================================================================
        # STEP 5: Summary
        # ========================================================================
        console.print("\n" + "=" * 80)
        console.print("[bold]Test Summary[/bold]")
        console.print("=" * 80)

        for test_name, passed in results:
            status = "[green][PASS][/green]" if passed else "[red][FAIL][/red]"
            console.print(f"  {test_name}: {status}")

        all_passed = all(result[1] for result in results)

        if all_passed:
            console.print("\n[bold green]✓ All tests PASSED[/bold green]")
            return 0
        else:
            console.print("\n[bold red]✗ Some tests FAILED[/bold red]")
            return 1

    except Exception as e:
        console.print(f"\n[red]Error: {str(e)}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    finally:
        if launch:
            console.print("\n[dim]Shutting down Anvil...[/dim]")
            launch.close()


if __name__ == "__main__":
    exit(main())
