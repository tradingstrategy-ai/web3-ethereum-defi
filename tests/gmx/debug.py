"""GMX Order Creation Test - Fork Testing

Creates and executes GMX orders on forked networks for testing.

MODES:
1. Anvil fork (default):   python tests/gmx/debug.py --fork
2. Tenderly fork:          python tests/gmx/debug.py --td
3. Custom Anvil RPC:       python tests/gmx/debug.py --anvil-rpc http://localhost:8545

Required environment variables:
- PRIVATE_KEY: Private key for signing transactions
- ARBITRUM_CHAIN_JSON_RPC: RPC endpoint for Anvil fork
- TD_ARB: Tenderly fork URL (for --td mode)
"""

import os
import sys
import argparse
import time

from eth_utils import to_checksum_address

from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.hotwallet import HotWallet
from eth_defi.gmx.contracts import (
    get_token_address_normalized,
)
from rich.console import Console
from web3 import Web3

from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from tests.gmx.fork_helpers import execute_order_as_keeper, setup_mock_oracle, extract_order_key_from_receipt
import logging
from rich.logging import RichHandler

# Configure logging to show detailed output from fork_helpers
FORMAT = "%(message)s"
logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])

logger = logging.getLogger("rich")

console = Console()

# Fork test configuration
FORK_BLOCK = 392496384
LARGE_USDC_HOLDER = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
LARGE_WETH_HOLDER = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")
MOCK_ETH_PRICE = 3450  # USD
MOCK_USDC_PRICE = 1  # USD


def setup_fork_network(web3: Web3):
    """Setup mock oracle and display network info."""
    block_number = web3.eth.block_number
    chain_id = web3.eth.chain_id
    chain = get_chain_name(chain_id).lower()

    console.print(f"  Block: {block_number}")
    console.print(f"  Chain ID: {chain_id}")
    console.print(f"  Chain: {chain}")

    # Setup mock oracle with fixed prices
    console.print("\n[dim]Setting up mock oracle...[/dim]")
    setup_mock_oracle(web3, eth_price_usd=MOCK_ETH_PRICE, usdc_price_usd=MOCK_USDC_PRICE)
    console.print(f"[dim]✓ Mock oracle configured (ETH=${MOCK_ETH_PRICE}, USDC=${MOCK_USDC_PRICE})[/dim]\n")

    return chain


def fund_wallet_anvil(web3: Web3, wallet_address: str, tokens: dict):
    """Fund wallet on Anvil fork using anvil_setBalance and whale transfers."""
    console.print("\n[bold]Funding wallet (Anvil mode)...[/bold]")

    # Set ETH balance
    eth_amount_wei = 100 * 10**18
    web3.provider.make_request("anvil_setBalance", [wallet_address, hex(eth_amount_wei)])
    console.print(f"  [green]✓ ETH balance: 100 ETH[/green]")

    # Transfer USDC from whale
    usdc_address = tokens.get("USDC")
    if usdc_address:
        usdc_amount = 100_000 * (10**6)
        usdc_token = fetch_erc20_details(web3, usdc_address)
        usdc_token.contract.functions.transfer(wallet_address, usdc_amount).transact({"from": LARGE_USDC_HOLDER})
        balance = usdc_token.contract.functions.balanceOf(wallet_address).call()
        console.print(f"  [green]✓ USDC balance: {balance / 10**6:.2f} USDC[/green]")

    # Transfer WETH from whale
    weth_address = tokens.get("WETH")
    if weth_address:
        weth_amount = 1000 * (10**18)
        weth_token = fetch_erc20_details(web3, weth_address)
        weth_token.contract.functions.transfer(wallet_address, weth_amount).transact({"from": LARGE_WETH_HOLDER})
        balance = weth_token.contract.functions.balanceOf(wallet_address).call()
        console.print(f"  [green]✓ WETH balance: {balance / 10**18:.2f} WETH[/green]")


