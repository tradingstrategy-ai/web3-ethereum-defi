"""
Fetch Available GMX Markets

This script demonstrates how to fetch and display available GMX markets
using the GMX trading interface.

The script shows:
- Available trading pairs
- Market details (base/quote tokens)
- Market type (swap/spot)
- Index token information

Usage
-----

Basic usage with environment variables::

    export JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc"
    python scripts/gmx/gmx_fetch_markets.py

For Arbitrum Sepolia testnet::

    export ARBITRUM_SEPOLIA_RPC_URL="https://arbitrum-sepolia.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_fetch_markets.py --testnet

Environment Variables
---------------------

The script requires one of the following environment variables:

- ``JSON_RPC_ARBITRUM``: Arbitrum mainnet RPC endpoint
- ``ARBITRUM_SEPOLIA_RPC_URL``: Arbitrum Sepolia testnet RPC endpoint

Example
-------

Fetch markets on Arbitrum mainnet::

    export JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc"
    python scripts/gmx/gmx_fetch_markets.py

Notes
-----

- No wallet/private key needed (read-only operation)
- Works with both mainnet and testnet
- Chain is auto-detected from RPC URL

See Also
--------

- :mod:`eth_defi.gmx.core.markets` - GMX markets module
- :mod:`eth_defi.gmx.config` - GMX configuration
"""

import os
import sys
import argparse
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from web3 import Web3, HTTPProvider

from eth_defi.chain import install_chain_middleware
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.markets import Markets

console = Console()


def main():
    """Main execution function for fetching GMX markets."""
    # Parse arguments
    parser = argparse.ArgumentParser(description="Fetch GMX markets")
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="Use Arbitrum Sepolia testnet instead of mainnet",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of markets to display (default: show all)",
    )
    args = parser.parse_args()

    # Get RPC URL
    if args.testnet:
        rpc_url = os.environ.get("ARBITRUM_SEPOLIA_RPC_URL")
        network_name = "Arbitrum Sepolia Testnet"
    else:
        rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
        network_name = "Arbitrum Mainnet"

    if not rpc_url:
        env_var = "ARBITRUM_SEPOLIA_RPC_URL" if args.testnet else "JSON_RPC_ARBITRUM"
        console.print(f"[red]Error: No RPC URL provided[/red]")
        console.print(f"Set {env_var} environment variable")
        sys.exit(1)

    console.print(Panel.fit(f"[bold cyan]GMX Markets Fetcher[/bold cyan]\nNetwork: {network_name}\nRPC: {rpc_url[:50]}...", border_style="cyan"))

    try:
        # Initialize Web3
        console.print("\n[blue]Connecting to blockchain...[/blue]")
        web3 = Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": 60}))
        install_chain_middleware(web3)

        if not web3.is_connected():
            console.print("[red]✗ Failed to connect to RPC[/red]")
            sys.exit(1)

        console.print(f"[green]✓[/green] Connected to chain ID: {web3.eth.chain_id}")

        # Initialize GMX config (no wallet needed for read-only)
        console.print("[blue]Initializing GMX configuration...[/blue]")
        config = GMXConfig(web3=web3)

        console.print(f"[green]✓[/green] Chain: {config.get_chain()}")

        # Fetch markets
        console.print("\n[blue]Fetching available markets...[/blue]")
        markets_fetcher = Markets(config)
        markets_data = markets_fetcher.get_available_markets()

        console.print(f"[green]✓[/green] Found {len(markets_data)} markets\n")

        # Prepare table
        table = Table(title=f"GMX Markets on {network_name}", show_header=True, header_style="bold magenta", border_style="blue")

        table.add_column("Symbol", style="cyan", width=12)
        table.add_column("Type", style="yellow", width=10)
        table.add_column("Index Token", style="green", width=15)
        table.add_column("Long Token", style="blue", width=15)
        table.add_column("Short Token", style="red", width=15)
        table.add_column("Market Address", style="dim", width=20)

        # Add markets to table
        markets_list = list(markets_data.items())
        if args.limit:
            markets_list = markets_list[: args.limit]

        for market_key, market_info in markets_list:
            # Extract token symbols from metadata
            market_symbol = market_info.get("market_symbol", "N/A")

            index_token = market_info.get("market_metadata", {}).get("symbol", "N/A")
            long_token = market_info.get("long_token_metadata", {}).get("symbol", "N/A")
            short_token = market_info.get("short_token_metadata", {}).get("symbol", "N/A")

            # Determine market type based on whether index token is synthetic
            is_synthetic = market_info.get("market_metadata", {}).get("synthetic", False)
            market_type = "swap" if is_synthetic else "spot"

            # Create trading symbol (e.g., BTC/USD)
            if market_symbol != "N/A" and short_token != "N/A":
                symbol = f"{market_symbol}/{short_token}"
            else:
                symbol = f"{long_token}/{short_token}"

            # Shorten address for display
            short_address = f"{market_key[:6]}...{market_key[-4:]}"

            table.add_row(symbol, market_type, index_token, long_token, short_token, short_address)

        console.print(table)

        # Summary statistics
        if args.limit and len(markets_data) > args.limit:
            console.print(f"\n[dim]Showing {args.limit} of {len(markets_data)} markets[/dim]")
            console.print(f"[dim]Use --limit to adjust or remove to show all[/dim]")

        # Market type breakdown
        console.print("\n[bold]Market Type Breakdown:[/bold]")
        swap_count = sum(1 for m in markets_data.values() if m.get("market_metadata", {}).get("synthetic", False))
        spot_count = len(markets_data) - swap_count

        console.print(f"  Spot markets: {spot_count}")
        console.print(f"  Swap markets: {swap_count}")

    except Exception as e:
        console.print(f"\n[red]✗[/red] Error: {e}")
        import traceback

        console.print("\n[dim]Full traceback:[/dim]")
        console.print(traceback.format_exc())
        sys.exit(1)

    console.print("\n[bold green]✓ Markets fetched successfully![/bold green]")


if __name__ == "__main__":
    main()
