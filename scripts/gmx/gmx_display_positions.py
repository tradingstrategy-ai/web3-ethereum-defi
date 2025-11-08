"""
GMX Position Display Script

This script displays all open positions for a wallet address on GMX protocol
using the eth_defi SDK. It provides a formatted table view with position details
including size, entry price, mark price, and profit/loss.

Usage
-----

Run the script and enter a wallet address when prompted::

    python scripts/gmx/gmx_display_positions.py

Or set the RPC URL for a specific network::

    export ARBITRUM_RPC_URL="https://arb1.arbitrum.io/rpc"
    python scripts/gmx/gmx_display_positions.py

Environment Variables
---------------------

- ``ARBITRUM_RPC_URL``: Arbitrum mainnet RPC endpoint (default: public RPC)
- ``ARBITRUM_SEPOLIA_RPC_URL``: Arbitrum Sepolia testnet RPC endpoint
- ``AVALANCHE_RPC_URL``: Avalanche C-Chain mainnet RPC endpoint

Example
-------

Display positions for a wallet on Arbitrum mainnet::

    python scripts/gmx/gmx_display_positions.py
    Enter the address: 0x60fe5Cbd886A778f584FFCC63833B068104D1f77

Notes
-----

- The script works in read-only mode (no wallet required)
- Position data includes real-time oracle prices
- Profit/loss is calculated based on current mark prices
- Both long and short positions are displayed

See Also
--------

- :mod:`eth_defi.gmx.core.open_positions` - Position fetching module
- :mod:`eth_defi.gmx.config` - GMX configuration

"""

import os
from rich.console import Console
from rich.table import Table
from web3 import Web3

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.chain import get_chain_name

console = Console()


def get_positions(config: GMXConfig, address: str) -> dict:
    """Fetch all open positions for a given wallet address.

    :param config: GMX configuration object
    :param address: Wallet address to query
    :return: Dictionary of open positions keyed by market symbol and direction
    """
    positions_fetcher = GetOpenPositions(config)
    return positions_fetcher.get_data(address)


def calculate_profit_usd(position: dict) -> float:
    """Calculate absolute profit/loss in USD.

    :param position: Position dictionary containing size and profit percentage
    :return: Profit/loss amount in USD
    """
    return position["position_size"] * (position["percent_profit"] / 100)


def display_positions(positions: dict):
    """Display positions in a formatted rich table.

    :param positions: Dictionary of positions from get_positions()
    """
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Market")
    table.add_column("Type", justify="center")
    table.add_column("Collateral", justify="center")
    table.add_column("Size (USD)", justify="right")
    table.add_column("Leverage", justify="right")
    table.add_column("Entry Price", justify="right")
    table.add_column("Mark Price", justify="right")
    table.add_column("Profit (%)", justify="right")
    table.add_column("Profit (USD)", justify="right")

    for position_key, data in positions.items():
        profit_usd = calculate_profit_usd(data)

        # Color code profit/loss
        pnl_pct_color = "green" if data["percent_profit"] > 0 else "red"
        pnl_usd_color = "green" if profit_usd > 0 else "red"

        table.add_row(
            data["market_symbol"],
            "LONG" if data["is_long"] else "SHORT",
            data["collateral_token"],
            f"${data['position_size']:.2f}",
            f"{data['leverage']:.2f}x",
            f"${data['entry_price']:.4f}",
            f"${data['mark_price']:.4f}",
            f"[{pnl_pct_color}]{data['percent_profit']:.2f}%[/{pnl_pct_color}]",
            f"[{pnl_usd_color}]${profit_usd:.2f}[/{pnl_usd_color}]"
        )

    console.print(table)


def main():
    """Main entry point for the position display script."""
    # Try to get RPC URL from environment, fall back to public RPC
    rpc_url = (
        os.environ.get("ARBITRUM_RPC_URL")
        or os.environ.get("ARBITRUM_SEPOLIA_RPC_URL")
        or os.environ.get("AVALANCHE_RPC_URL")
        or "https://arb1.arbitrum.io/rpc"  # Default to Arbitrum mainnet public RPC
    )

    console.print(f"[cyan]Connecting to RPC: {rpc_url}[/cyan]")

    # Create web3 provider
    web3 = create_multi_provider_web3(rpc_url)

    # Verify connection
    try:
        block_number = web3.eth.block_number
        chain_name = get_chain_name(web3.eth.chain_id)
        console.print(f"[green]Connected to {chain_name}[/green]")
        console.print(f"Current block: {block_number}\n")
    except Exception as e:
        console.print(f"[red]Failed to connect to RPC: {e}[/red]")
        return

    # Create GMX config (read-only mode, no wallet needed)
    config = GMXConfig(web3)

    # Get wallet address from user
    address = input("Enter the wallet address: ")

    console.print(f"\n[cyan]Fetching open positions for: {address}[/cyan]\n")

    try:
        positions = get_positions(config, address)

        if positions:
            display_positions(positions)

            # Display summary
            total_size = sum(p["position_size"] for p in positions.values())
            total_profit_usd = sum(calculate_profit_usd(p) for p in positions.values())

            console.print(f"\n[bold]Summary:[/bold]")
            console.print(f"Total positions: {len(positions)}")
            console.print(f"Total position size: ${total_size:.2f}")

            profit_color = "green" if total_profit_usd > 0 else "red"
            console.print(f"Total P&L: [{profit_color}]${total_profit_usd:.2f}[/{profit_color}]")
        else:
            console.print("[yellow]No open positions found.[/yellow]")

    except Exception as e:
        console.print(f"[red]Error fetching positions: {e}[/red]")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
