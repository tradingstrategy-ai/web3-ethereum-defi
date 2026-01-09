"""
GMX CCXT-Style Order Creation Example

This script demonstrates how to create orders on GMX using the new CCXT-compatible
initialization pattern with parameters dictionary.

The script performs the following operations:

1. Initializes GMX with CCXT-style parameters (rpcUrl + privateKey)
2. Loads available markets
3. Creates a market buy order (opens long position)
4. Executes the order as keeper
5. Verifies the position was opened
6. Creates a market sell order (closes the position)
7. Executes the sell order
8. Verifies the position was closed

Usage
-----

Basic usage with environment variables::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_SEPOLIA_RPC_URL="https://arbitrum-sepolia.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_ccxt_style_order_creation.py

For Arbitrum mainnet::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_RPC_URL="https://arbitrum-mainnet.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_ccxt_style_order_creation.py

Environment Variables
---------------------

The script requires the following environment variables:

- ``PRIVATE_KEY``: Your wallet's private key with 0x prefix (required)
- ``ARBITRUM_SEPOLIA_RPC_URL``: Arbitrum Sepolia testnet RPC endpoint
- ``ARBITRUM_RPC_URL``: Arbitrum mainnet RPC endpoint

Example
-------

Open and close a $10 USD position on ETH market::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_SEPOLIA_RPC_URL="https://arbitrum-sepolia.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_ccxt_style_order_creation.py

Notes
-----

- Uses CCXT-compatible initialization pattern
- Chain is auto-detected from RPC URL
- Orders are automatically signed and broadcast
- Ensure your wallet has sufficient ETH for gas fees
- Ensure your wallet has sufficient collateral tokens

See Also
--------

- :mod:`eth_defi.gmx.ccxt.exchange` - GMX CCXT adapter
- :mod:`eth_defi.gmx.trading` - GMX trading module
"""

import os
import sys

from rich.console import Console
from rich.table import Table

from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.trace import assert_transaction_success_with_explanation

console = Console()


