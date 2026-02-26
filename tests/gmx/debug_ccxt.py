"""GMX CCXT Order Creation Test - Fork Testing

Creates and executes GMX orders using CCXT interface on forked networks for testing.

MODES:
1. Anvil fork (default):   python tests/gmx/debug_ccxt.py --fork
2. Tenderly fork:          python tests/gmx/debug_ccxt.py --td
3. Custom Anvil RPC:       python tests/gmx/debug_ccxt.py --anvil-rpc http://localhost:8545

Required environment variables:
- PRIVATE_KEY: Private key for signing transactions
- ARBITRUM_CHAIN_JSON_RPC: RPC endpoint for Anvil fork
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
    console.print("[dim]Mock oracle configured with on-chain prices[/dim]\n")

    return chain


def fund_wallet_anvil(web3: Web3, wallet_address: str, tokens: dict):
    """Fund wallet on Anvil fork using anvil_setBalance and whale transfers."""
    console.print("\n[bold]Funding wallet (Anvil mode)...[/bold]")

    # Set ETH balance for wallet
    eth_amount_wei = 100_000 * 10**18
    web3.provider.make_request("anvil_setBalance", [wallet_address, hex(eth_amount_wei)])

    # Verify balance was actually set
    actual_balance = web3.eth.get_balance(wallet_address)
    if actual_balance < eth_amount_wei * 0.99:  # Allow 1% tolerance
        raise RuntimeError(f"anvil_setBalance failed: expected {eth_amount_wei}, got {actual_balance}")
    console.print(f"  [green]ETH balance: {actual_balance / 10**18:,.2f} ETH (verified)[/green]")

    # Give whales some ETH for gas
    gas_eth = 100_000 * 10**18
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
    eth_amount_wei = 100_000 * 10**18
    web3.provider.make_request("tenderly_setBalance", [wallet_address, hex(eth_amount_wei)])
    console.print("  [green]ETH balance: 100,000 ETH[/green]")

    # Set USDC balance
    usdc_address = tokens.get("USDC")
    if usdc_address:
        usdc_amount = 100_000 * (10**6)
        web3.provider.make_request("tenderly_setErc20Balance", [usdc_address, wallet_address, hex(usdc_amount)])
        console.print("  [green]USDC balance: 100,000 USDC[/green]")

    # Set WETH balance
    weth_address = tokens.get("WETH")
    if weth_address:
        weth_amount = 100_000 * (10**18)
        web3.provider.make_request("tenderly_setErc20Balance", [weth_address, wallet_address, hex(weth_amount)])
        console.print("  [green]WETH balance: 100,000 WETH[/green]")


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="GMX CCXT Order Creation Test - Fork Testing",
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
        console.print("\n[bold green]=== GMX CCXT Fork Test ===[/bold green]\n")

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

            # Fork at latest block (old FORK_BLOCK was causing issues with GMX market data)
            launch = fork_network_anvil(
                fork_rpc,
                unlocked_addresses=[LARGE_USDC_HOLDER, LARGE_WETH_HOLDER],
                # Use latest block - older blocks may have stale GMX market data
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
        # CCXT-style initialization with wallet
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
        # STEP 4: Load Markets (with REST API -> RPC fallback)
        # ========================================================================
        console.print("\n[bold]Loading GMX markets...[/bold]")

        # Try REST API first (fast), fallback to RPC (slow but reliable) if API is down
        gmx.load_markets()
        if not gmx.markets:
            console.print("  [yellow]REST API failed, falling back to RPC mode (this may take 1-2 minutes)...[/yellow]")
            gmx.markets_loaded = False  # Reset to allow reload
            gmx.load_markets(params={"rest_api_mode": False})

        if gmx.markets:
            console.print(f"  [green]Loaded {len(gmx.markets)} markets[/green]")
        else:
            console.print("  [red]Failed to load markets from both REST API and RPC[/red]")
            raise RuntimeError("Could not load GMX markets - both REST API and RPC failed")

        # ========================================================================
        # STEP 5: Create and Submit GMX Order via CCXT
        # ========================================================================
        console.print("\n[bold]Creating GMX order via CCXT...[/bold]")

        symbol = "ETH/USDC:USDC"
        leverage = 2.5
        size_usd = args.size

        console.print(f"  Symbol: {symbol}")
        console.print(f"  Size: ${size_usd} at {leverage}x leverage")
        console.print("  Side: buy (LONG)")
        console.print("  Collateral: ETH")

        # Create order using CCXT interface
        # Note: Use size_usd param for direct USD sizing (amount is in base currency by CCXT standard)
        order = gmx.create_order(
            symbol=symbol,
            type="market",
            side="buy",
            amount=0,  # Not used when size_usd is provided
            params={
                "size_usd": size_usd,  # Direct USD position size
                "leverage": leverage,
                "collateral_symbol": "ETH",
                "slippage_percent": 0.005,
                "execution_buffer": EXECUTION_BUFFER,
            },
        )

        console.print("\n[green]Order created via CCXT[/green]")
        console.print(f"  Order ID: {order.get('id')}")
        console.print(f"  Status: {order.get('status')}")
        console.print(f"  Symbol: {order.get('symbol')}")
        console.print(f"  Side: {order.get('side')}")
        console.print(f"  Amount: {order.get('amount')}")

        # Get transaction hash from order info
        tx_hash = order.get("info", {}).get("tx_hash")
        if not tx_hash:
            # Try to get from order id (which is the tx hash)
            tx_hash = order.get("id")

        if tx_hash:
            console.print(f"  TX Hash: {tx_hash}")

            # Wait for transaction receipt
            if isinstance(tx_hash, str):
                tx_hash_bytes = bytes.fromhex(tx_hash[2:]) if tx_hash.startswith("0x") else bytes.fromhex(tx_hash)
            else:
                tx_hash_bytes = tx_hash

            receipt = web3.eth.wait_for_transaction_receipt(tx_hash_bytes)

            if receipt["status"] == 1:
                console.print("[green]Order submitted[/green]")
                console.print(f"  Block: {receipt['blockNumber']}")
                console.print(f"  Gas used: {receipt['gasUsed']}")

                # ========================================================================
                # STEP 5: Execute Order as Keeper
                # ========================================================================
                order_key = None
                try:
                    order_key = extract_order_key_from_receipt(receipt)
                    console.print(f"\n[green]Order Key: {order_key.hex()}[/green]")
                except Exception as e:
                    console.print(f"\n[yellow]Could not extract order key: {e}[/yellow]")

                if order_key:
                    console.print("\n[bold]Executing order as keeper...[/bold]")
                    try:
                        exec_receipt, keeper_address = execute_order_as_keeper(web3, order_key)

                        console.print("[green]Order executed[/green]")
                        console.print(f"  Keeper: {keeper_address}")
                        console.print(f"  Block: {exec_receipt['blockNumber']}")
                        console.print(f"  Gas used: {exec_receipt['gasUsed']}")

                    except Exception as e:
                        console.print(f"[red]Keeper execution failed: {e}[/red]")

                # ========================================================================
                # STEP 6: Verify Position via CCXT
                # ========================================================================
                console.print("\n[bold]Verifying position via CCXT...[/bold]")
                time.sleep(2)  # Brief wait for state to settle

                positions = gmx.fetch_positions([symbol])

                if positions:
                    console.print(f"[green]Found {len(positions)} position(s)[/green]\n")

                    for idx, position in enumerate(positions, 1):
                        market_symbol = position.get("symbol", "Unknown")
                        side = position.get("side", "unknown")
                        contracts = position.get("contracts", 0)
                        notional = position.get("notional", 0)
                        entry_price = position.get("entryPrice", 0)
                        mark_price = position.get("markPrice", 0)
                        leverage_pos = position.get("leverage", 0)
                        percentage = position.get("percentage", 0)
                        liquidation_price = position.get("liquidationPrice", 0)

                        console.print(f"  Position #{idx}")
                        console.print(f"    Market:           {market_symbol}")
                        console.print(f"    Side:             {side}")
                        console.print(f"    Contracts:        {contracts}")
                        console.print(f"    Notional:         ${notional:,.2f}")
                        console.print(f"    Leverage:         {leverage_pos:.2f}x")
                        console.print(f"    Entry Price:      ${entry_price:,.2f}")
                        console.print(f"    Mark Price:       ${mark_price:,.2f}")

                        if liquidation_price:
                            console.print(f"    Liquidation:      ${liquidation_price:,.2f}")

                        if percentage != 0:
                            pnl_color = "green" if percentage > 0 else "red"
                            console.print(f"    PnL:              [{pnl_color}]{percentage:+.2f}%[/{pnl_color}]")

                    # ========================================================================
                    # STEP 7: Close Position via CCXT (using first position found)
                    # ========================================================================
                    console.print("\n[bold]Closing position via CCXT...[/bold]")

                    first_position = positions[0]
                    position_symbol = first_position.get("symbol")
                    position_side = first_position.get("side")
                    position_size = first_position.get("notional", 0)

                    console.print(f"  Closing {position_symbol} {position_side}")
                    console.print(f"  Position size: ${position_size:.2f}")

                    # Fetch current on-chain price and adjust for profit scenario
                    console.print("\n[dim]Fetching current on-chain prices for close position...[/dim]")
                    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(web3)
                    # For long positions: price goes UP (+1000) to create profit
                    # For short positions: price goes DOWN (-1000) to create profit
                    is_long = position_side == "long"
                    new_eth_price = current_eth_price + 1000 if is_long else current_eth_price - 1000

                    console.print(
                        f"[dim]Setting up mock oracle for closing position (ETH=${new_eth_price}, USDC=${current_usdc_price})...[/dim]",
                    )
                    setup_mock_oracle(
                        web3,
                        eth_price_usd=new_eth_price,
                        usdc_price_usd=current_usdc_price,
                    )
                    console.print("[dim]Mock oracle configured[/dim]\n")

                    # Re-fund wallet — execute_order_as_keeper zeroes the wallet's
                    # ETH balance on Anvil forks.  See execute_order_as_keeper docstring.
                    web3.provider.make_request("anvil_setBalance", [wallet_address, hex(100_000 * 10**18)])

                    try:
                        # Close position using CCXT (sell side closes long positions)
                        # reduceOnly=True is REQUIRED to close existing position vs open new short
                        close_order = gmx.create_order(
                            symbol=position_symbol,
                            type="market",
                            side="sell",
                            amount=0,  # Not used for close when size_usd provided
                            params={
                                "size_usd": position_size,  # USD size to close
                                "reduceOnly": True,  # CRITICAL: Close existing position, not open new short
                                "collateral_symbol": "ETH",
                                "slippage_percent": 0.005,
                                "execution_buffer": 2.0,  # Reduced from 15 (EXECUTION_BUFFER/2) - was causing excessive fees
                            },
                        )

                        console.print("\n[green]Close order created via CCXT[/green]")
                        console.print(f"  Order ID: {close_order.get('id')}")
                        console.print(f"  Status: {close_order.get('status')}")

                        # Get transaction hash from close order
                        close_tx_hash = close_order.get("info", {}).get("tx_hash") or close_order.get("id")

                        if close_tx_hash:
                            console.print(f"  TX Hash: {close_tx_hash}")

                            if isinstance(close_tx_hash, str):
                                close_tx_hash_bytes = bytes.fromhex(close_tx_hash[2:]) if close_tx_hash.startswith("0x") else bytes.fromhex(close_tx_hash)
                            else:
                                close_tx_hash_bytes = close_tx_hash

                            close_receipt = web3.eth.wait_for_transaction_receipt(close_tx_hash_bytes)

                            if close_receipt["status"] == 1:
                                console.print("[green]Close order submitted[/green]")
                                console.print(f"  Block: {close_receipt['blockNumber']}")
                                console.print(f"  Gas used: {close_receipt['gasUsed']}")

                                # Execute close order as keeper
                                close_order_key = extract_order_key_from_receipt(close_receipt)
                                console.print(f"\n[green]Close Order Key: {close_order_key.hex()}[/green]")

                                console.print("\n[bold]Executing close order as keeper...[/bold]")
                                close_exec_receipt, keeper_address = execute_order_as_keeper(web3, close_order_key)

                                console.print("[green]Close order executed[/green]")
                                console.print(f"  Keeper: {keeper_address}")
                                console.print(f"  Block: {close_exec_receipt['blockNumber']}")
                                console.print(f"  Gas used: {close_exec_receipt['gasUsed']}")

                                # Verify position was closed
                                console.print("\n[bold]Verifying position closure...[/bold]")
                                time.sleep(2)

                                final_positions = gmx.fetch_positions([symbol])
                                if len(final_positions) == 0:
                                    console.print("[green]Position successfully closed![/green]")
                                else:
                                    console.print(f"[yellow]Warning: {len(final_positions)} position(s) still open[/yellow]")
                                    for pos in final_positions:
                                        console.print(f"    {pos['symbol']} {pos['side']}: ${pos.get('notional', 0):.2f}")
                            else:
                                console.print("[red]Close order failed[/red]")

                    except Exception as e:
                        console.print(f"[red]Close position failed: {e}[/red]")
                        import traceback

                        traceback.print_exc()

                else:
                    console.print("[yellow]No positions found[/yellow]")

            else:
                console.print("\n[red]Order failed[/red]")
                try:
                    assert_transaction_success_with_explanation(web3, tx_hash_bytes)
                except Exception as e:
                    console.print(f"  Error: {str(e)}")
        else:
            console.print("[yellow]No transaction hash found in order[/yellow]")

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