def fund_wallet_tenderly(web3: Web3, wallet_address: str, tokens: dict):
    """Fund wallet on Tenderly fork using Tenderly RPC methods."""
    console.print("\n[bold]Funding wallet (Tenderly mode)...[/bold]")

    # Set ETH balance
    eth_amount_wei = 100 * 10**18
    web3.provider.make_request("tenderly_setBalance", [wallet_address, hex(eth_amount_wei)])
    console.print(f"  [green]✓ ETH balance: 100 ETH[/green]")

    # Set USDC balance
    usdc_address = tokens.get("USDC")
    if usdc_address:
        usdc_amount = 100_000 * (10**6)
        web3.provider.make_request("tenderly_setErc20Balance", [usdc_address, wallet_address, hex(usdc_amount)])
        console.print(f"  [green]✓ USDC balance: 100,000 USDC[/green]")

    # Set WETH balance
    weth_address = tokens.get("WETH")
    if weth_address:
        weth_amount = 1000 * (10**18)
        web3.provider.make_request("tenderly_setErc20Balance", [weth_address, wallet_address, hex(weth_amount)])
        console.print(f"  [green]✓ WETH balance: 1,000 WETH[/green]")


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="GMX Order Creation Test - Fork Testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Fork mode options (mutually exclusive)
    fork_group = parser.add_mutually_exclusive_group()
    fork_group.add_argument("--fork", action="store_true", help="Create Anvil fork (default)")
    fork_group.add_argument("--td", action="store_true", help="Use Tenderly fork (requires TD_ARB env var)")
    fork_group.add_argument("--anvil-rpc", type=str, help="Connect to existing Anvil RPC (e.g., http://127.0.0.1:8545)")

    # Position size override
    parser.add_argument("--size", type=float, default=10.0, help="Position size in USD (default: 10)")

    return parser.parse_args()


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
        console.print("\n[bold green]=== GMX Fork Test ===[/bold green]\n")

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
            # Anvil fork mode (default)
            fork_rpc = os.environ.get("ARBITRUM_CHAIN_JSON_RPC")
            if not fork_rpc:
                console.print("[red]Error: ARBITRUM_CHAIN_JSON_RPC environment variable not set[/red]")
                sys.exit(1)

            console.print(f"Creating Anvil fork at block {FORK_BLOCK}...")
            launch = fork_network_anvil(
                fork_rpc,
                unlocked_addresses=[LARGE_USDC_HOLDER, LARGE_WETH_HOLDER],
                fork_block_number=FORK_BLOCK,
            )

            web3 = create_multi_provider_web3(launch.json_rpc_url, default_http_timeout=(3.0, 180.0))
            console.print(f"  Anvil fork started on {launch.json_rpc_url}")

        # Setup network and oracle
        chain = setup_fork_network(web3)

        # ========================================================================
        # STEP 2: Setup Wallet
        # ========================================================================
        console.print("\n[bold]Setting up wallet...[/bold]")
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

        # ========================================================================
        # STEP 3: Fund Wallet
        # ========================================================================
        if is_tenderly:
            fund_wallet_tenderly(web3, wallet_address, tokens)
        else:
            fund_wallet_anvil(web3, wallet_address, tokens)

        # ========================================================================
        # STEP 4: Create and Submit GMX Order
        # ========================================================================
        console.print("\n[bold]Creating GMX order...[/bold]")

        market_symbol = "ETH"
        collateral_symbol = "ETH"
        start_token_symbol = "ETH"
        leverage = 2.5
        size_usd = args.size

        console.print(f"  Market: {market_symbol}")
        console.print(f"  Collateral: {collateral_symbol}")
        console.print(f"  Size: ${size_usd} at {leverage}x leverage")
        console.print(f"  Direction: LONG")

        config = GMXConfig(web3, user_wallet_address=wallet_address)
        trading_client = GMXTrading(config)

        order = trading_client.open_position(
            market_symbol=market_symbol,
            collateral_symbol=collateral_symbol,
            start_token_symbol=start_token_symbol,
            is_long=True,
            size_delta_usd=size_usd,
            leverage=leverage,
            slippage_percent=0.005,
            execution_buffer=2.2,
        )

        console.print(f"\n[green]✓ Order created[/green]")
        console.print(f"  Execution Fee: {order.execution_fee / 1e18:.6f} ETH")
        console.print(f"  Mark Price: {order.mark_price}")

        console.print("\n[bold]Submitting order...[/bold]")

        transaction = order.transaction
        if "nonce" in transaction:
            del transaction["nonce"]

        signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        console.print(f"  TX Hash: {tx_hash.hex()}")

        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

        if receipt["status"] == 1:
            console.print(f"[green]✓ Order submitted[/green]")
            console.print(f"  Block: {receipt['blockNumber']}")
            console.print(f"  Gas used: {receipt['gasUsed']}")

            # ========================================================================
            # STEP 5: Execute Order as Keeper
            # ========================================================================
            order_key = None
            try:
                order_key = extract_order_key_from_receipt(receipt)
                console.print(f"\n[green]✓ Order Key: {order_key.hex()}[/green]")
            except Exception as e:
                console.print(f"\n[yellow]⚠ Could not extract order key: {e}[/yellow]")

            if order_key:
                console.print("\n[bold]Executing order as keeper...[/bold]")
                try:
                    exec_receipt, keeper_address = execute_order_as_keeper(web3, order_key)

                    console.print(f"[green]✓ Order executed[/green]")
                    console.print(f"  Keeper: {keeper_address}")
                    console.print(f"  Block: {exec_receipt['blockNumber']}")
                    console.print(f"  Gas used: {exec_receipt['gasUsed']}")

                except Exception as e:
                    console.print(f"[red]✗ Keeper execution failed: {e}[/red]")

            # ========================================================================
            # STEP 6: Verify Position
            # ========================================================================
            console.print("\n[bold]Verifying position...[/bold]")
            time.sleep(2)  # Brief wait for state to settle

            position_verifier = GetOpenPositions(config)
            open_positions = position_verifier.get_data(wallet_address)

            if open_positions:
                console.print(f"[green]✓ Found {len(open_positions)} position(s)[/green]\n")

                for idx, (position_key, position) in enumerate(open_positions.items(), 1):
                    market_symbol = position.get("market_symbol", "Unknown")
                    is_long = position.get("is_long", False)
                    direction = "LONG 🟢" if is_long else "SHORT 🔴"
                    collateral_token = position.get("collateral_token", "Unknown")

                    position_size = position.get("position_size", 0)
                    size_in_tokens = position.get("size_in_tokens", 0) / 1e18
                    initial_collateral_amount = position.get("initial_collateral_amount", 0)
                    initial_collateral_amount_usd = position.get("initial_collateral_amount_usd", 0)
                    entry_price = position.get("entry_price", 0)
                    mark_price = position.get("mark_price", 0)
                    leverage = position.get("leverage", 0)
                    percent_profit = position.get("percent_profit", 0)

                    token_decimals = 18 if "ETH" in collateral_token.upper() else 6
                    collateral_amount = initial_collateral_amount / (10**token_decimals)

                    console.print(f"  Position #{idx}")
                    console.print(f"    Market:           {market_symbol}/USD")
                    console.print(f"    Direction:        {direction}")
                    console.print(f"    Collateral Token: {collateral_token}")
                    console.print(f"    Position Size:    ${position_size:,.2f}")
                    console.print(f"    Collateral:       {collateral_amount:.6f} {collateral_token}")
                    console.print(f"    Collateral Value: ${initial_collateral_amount_usd:,.2f}")
                    console.print(f"    Leverage:         {leverage:.2f}x")
                    console.print(f"    Entry Price:      ${entry_price:,.2f}")
                    console.print(f"    Mark Price:       ${mark_price:,.2f}")

                    if percent_profit != 0:
                        pnl_color = "green" if percent_profit > 0 else "red"
                        console.print(f"    PnL:              [{pnl_color}]{percent_profit:+.2f}%[/{pnl_color}]")
            else:
                console.print(f"[yellow]⚠ No positions found[/yellow]")

        else:
            console.print(f"\n[red]✗ Order failed[/red]")
            try:
                assert_transaction_success_with_explanation(web3, tx_hash)
            except Exception as e:
                console.print(f"  Error: {str(e)}")

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
    main()