def main():
    """Main execution function for CCXT-style order creation."""
    # Get environment variables
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM") or os.environ.get("ARBITRUM_SEPOLIA_RPC_URL")
    private_key = os.environ.get("PRIVATE_KEY")

    if not rpc_url:
        console.print("[red]Error: No RPC URL provided[/red]")
        console.print("Set ARBITRUM_SEPOLIA_RPC_URL or JSON_RPC_ARBITRUM= environment variable")
        sys.exit(1)

    if not private_key:
        console.print("[red]Error: No private key provided[/red]")
        console.print("Set PRIVATE_KEY environment variable")
        sys.exit(1)

    console.print("[bold]GMX CCXT-Style Order Creation Example[/bold]")
    console.print(f"RPC URL: {rpc_url}")

    # Initialize GMX with CCXT-style parameters
    console.print("\n[blue]Step 1: Initializing GMX with CCXT-style parameters...[/blue]")
    gmx = GMX(
        {
            "rpcUrl": rpc_url,
            "privateKey": private_key,
            "verbose": False,  # Set to True for debug logging
        }
    )

    console.print(f"[green]GMX initialized successfully[/green]")
    console.print(f"  Wallet address: {gmx.wallet.address}")
    console.print(f"  Chain ID: {gmx.web3.eth.chain_id}")

    # Load markets
    console.print("\n[blue]Step 2: Loading available markets...[/blue]")
    gmx.load_markets()
    console.print(f"[green]Loaded {len(gmx.markets)} markets[/green]")

    # Display available markets
    market_table = Table(title="Available Markets")
    market_table.add_column("Symbol", style="cyan")
    market_table.add_column("Type", style="magenta")
    market_table.add_column("Base", style="green")
    market_table.add_column("Quote", style="yellow")

    for symbol in list(gmx.markets.keys())[:5]:  # Show first 5 markets
        market = gmx.markets[symbol]
        market_table.add_row(
            symbol,
            market.get("type", "N/A"),
            market.get("base", "N/A"),
            market.get("quote", "N/A"),
        )

    console.print(market_table)

    # Create market buy order (open long position)
    console.print("\n[blue]Step 3: Creating market buy order (long position)...[/blue]")
    symbol = "ETH/USDC:USDC"
    position_size = 10.0  # $10 USD

    try:
        buy_order = gmx.create_market_buy_order(
            symbol,
            position_size,
            {
                "leverage": 2.5,
                "collateral_symbol": "ETH",
                "slippage_percent": 0.005,
                "execution_buffer": 2.2,
            },
        )

        console.print(f"[green]Buy order created successfully[/green]")
        console.print(f"  Order ID: {buy_order['id']}")
        console.print(f"  Status: {buy_order['status']}")
        console.print(f"  Symbol: {buy_order['symbol']}")
        console.print(f"  Side: {buy_order['side']}")
        console.print(f"  Amount: ${buy_order['amount']}")
        console.print(f"  Fee: ${buy_order['fee']['cost']:.4f}")
        console.print(f"  TX Hash: {buy_order['info']['tx_hash']}")

        # Verify transaction success
        if buy_order.get("id"):
            assert_transaction_success_with_explanation(gmx.web3, buy_order["id"])
            console.print("[green]Transaction verified successfully[/green]")

    except Exception as e:
        console.print(f"[red]Failed to create buy order: {e}[/red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    # Note: In a real scenario, you would need to execute the order as a keeper
    # For this example, we assume the order gets executed

    console.print("\n[yellow]Note: Order created on-chain. In production, a keeper would execute it.[/yellow]")
    console.print("[yellow]For testing, use the fork_helpers to execute orders as keeper.[/yellow]")

    # Check open positions
    console.print("\n[blue]Step 4: Checking open positions...[/blue]")
    try:
        positions_manager = GetOpenPositions(gmx.config)
        open_positions = positions_manager.get_data(gmx.wallet.address)

        if open_positions:
            position_table = Table(title="Open Positions")
            position_table.add_column("Market", style="cyan")
            position_table.add_column("Type", style="magenta")
            position_table.add_column("Size (USD)", style="green")
            position_table.add_column("Collateral", style="yellow")
            position_table.add_column("Leverage", style="red")

            for position_key, position_data in open_positions.items():
                position_table.add_row(
                    position_data.get("market_symbol", "N/A"),
                    "LONG" if position_data.get("is_long") else "SHORT",
                    f"${position_data.get('position_size', 0):.2f}",
                    position_data.get("collateral_token", "N/A"),
                    f"{position_data.get('leverage', 0):.2f}x",
                )

            console.print(position_table)
        else:
            console.print("[yellow]No open positions found (order may not be executed yet)[/yellow]")

    except Exception as e:
        console.print(f"[yellow]Could not fetch positions: {e}[/yellow]")

    # Example: Close position with market sell order
    console.print("\n[blue]Step 5: Example - Closing position with market sell order...[/blue]")
    console.print("[yellow]Skipping for this example - uncomment code below to close position[/yellow]")

    """
    # Uncomment to close the position:
    try:
        sell_order = gmx.create_market_sell_order(
            symbol,
            position_size,  # Close same size
            {
                "collateral_symbol": "ETH",
                "slippage_percent": 0.005,
                "execution_buffer": 2.2,
            },
        )

        console.print(f"[green]Sell order created successfully[/green]")
        console.print(f"  Order ID: {sell_order['id']}")
        console.print(f"  Status: {sell_order['status']}")
        console.print(f"  TX Hash: {sell_order['info']['tx_hash']}")

    except Exception as e:
        console.print(f"[red]Failed to create sell order: {e}[/red]")
    """

    console.print("\n[bold green]CCXT-Style Order Creation Example Completed![/bold green]")


if __name__ == "__main__":
    main()
